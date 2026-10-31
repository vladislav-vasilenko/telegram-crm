import asyncio
import base64
import io
import os
import uuid
from datetime import timedelta

from telethon import TelegramClient, errors, functions, types

from .analysis import classify
from .models import Chat, Message
from .store import utcnow

# Pause between history requests: starts small, doubles after a FloodWait, then decays back.
HISTORY_DELAY_MIN = 0.25
HISTORY_DELAY_MAX = 3.0
# FloodWaits up to this long are waited out in place; longer ones stop the scan with a cooldown.
FLOOD_WAIT_MAX = 60
FLOOD_RETRIES = 3
# Incremental updates miss edits/deletions of older messages, so reload each chat fully this often.
FULL_REFRESH_AFTER = timedelta(days=7)


class TelegramService:
    def __init__(self, store):
        self.store = store
        self.client = None
        self.lock = asyncio.Lock()
        self.authorized = False
        self.account = None
        self.phone = None
        self.phone_code_hash = None
        self.auth_step = "phone"
        self.delivery = None
        self.qr_task = None
        self.qr_image = None
        self.qr_error = None
        self.task = None
        self.history_delay = HISTORY_DELAY_MIN
        self.status ={"running": False, "done": 0, "total": 0, "message": "Готов к работе", "error": None}

    async def get_client(self):
        if self.client is None:
            credentials = self.store.credentials()
            api_id = os.environ.get("TG_API_ID") or credentials.get("api_id")
            api_hash = os.environ.get("TG_API_HASH") or credentials.get("api_hash")
            if not api_id or not api_hash:
                raise ValueError("Сначала подключите Telegram")
            self.client = TelegramClient(str(self.store.directory / "telegram"), int(api_id), api_hash,
                                         receive_updates=False, flood_sleep_threshold=0,
                                         connection_retries=2, request_retries=2, timeout=10,
                                         device_model="Dialogue CRM", app_version="0.1.0")
            (self.store.directory / "telegram.session").chmod(0o600)
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def restore(self):
        if not (self.store.directory / "telegram.session").exists():
            return
        async with self.lock:
            try:
                client = await self.get_client()
                if await client.is_user_authorized():
                    await self.set_authorized()
            except (ValueError, OSError, errors.RPCError):
                self.status["message"] = "Не удалось восстановить подключение. Проверьте сеть и войдите повторно."

    async def set_authorized(self):
        me = await self.client.get_me()
        if me.bot:
            raise ValueError("Нужен личный аккаунт Telegram, бот не может читать вашу переписку")
        previous = self.store.metadata("telegram_account_id")
        if previous is not None and previous != me.id and any(not row["chat"].demo for row in self.store.all()):
            await self.client.disconnect()
            self.client = None
            raise ValueError("В этой базе уже есть другой аккаунт. Запустите сервис с отдельным --data-dir.")
        self.store.set_metadata("telegram_account_id", me.id)
        self.authorized = True
        self.account = {"title": me.first_name or "Telegram", "premium": bool(me.premium)}
        self.auth_step = "done"
        self.phone, self.phone_code_hash = None, None
        self.qr_image = None

    async def begin_auth(self, request):
        await self.cancel_qr()
        async with self.lock:
            if self.authorized:
                raise ValueError("Telegram уже подключён")
            if self.client:
                await self.client.disconnect()
                self.client = None
            self.store.save_credentials(api_id=request.api_id, api_hash=request.api_hash)
            client = await self.get_client()
            if await client.is_user_authorized():
                await self.set_authorized()
                return {"step": "done"}
            sent = await client.send_code_request(request.phone)
            self.describe_delivery(sent)
            self.phone, self.phone_code_hash = request.phone, sent.phone_code_hash
            self.auth_step = "code"
            return {"step": "code", "delivery": self.delivery}

    def describe_delivery(self, sent):
        kind = type(getattr(sent, "type", None)).__name__
        explanations = {
            "SentCodeTypeApp": "Код отправлен в служебный чат Telegram на уже подключённых устройствах. Откройте Telegram на телефоне; SMS в этом случае не ожидается.",
            "SentCodeTypeSms": "Telegram выбрал отправку кода по SMS. Проверьте сообщения на номере вашего аккаунта.",
            "SentCodeTypeSmsWord": "Telegram отправил по SMS секретное слово. Введите слово целиком.",
            "SentCodeTypeSmsPhrase": "Telegram отправил по SMS секретную фразу. Введите фразу целиком.",
            "SentCodeTypeEmailCode": "Telegram отправил код на настроенную почту: " + str(getattr(getattr(sent, "type", None), "email_pattern", "проверьте email")),
            "SentCodeTypeCall": "Telegram выбрал голосовой звонок: код продиктуют по телефону.",
            "SentCodeTypeFlashCall": "Telegram выбрал короткий звонок. Если этот способ недоступен, используйте вход по QR.",
            "SentCodeTypeMissedCall": "Telegram выбрал пропущенный звонок: код — последние цифры номера звонящего.",
            "SentCodeTypeFragmentSms": "Telegram выбрал доставку через Fragment. Для этого сервиса используйте вход по QR.",
            "SentCodeTypeFirebaseSms": "Telegram выбрал SMS через Firebase — такой способ работает только в официальных приложениях, поэтому SMS, скорее всего, не придёт. Нажмите «Повторить доставку» (когда кнопка доступна) или войдите по QR.",
        }
        if kind == "SentCodeTypeSetUpEmailRequired":
            raise ValueError("Telegram требует привязать email для входа и код не отправил. Войдите по QR через Telegram → Настройки → Устройства → Подключить устройство.")
        if kind not in explanations:
            raise ValueError("Telegram выбрал способ входа, который не поддерживается этой формой. Используйте вход по QR через уже подключённый телефон.")
        timeout = max(0, int(getattr(sent, "timeout", 0) or 0))
        self.delivery = {"type": kind, "message": explanations[kind],
                         "can_resend": bool(getattr(sent, "next_type", None)),
                         "resend_after": (utcnow() + timedelta(seconds=timeout)).isoformat(),
                         "next_type": type(sent.next_type).__name__ if getattr(sent, "next_type", None) else None}

    async def resend_code(self):
        async with self.lock:
            if self.auth_step != "code" or not self.phone or not self.phone_code_hash:
                raise ValueError("Запросите вход заново или используйте QR")
            if not self.delivery or not self.delivery["can_resend"]:
                raise ValueError("Telegram не предложил другой способ доставки. Используйте QR.")
            if utcnow().isoformat() < self.delivery["resend_after"]:
                raise ValueError("Дождитесь таймера Telegram перед повторным запросом")
            sent = await self.client(functions.auth.ResendCodeRequest(self.phone, self.phone_code_hash))
            self.describe_delivery(sent)
            self.phone_code_hash = sent.phone_code_hash
            return {"step": "code", "delivery": self.delivery}

    async def begin_qr(self, app_credentials=None):
        await self.cancel_qr()
        async with self.lock:
            if self.authorized:
                return {"step": "done"}
            if app_credentials:
                if self.client:
                    await self.client.disconnect()
                    self.client = None
                self.store.save_credentials(api_id=app_credentials.api_id, api_hash=app_credentials.api_hash)
            client = await self.get_client()
            if await client.is_user_authorized():
                await self.set_authorized()
                return {"step": "done"}
            await self.enable_login_updates(client)
            qr = await client.qr_login()
            self.auth_step, self.qr_error = "qr", None
            self.render_qr(qr)
            self.phone, self.phone_code_hash, self.delivery = None, None, None
            self.qr_task = asyncio.create_task(self.wait_qr(qr))
            # QRLogin.wait must register its update handler before the image reaches the browser.
            await asyncio.sleep(0)
            return {"step": "qr"}

    @staticmethod
    async def enable_login_updates(client):
        # QR login completes via an UpdateLoginToken push, so requests must stop being wrapped in
        # InvokeWithoutUpdates. set_receive_updates() flips that flag first and then calls
        # updates.getState, which Telegram rejects for a not-yet-authorized key
        # (AUTH_KEY_UNREGISTERED). The flag is what matters; the following ExportLoginToken
        # request (sent without the wrapper) subscribes this connection to updates.
        try:
            await client.set_receive_updates(True)
        except (errors.AuthKeyError, errors.UnauthorizedError):
            pass

    def render_qr(self, qr):
        import qrcode
        buffer = io.BytesIO()
        qrcode.make(qr.url, border=4, box_size=7).save(buffer, format="PNG")
        self.qr_image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    async def wait_qr(self, qr):
        try:
            for _ in range(30):
                try:
                    await qr.wait()
                    async with self.lock:
                        await self.set_authorized()
                    return
                except asyncio.TimeoutError:
                    if self.auth_step != "qr":
                        return
                    async with self.lock:
                        await qr.recreate()
                        self.render_qr(qr)
            self.qr_error = "Время входа истекло. Создайте новый QR."
            self.auth_step = "phone"
        except errors.SessionPasswordNeededError:
            self.auth_step = "password"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.qr_error = f"Не удалось завершить QR-вход ({type(error).__name__}). Попробуйте снова."
            self.auth_step = "phone"
        finally:
            if self.auth_step != "qr":
                self.qr_image = None

    async def cancel_qr(self):
        if self.qr_task and not self.qr_task.done():
            self.qr_task.cancel()
            try:
                await self.qr_task
            except asyncio.CancelledError:
                pass
        self.qr_task = None
        self.qr_image = None
        if self.auth_step == "qr":
            self.auth_step = "phone"

    async def submit_code(self, code):
        async with self.lock:
            if self.auth_step != "code" or not self.phone or not self.phone_code_hash:
                raise ValueError("Запросите новый код входа")
            try:
                normalized = code.strip()
                if self.delivery and self.delivery["type"] not in ("SentCodeTypeSmsWord", "SentCodeTypeSmsPhrase"):
                    normalized = normalized.replace(" ", "").replace("-", "")
                await self.client.sign_in(phone=self.phone, code=normalized, phone_code_hash=self.phone_code_hash)
            except errors.SessionPasswordNeededError:
                self.auth_step = "password"
                return {"step": "password"}
            await self.set_authorized()
            return {"step": "done"}

    async def submit_password(self, password):
        async with self.lock:
            if self.auth_step != "password" or not self.client:
                raise ValueError("Сначала подтвердите код входа")
            await self.client.sign_in(password=password)
            await self.set_authorized()
            return {"step": "done"}

    def require_ready(self):
        if not self.authorized:
            raise ValueError("Подключите Telegram в веб-панели")
        cooldown = self.store.metadata("telegram_cooldown_until")
        if cooldown and utcnow().isoformat() < cooldown:
            raise ValueError(f"Telegram запросил паузу до {cooldown}")

    def start_scan(self, full=False):
        self.require_ready()
        if self.lock.locked() or self.status["running"]:
            raise ValueError("Операция с Telegram уже выполняется")
        self.status = {"running": True, "done": 0, "total": 0, "error": None,
                       "message": "Полное обновление: загружаю список чатов…" if full else "Загружаю список чатов…"}
        self.task = asyncio.create_task(self.scan(full=full))

    @staticmethod
    def history_plan(old, top_id, limit, full=False):
        """Decide how to refresh one chat: reuse the cache, fetch only newer messages, or reload it."""
        if (full or not old or old.history_status != "ok" or old.history_limit != limit
                or not old.history_fetched_at or utcnow() - old.history_fetched_at >= FULL_REFRESH_AFTER):
            return "full"
        if top_id == old.last_message_id:
            return "reuse"
        if top_id and old.last_message_id and top_id > old.last_message_id:
            return "incremental"
        return "full"  # The newest message disappeared: something was deleted, reload.

    @staticmethod
    def merge_history(newer, cached, limit):
        by_id = {m.id: m for m in cached}
        by_id.update({m.id: m for m in newer})
        return sorted(by_id.values(), key=lambda m: m.id, reverse=True)[:limit]

    async def fetch_history(self, client, dialog, limit, min_id=0):
        """Fetch history with adaptive pacing; short FloodWaits are waited out instead of aborting."""
        for attempt in range(FLOOD_RETRIES + 1):
            messages, status = [], "ok"
            try:
                async for message in client.iter_messages(dialog.input_entity, limit=limit, min_id=min_id):
                    if isinstance(message, types.Message):
                        messages.append(self.message(message))
            except errors.FloodWaitError as error:
                if error.seconds > FLOOD_WAIT_MAX or attempt == FLOOD_RETRIES:
                    raise
                self.history_delay = min(HISTORY_DELAY_MAX, max(self.history_delay * 2, 1.0))
                previous = self.status["message"]
                self.status["message"] = f"Telegram попросил паузу {error.seconds} сек — продолжу автоматически"
                await asyncio.sleep(error.seconds + 1)
                self.status["message"] = previous
                continue
            except (errors.RPCError, ValueError):
                status = "unavailable" if not messages else "limited"
            await asyncio.sleep(self.history_delay)
            # Speed back up gradually after a FloodWait.
            self.history_delay = max(HISTORY_DELAY_MIN, self.history_delay * 0.9)
            return messages, status

    async def scan(self, full=False):
        async with self.lock:
            scan_id = uuid.uuid4().hex
            self.status.update(running=True, error=None)
            self.history_delay = HISTORY_DELAY_MIN
            fetched = 0
            try:
                client = await self.get_client()
                settings = self.store.settings()
                limit = settings.history_limit
                dialogs = [dialog async for dialog in client.iter_dialogs(ignore_migrated=True)]
                dialogs = [d for d in dialogs if not getattr(d.entity, "is_self", False)]
                self.status["total"] = len(dialogs)
                self.store.clear_demo()
                self.store.set_metadata("demo", False)
                for index, dialog in enumerate(dialogs):
                    entity = dialog.entity
                    previous = self.store.get(str(dialog.id))
                    old = previous["chat"] if previous else None
                    top = self.message(dialog.message) if isinstance(dialog.message, types.Message) else None
                    top_id = dialog.message.id if dialog.message else None
                    self.status["message"] = f"{'Полное обновление' if full else 'Обновление'}: чат {index + 1} из {len(dialogs)}"
                    plan = self.history_plan(old, top_id, limit, full)
                    if plan == "reuse":
                        # The dialog list already carries the newest message, so an edit to it costs no request.
                        messages = self.merge_history([top] if top else [], old.messages, limit)
                        history_status, fetched_at = old.history_status, old.history_fetched_at
                    elif plan == "incremental":
                        newer, history_status = await self.fetch_history(client, dialog, limit, min_id=old.last_message_id)
                        fetched += 1
                        if history_status != "ok":
                            newer, history_status = newer + ([top] if top else []), "limited"  # next scan reloads fully
                        messages = self.merge_history(newer, old.messages, limit)
                        fetched_at = old.history_fetched_at
                    else:
                        messages, history_status = await self.fetch_history(client, dialog, limit)
                        fetched += 1
                        if history_status == "unavailable" and top:
                            messages, history_status = [top], "limited"
                        fetched_at = utcnow()
                    kind = "bot" if getattr(entity, "bot", False) else "user" if dialog.is_user else "channel" if dialog.is_channel and not dialog.is_group else "group"
                    chat = Chat(id=str(dialog.id), title=dialog.name or "Без имени", username=getattr(entity, "username", None),
                                kind=kind, pinned=bool(dialog.pinned), archived=bool(dialog.archived),
                                unread_count=dialog.unread_count, telegram_scam=bool(getattr(entity, "scam", False)),
                                telegram_fake=bool(getattr(entity, "fake", False)), deleted=bool(getattr(entity, "deleted", False)),
                                messages=messages, last_message_id=top_id, history_status=history_status,
                                history_limit=limit, history_fetched_at=fetched_at)
                    self.store.upsert(chat, scan_id)
                    self.status["done"] = index + 1
                self.store.finish_scan(scan_id)
                self.status["message"] = f"Обновлено чатов: {len(dialogs)}, запрошено у Telegram: {fetched}"
            except errors.FloodWaitError as error:
                until = utcnow() + timedelta(seconds=error.seconds + 1)
                self.store.set_metadata("telegram_cooldown_until", until.isoformat())
                self.status["error"] = f"Telegram запросил паузу {error.seconds} сек. Частичный результат сохранён; повторите сканирование позже."
            except asyncio.CancelledError:
                self.status["message"] = "Сканирование остановлено; частичный результат сохранён"
                raise
            except Exception as error:
                self.status["error"] = f"Не удалось завершить сканирование ({type(error).__name__}). Проверьте сеть и подключение."
            finally:
                self.status["running"] = False
                self.store.set_metadata("last_scan_status", dict(self.status))

    @staticmethod
    def message(message):
        urls = [entity.url for entity in message.entities or [] if isinstance(entity, types.MessageEntityTextUrl)]
        return Message(id=message.id, date=message.date, outgoing=bool(message.out), text=message.message or "",
                       urls=urls, forwarded=message.fwd_from is not None,
                       media=type(message.media).__name__ if message.media else None)

    def folder_plan(self):
        names = {"important": "CRM Важные", "stalled": "CRM Зависло", "suspected_scam": "CRM Проверка",
                 "inactive": "CRM Неактив", "very_old": "CRM Старые", "dead": "CRM Мёртвые"}
        rows = [classify(**row, settings=self.store.settings()) for row in self.store.all() if not row["chat"].demo]
        return [{"category": key, "title": title, "chat_ids": [row["id"] for row in rows if key in row["categories"]]}
                for key, title in names.items()]

    async def apply_folders(self):
        self.require_ready()
        if self.lock.locked():
            raise ValueError("Дождитесь окончания сканирования")
        async with self.lock:
            client = await self.get_client()
            response = await client(functions.messages.GetDialogFiltersRequest())
            current = {f.id: f for f in response.filters if hasattr(f, "id")}
            owned = self.store.metadata("owned_folders", {})
            planned, used = [], set(current)
            # Preflight every peer before the first write. Never truncate large categories.
            for row in self.folder_plan():
                if not row["chat_ids"]:
                    continue
                saved = owned.get(row["category"])
                if saved and saved["id"] in current:
                    existing = current[saved["id"]]
                    title = getattr(existing.title, "text", existing.title)
                    if title != saved["title"] or not isinstance(existing, types.DialogFilter):
                        raise ValueError("Одна из CRM-папок переименована или изменена. Обновление остановлено, пользовательские папки сохранены.")
                    folder_id = saved["id"]
                else:
                    folder_id = next((i for i in range(2, 256) if i not in used), None)
                    if folder_id is None:
                        raise ValueError("Нет свободного ID папки Telegram")
                    used.add(folder_id)
                peers = [await client.get_input_entity(int(peer_id)) for peer_id in row["chat_ids"]]
                planned.append((row, folder_id, peers))
            completed = []
            for row, folder_id, peers in planned:
                folder = types.DialogFilter(id=folder_id, title=types.TextWithEntities(text=row["title"], entities=[]),
                                            pinned_peers=[], include_peers=peers, exclude_peers=[])
                try:
                    await client(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=folder))
                except errors.RPCError as error:
                    raise ValueError(f"Обновлено {len(completed)} папок. Telegram отклонил следующую ({type(error).__name__}); проверьте лимит папок и чатов в папке. Чаты не обрезались.") from None
                owned[row["category"]] = {"id": folder_id, "title": row["title"]}
                self.store.set_metadata("owned_folders", owned)
                completed.append(row["title"])
            return {"updated": completed, "note": "Пустые категории пропущены; существующие пустеющие папки оставлены без изменений."}

    async def close(self):
        await self.cancel_qr()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.disconnect()

