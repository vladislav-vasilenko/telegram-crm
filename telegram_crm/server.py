import asyncio
import csv
import io
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from telethon import errors

from .analysis import CATEGORIES, classify
from .demo import load_demo
from .chat_export import full_history, recent_text
from .companies import apply_companies, folder_index
from .i18n import translate
from .layout import fetch_layout
from .romance import METADATA_KEY as ROMANCE_KEY, analyze_romance, apply_romance, estimate_romance, export_romance, import_romance
from .search import history_window, search_history
from .media import INLINE_TYPES, MediaService, attachment_header, describe, parse_range
from .llm import PartsFailed, analyze_batch, create_batch, estimate, import_result, provider_for
from .models import RomanceImport, Annotation, AuthApp, CleanupRequest, AuthCode, AuthPassword, AuthStart, BatchRequest, ImportRequest, LLMKey, ScanRequest, Settings
from .store import Store, utcnow
from .telegram import TelegramService
from .triage import apply_triage, triage_summary

STATIC = Path(__file__).parent / "static"


LAYOUT_FRESH_SECONDS = 30


def create_app(data_dir: Path, *, demo=False, testing=False):
    store = Store(data_dir)
    if not store.metadata("settings") and os.environ.get("TG_LLM_MODEL"):
        store.save_settings(Settings(llm_model=os.environ["TG_LLM_MODEL"]))
    telegram = TelegramService(store)
    media = MediaService(telegram, store)
    layout_lock = asyncio.Lock()
    token = secrets.token_urlsafe(32)
    jobs = {"llm": {"running": False, "done": 0, "total": 0, "error": None}}
    llm_task = None

    def api_key(provider=None):
        provider = provider or provider_for(store.settings().llm_model)
        if provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY") or store.credentials().get("anthropic_api_key")
        return os.environ.get("OPENAI_API_KEY") or store.credentials().get("openai_api_key")

    def analyzed_rows():
        settings = store.settings()
        saved, romance = store.metadata("triage", {}), store.metadata(ROMANCE_KEY, {})
        folders = folder_index(store.metadata("telegram_layout"))
        return sorted([apply_companies(apply_romance(apply_triage(classify(**row, settings=settings), row["chat"], row["annotation"], saved), row["chat"], row["annotation"], romance),
                                       row["chat"], row["annotation"], folders)
                       for row in store.all()],
                      key=lambda r: (-r["priority_score"], r["title"].lower()))

    def remaining(rows=None):
        return [row["id"] for row in (rows if rows is not None else analyzed_rows()) if "important" not in row["categories"]
                and (not row["llm"] or row["llm_stale"]) and row["loaded_messages"] and not row["demo"]]

    def start_llm(request, runner=analyze_batch):
        nonlocal llm_task
        if jobs["llm"]["running"]:
            raise ValueError("Анализ LLM уже выполняется")
        key = api_key()
        if not key:
            provider = "Anthropic" if provider_for(store.settings().llm_model) == "anthropic" else "OpenAI"
            raise ValueError(f"Добавьте API-ключ {provider} в настройках или используйте копирование пачки")
        for chat_id in request.chat_ids:
            if not store.get(chat_id):
                raise ValueError("Один из выбранных чатов не найден")
        jobs["llm"] = {"running": True, "done": 0, "total": len(request.chat_ids), "error": None}

        async def run():
            try:
                await runner(store, request, key, store.settings().llm_model,
                                    lambda done, total: jobs["llm"].update(done=done, total=total))
            except asyncio.CancelledError:
                raise
            except PartsFailed as error:
                jobs["llm"]["error"] = str(error)  # our own summary, no provider text
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
    app.state.media = media

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

    @app.get("/telegram")
    async def telegram_view():
        return FileResponse(STATIC / "telegram.html")

    @app.get("/api/health")
    async def health():
        return {"application": "telegram-dialogue-crm", "version": "0.1.0"}

    @app.get("/api/state")
    async def state():
        rows = analyzed_rows()
        return {"token": token, "chats": rows,
                "counts": {key: sum(key in row["categories"] for row in rows) for key in CATEGORIES},
                "categories": CATEGORIES, "triage": triage_summary(rows, store.metadata("triage")), "settings": store.settings().model_dump(),
                "last_scan_at": store.metadata("last_scan_at"), "demo": bool(rows and all(row["demo"] for row in rows)),
                "telegram": {"authorized": telegram.authorized, "account": telegram.account, "step": telegram.auth_step,
                             "configured": bool(os.environ.get("TG_API_ID") or store.credentials().get("api_id")), "delivery": telegram.delivery},
                "scan": telegram.status, "llm_job": jobs["llm"], "llm_configured": bool(api_key()),
                "llm_provider": provider_for(store.settings().llm_model),
                "llm_keys": {"openai": bool(api_key("openai")), "anthropic": bool(api_key("anthropic"))},
                "remaining_count": len(remaining(rows)), "now": utcnow().isoformat()}

    @app.get("/api/status")
    async def status():
        # Cheap progress poll: /api/state classifies every chat and runs on the same event loop as the scan.
        return {"scan": telegram.status, "llm_job": jobs["llm"]}

    @app.get("/api/search")
    async def search(q: str = ""):
        """Chats whose loaded messages contain q, with a snippet of the newest match."""
        query = q.strip()
        if len(query) < 2:
            return {"query": query, "hits": {}}
        pattern = re.compile(re.escape(query), re.I)
        hits = {}
        for row in store.all():
            for message in sorted(row["chat"].messages, key=lambda m: (m.date, m.id), reverse=True):
                match = pattern.search(message.text)
                if match:
                    hits[row["chat"].id] = " ".join(message.text[max(0, match.start() - 60):match.end() + 100].split())
                    break
        return {"query": query, "hits": hits}

    def media_chat(chat_id: str, request: Request):
        """Real Telegram chats only, and never for a page on another site."""
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Запрос с другого сайта запрещён")
        row = store.get(chat_id)
        if not row:
            raise HTTPException(404, "Чат не найден")
        return row["chat"]

    @app.get("/api/telegram/layout")
    async def telegram_layout(cached: bool = False):
        """Live order and folders; the last saved copy when asked for it or when Telegram cannot answer."""
        saved = store.metadata("telegram_layout", {"dialogs": [], "folders": []})
        if cached or not telegram.authorized:
            return saved
        async with layout_lock:
            saved = store.metadata("telegram_layout", saved)
            fetched = saved.get("fetched_at")
            if fetched and (utcnow() - datetime.fromisoformat(fetched)).total_seconds() < LAYOUT_FRESH_SECONDS:
                return saved
            try:
                telegram.require_ready()
                fresh = await fetch_layout(await telegram.get_client())
            except (ValueError, OSError, errors.RPCError) as error:
                return {**saved, "stale": True, "error": type(error).__name__}
            store.set_metadata("telegram_layout", fresh)
            return fresh

    async def export_peer(chat):
        if chat.demo:
            raise ValueError("Для демо-чата доступна только выгрузка последних сообщений")
        if not telegram.authorized:
            raise ValueError("Подключите Telegram в веб-панели")
        return await media.client_and_peer(chat.id)

    @app.get("/api/telegram/search")
    async def telegram_search(q: str, private_only: bool = True):
        """Whole-history search. It costs Telegram requests, so the page runs it on demand, not on every key."""
        if len(q.strip()) < 2:
            raise ValueError("Введите хотя бы 2 символа")
        if not telegram.authorized:
            raise ValueError("Подключите Telegram в веб-панели")
        telegram.require_ready()
        rows = {row["chat"].id: row["chat"] for row in store.all()}
        return await search_history(await telegram.get_client(), q.strip(), rows, private_only)

    @app.get("/api/chats/{chat_id}/history")
    async def chat_history(chat_id: str, request: Request, around: int | None = None, before: int | None = None, after: int | None = None):
        client, peer = await export_peer(media_chat(chat_id, request))
        return {"messages": await history_window(client, peer, telegram.message, around, before, after)}

    @app.get("/api/chats/{chat_id}/export/info")
    async def export_info(chat_id: str, request: Request):
        client, peer = await export_peer(media_chat(chat_id, request))
        return {"total": (await client.get_messages(peer, limit=0)).total}

    @app.get("/api/chats/{chat_id}/export")
    async def export_chat(chat_id: str, request: Request, scope: Literal["recent", "full"] = "recent"):
        chat = media_chat(chat_id, request)
        if scope == "recent":
            language = store.settings().language
            name = chat.title + translate(" - последние 100.txt", language)
            return Response(recent_text(chat, language=language), media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": attachment_header(name, False)})
        client, peer = await export_peer(chat)
        total = (await client.get_messages(peer, limit=0)).total
        language = store.settings().language
        return StreamingResponse(full_history(client, peer, chat, total, language), media_type="text/plain; charset=utf-8",
                                 headers={"Content-Disposition": attachment_header(chat.title + translate(" - вся история.txt", language), False)})

    @app.get("/api/chats/{chat_id}/media")
    async def media_info(chat_id: str, request: Request, ids: str = ""):
        chat = media_chat(chat_id, request)
        message_ids = [int(value) for value in ids.split(",") if value.isdigit()][:100]
        if chat.demo or not message_ids:
            return {"items": {}}
        if not telegram.authorized:  # possibly still connecting after a start: the page tries again
            return {"items": {}, "retry": True}
        return {"items": await media.info(chat_id, message_ids)}

    @app.get("/api/chats/{chat_id}/media/{message_id}/preview")
    async def media_preview(chat_id: str, message_id: int, request: Request):
        media_chat(chat_id, request)
        try:
            return FileResponse(await media.cached_image(chat_id, message_id, "preview"), media_type="image/jpeg")
        except KeyError:
            raise HTTPException(404, "Вложение не найдено") from None

    @app.get("/api/chats/{chat_id}/media/{message_id}/file")
    async def media_file(chat_id: str, message_id: int, request: Request, download: bool = False):
        media_chat(chat_id, request)
        try:
            message = await media.message(chat_id, message_id)
            described = describe(message)
            inline = described["mime"] in INLINE_TYPES and not download
            disposition = {"Content-Disposition": attachment_header(described["name"], inline)}
            if described["size"] is None:  # a photo: Telegram has no byte ranges for it, the disk cache does
                return FileResponse(await media.cached_image(chat_id, message_id, "full"), media_type="image/jpeg", headers=disposition)
            byte_range = parse_range(request.headers.get("range"), described["size"])
            body, start, end, size = await media.open_stream(chat_id, message_id, byte_range)
        except KeyError:
            raise HTTPException(404, "Вложение не найдено") from None
        except ValueError as error:
            if "диапазон" in str(error):
                return Response(status_code=416, headers={"Content-Range": f"bytes */{described['size']}"})
            raise
        headers = {**disposition, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
        if byte_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(body, status_code=206 if byte_range else 200,
                                 media_type=described["mime"] if inline else "application/octet-stream", headers=headers)

    @app.get("/api/chats/{chat_id}")
    async def chat(chat_id: str):
        row = store.get(chat_id)
        if not row:
            raise HTTPException(404, "Чат не найден")
        return {**apply_companies(apply_romance(apply_triage(classify(**row, settings=store.settings()), row["chat"], row["annotation"], store.metadata("triage", {})),
                                                row["chat"], row["annotation"], store.metadata(ROMANCE_KEY, {})),
                                  row["chat"], row["annotation"], folder_index(store.metadata("telegram_layout"))),
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
        store.save_credentials(**{f"{key.provider}_api_key": key.api_key.strip()})
        return {"ok": True}

    @app.delete("/api/llm/key")
    async def delete_key(provider: str = "openai"):
        if provider not in ("openai", "anthropic"):
            raise ValueError("Неизвестный провайдер")
        store.save_credentials(**{f"{provider}_api_key": ""})
        env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        return {"ok": True, "environment_key_present": bool(os.environ.get(env))}

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

    @app.post("/api/cleanup", status_code=202)
    async def cleanup(request: CleanupRequest):
        rows = {row["id"]: row for row in analyzed_rows()}
        plan, skipped = [], {"important": 0, "not_selected_action": 0, "unknown": 0}
        for chat_id in request.chat_ids:
            row = rows.get(chat_id)
            if not row or row["demo"]:
                skipped["unknown"] += 1
            elif "important" in row["categories"] and not request.include_important:
                skipped["important"] += 1  # pinned, frequent, marked or LLM-important chats are protected by default
            elif row["kind"] in ("channel", "group") and request.unfollow:
                plan.append({**{k: row[k] for k in ("id", "title", "kind", "username")}, "action": "leave"})
            elif row["kind"] in ("user", "bot") and request.delete:
                plan.append({**{k: row[k] for k in ("id", "title", "kind", "username")}, "action": "delete"})
            else:
                skipped["not_selected_action"] += 1
        if not plan:
            raise ValueError("Нет чатов для очистки: важные защищены, а для остальных не выбрано подходящее действие")
        telegram.start_cleanup(plan)
        return {"started": True, "planned": len(plan), "skipped": skipped}

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

    @app.post("/api/llm/estimate")
    async def llm_estimate(request: BatchRequest):
        return estimate(store, request)

    @app.post("/api/llm/analyze", status_code=202)
    async def llm(request: BatchRequest):
        start_llm(request)
        return {"started": True}

    @app.post("/api/romance/estimate")
    async def romance_estimate(request: BatchRequest):
        return estimate_romance(store, request)

    @app.post("/api/romance/export")
    async def romance_export(request: BatchRequest):
        return export_romance(store, request)

    @app.post("/api/romance/import")
    async def romance_import(request: RomanceImport):
        return {"imported": import_romance(store, request.batch_id, request.result)}

    @app.post("/api/romance/analyze", status_code=202)
    async def romance_analyze(request: BatchRequest):
        start_llm(request, analyze_romance)
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
        language = store.settings().language
        writer.writerow([translate(name, language) for name in ("Чат", "Категории", "Последнее сообщение (UTC)", "Дней без общения", "Сообщений за 30 дней (в выборке)", "Причины")])
        def cell(value):
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for row in analyzed_rows():
            writer.writerow([cell(row["title"]), "; ".join(translate(CATEGORIES[c], language) for c in row["categories"]),
                             row["last_message_at"], row["idle_days"], row["recent_messages"], cell("; ".join(translate(reason, language) for reason in row["reasons"]))])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="telegram-chats.csv"'})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

