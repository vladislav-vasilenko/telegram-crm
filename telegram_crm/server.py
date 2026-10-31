import asyncio
import csv
import io
import os
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from telethon import errors

from .analysis import CATEGORIES, classify
from .demo import load_demo
from .llm import analyze_batch, create_batch, import_result
from .models import Annotation, AuthApp, AuthCode, AuthPassword, AuthStart, BatchRequest, ImportRequest, LLMKey, ScanRequest, Settings
from .store import Store, utcnow
from .telegram import TelegramService

STATIC = Path(__file__).parent / "static"


def create_app(data_dir: Path, *, demo=False, testing=False):
    store = Store(data_dir)
    if not store.metadata("settings") and os.environ.get("TG_LLM_MODEL"):
        store.save_settings(Settings(llm_model=os.environ["TG_LLM_MODEL"]))
    telegram = TelegramService(store)
    token = secrets.token_urlsafe(32)
    jobs = {"llm": {"running": False, "done": 0, "total": 0, "error": None}}
    llm_task = None

    def api_key():
        return os.environ.get("OPENAI_API_KEY") or store.credentials().get("openai_api_key")

    def analyzed_rows():
        settings = store.settings()
        return sorted([classify(**row, settings=settings) for row in store.all()],
                      key=lambda r: (-r["priority_score"], r["title"].lower()))

    def remaining(rows=None):
        return [row["id"] for row in (rows if rows is not None else analyzed_rows()) if "important" not in row["categories"]
                and (not row["llm"] or row["llm_stale"]) and row["loaded_messages"] and not row["demo"]]

    def start_llm(request):
        nonlocal llm_task
        if jobs["llm"]["running"]:
            raise ValueError("Анализ LLM уже выполняется")
        key = api_key()
        if not key:
            raise ValueError("Добавьте API-ключ в настройках или используйте копирование пачки")
        for chat_id in request.chat_ids:
            if not store.get(chat_id):
                raise ValueError("Один из выбранных чатов не найден")
        jobs["llm"] = {"running": True, "done": 0, "total": len(request.chat_ids), "error": None}

        async def run():
            try:
                await analyze_batch(store, request, key, store.settings().llm_model,
                                    lambda done, total: jobs["llm"].update(done=done, total=total))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Provider errors may echo a key or message text. Never return their body.
                jobs["llm"]["error"] = f"Анализ остановлен ({type(error).__name__}). Проверьте ключ, модель и доступ к API. Завершённые части сохранены."
            finally:
                jobs["llm"]["running"] = False

        llm_task = asyncio.create_task(run())

    async def scheduler():
        await telegram.restore()
        next_attempt = utcnow()
        while True:
            await asyncio.sleep(10)
            settings = store.settings()
            if telegram.status["running"] or not telegram.authorized:
                continue
            last_scan = store.metadata("last_scan_at")
            due = not last_scan or utcnow() - datetime.fromisoformat(last_scan) >= timedelta(hours=settings.scan_interval_hours)
            if settings.scan_interval_hours and due and utcnow() >= next_attempt:
                try:
                    telegram.start_scan()
                    await telegram.task
                except (ValueError, OSError):
                    pass
                next_attempt = utcnow() + timedelta(minutes=15)
            successful_scan = store.metadata("last_scan_at")
            if (settings.llm_after_scan and successful_scan and not telegram.status.get("error")
                    and successful_scan != store.metadata("last_auto_llm_scan")
                    and api_key() and not jobs["llm"]["running"]):
                ids = remaining()[:settings.llm_max_chats]
                store.set_metadata("last_auto_llm_scan", successful_scan)
                if ids:
                    start_llm(BatchRequest(chat_ids=ids, redact_contacts=settings.redact_contacts))

    @asynccontextmanager
    async def lifespan(app):
        if demo:
            load_demo(store)
        task = asyncio.create_task(scheduler()) if not testing else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if llm_task and not llm_task.done():
            llm_task.cancel()
            with suppress(asyncio.CancelledError):
                await llm_task
        await telegram.close()
        store.close()

    app = FastAPI(title="Диалоги / Telegram CRM", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.telegram = store, telegram

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        allowed = {"127.0.0.1", "localhost"} | ({"testserver"} if testing else set())
        if request.url.hostname not in allowed:
            return JSONResponse({"detail": "Только локальный доступ"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-crm-token") != token:
                return JSONResponse({"detail": "Обновите страницу: неверный токен сессии"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Запрос с другого сайта запрещён"}, status_code=403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Неверная длина запроса"}, status_code=400)
            if size > 2_000_000:
                return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in error.errors())}, status_code=422)

    @app.exception_handler(errors.RPCError)
    async def telegram_error(request, error):
        known = {"PhoneCodeInvalidError": "Неверный код входа", "PhoneCodeExpiredError": "Код истёк. Запросите новый.",
                 "PasswordHashInvalidError": "Неверный пароль двухэтапной проверки", "ApiIdInvalidError": "Неверные api_id / api_hash",
                 "PhoneNumberInvalidError": "Неверный номер телефона"}
        if isinstance(error, errors.FloodWaitError):
            message = f"Telegram запросил паузу {error.seconds} сек. Попробуйте позже."
        else:
            message = known.get(type(error).__name__, f"Ошибка Telegram: {type(error).__name__}")
        return JSONResponse({"detail": message}, status_code=429 if isinstance(error, errors.FloodWaitError) else 400)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    async def health():
        return {"application": "telegram-dialogue-crm", "version": "0.1.0"}

    @app.get("/api/state")
    async def state():
        rows = analyzed_rows()
        return {"token": token, "chats": rows,
                "counts": {key: sum(key in row["categories"] for row in rows) for key in CATEGORIES},
                "categories": CATEGORIES, "settings": store.settings().model_dump(),
                "last_scan_at": store.metadata("last_scan_at"), "demo": bool(rows and all(row["demo"] for row in rows)),
                "telegram": {"authorized": telegram.authorized, "account": telegram.account, "step": telegram.auth_step,
                             "configured": bool(os.environ.get("TG_API_ID") or store.credentials().get("api_id")), "delivery": telegram.delivery},
                "scan": telegram.status, "llm_job": jobs["llm"], "llm_configured": bool(api_key()),
                "remaining_count": len(remaining(rows)), "now": utcnow().isoformat()}

    @app.get("/api/status")
    async def status():
        # Cheap progress poll: /api/state classifies every chat and runs on the same event loop as the scan.
        return {"scan": telegram.status, "llm_job": jobs["llm"]}

    @app.get("/api/chats/{chat_id}")
    async def chat(chat_id: str):
        row = store.get(chat_id)
        if not row:
            raise HTTPException(404, "Чат не найден")
        return {**classify(**row, settings=store.settings()),
                "messages": [m.model_dump(mode="json") for m in sorted(row["chat"].messages, key=lambda m: (m.date, m.id))]}

    @app.put("/api/chats/{chat_id}/annotation")
    async def annotate(chat_id: str, annotation: Annotation):
        if not store.get(chat_id):
            raise HTTPException(404, "Чат не найден")
        store.annotate(chat_id, annotation)
        return {"ok": True}

    @app.put("/api/settings")
    async def settings(settings: Settings):
        if settings.llm_after_scan and not api_key():
            raise ValueError("Для автоматического LLM-анализа сначала сохраните API-ключ")
        store.save_settings(settings)
        return {"ok": True}

    @app.put("/api/llm/key")
    async def save_key(key: LLMKey):
        store.save_credentials(openai_api_key=key.api_key.strip())
        return {"ok": True}

    @app.delete("/api/llm/key")
    async def delete_key():
        store.save_credentials(openai_api_key="")
        return {"ok": True, "environment_key_present": bool(os.environ.get("OPENAI_API_KEY"))}

    @app.post("/api/demo")
    async def demo_data():
        if telegram.status["running"]:
            raise ValueError("Дождитесь окончания сканирования")
        load_demo(store)
        return {"ok": True}

    @app.post("/api/auth/start")
    async def auth_start(request: AuthStart):
        return await telegram.begin_auth(request)

    @app.post("/api/auth/code")
    async def auth_code(request: AuthCode):
        return await telegram.submit_code(request.code)

    @app.post("/api/auth/password")
    async def auth_password(request: AuthPassword):
        return await telegram.submit_password(request.password)

    @app.post("/api/auth/resend")
    async def auth_resend():
        return await telegram.resend_code()

    @app.post("/api/auth/qr")
    async def auth_qr(request: AuthApp | None = None):
        return await telegram.begin_qr(request)

    @app.get("/api/auth/qr/status")
    async def qr_status(request: Request):
        if request.headers.get("x-crm-token") != token:
            raise HTTPException(403, "Неверный токен панели")
        return {"step": telegram.auth_step, "image": telegram.qr_image, "error": telegram.qr_error}

    @app.post("/api/auth/qr/cancel")
    async def cancel_qr():
        await telegram.cancel_qr()
        return {"ok": True}

    @app.post("/api/scan", status_code=202)
    async def scan(request: ScanRequest | None = None):
        telegram.start_scan(full=bool(request and request.full))
        return {"started": True}

    @app.post("/api/scan/cancel")
    async def cancel_scan():
        if telegram.task and not telegram.task.done():
            telegram.task.cancel()
        return {"ok": True}

    @app.post("/api/batches/export")
    async def export(request: BatchRequest):
        return create_batch(store, request)

    @app.post("/api/batches/import")
    async def import_batch(request: ImportRequest):
        return {"imported": import_result(store, request.batch_id, request.result)}

    @app.post("/api/llm/analyze", status_code=202)
    async def llm(request: BatchRequest):
        start_llm(request)
        return {"started": True}

    @app.get("/api/folders/plan")
    async def folder_plan():
        return {"folders": telegram.folder_plan()}

    @app.post("/api/folders/apply")
    async def folders():
        return await telegram.apply_folders()

    @app.get("/api/export.csv")
    async def export_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Чат", "Категории", "Последнее сообщение (UTC)", "Дней без общения", "Сообщений за 30 дней (в выборке)", "Причины"])
        def cell(value):
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for row in analyzed_rows():
            writer.writerow([cell(row["title"]), "; ".join(CATEGORIES[c] for c in row["categories"]),
                             row["last_message_at"], row["idle_days"], row["recent_messages"], cell("; ".join(row["reasons"]))])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="telegram-chats.csv"'})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

