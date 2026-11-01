import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from telethon import errors, types

from telegram_crm.chat_export import full_history, recent_text
from telegram_crm.models import Chat, Message
from telegram_crm.server import create_app
from telegram_crm.store import utcnow

START = utcnow() - timedelta(days=5)


def stored(count):
    return [Message(id=i, date=START + timedelta(minutes=i), outgoing=i % 2 == 0, text=f"текст {i}") for i in range(1, count + 1)]


def live(message_id, **fields):
    return SimpleNamespace(id=message_id, date=START + timedelta(minutes=message_id), message=f"текст {message_id}", out=False, **fields)


class History:
    """Serves messages newer than min_id, oldest first, and can fail once at a chosen message."""
    def __init__(self, count, fail_after=None, error=None):
        self.count, self.fail_after, self.error, self.requests = count, fail_after, error, []

    def iter_messages(self, peer, limit, reverse, min_id):
        assert limit is None and reverse is True
        self.requests.append(min_id)

        async def rows():
            for message_id in range(min_id + 1, self.count + 1):
                if message_id == self.fail_after and self.error:
                    error, self.error = self.error, None
                    raise error
                yield live(message_id)
        return rows()

    async def get_messages(self, peer, limit):
        assert limit == 0
        return SimpleNamespace(total=self.count)

    async def get_input_entity(self, chat_id):
        return chat_id


def run_history(client, chat, total):
    async def collect():
        return "".join([part async for part in full_history(client, 1, chat, total)])
    return asyncio.run(collect())


def flood(seconds):
    error = errors.FloodWaitError(request=None, capture=seconds)
    error.seconds = seconds
    return error


CHAT = Chat(id="42", title="Анна")


def test_recent_keeps_last_hundred_in_order_with_direction():
    text = recent_text(Chat(id="42", title="Анна", messages=stored(130)))
    lines = [line for line in text.splitlines() if line.startswith("[")]
    assert len(lines) == 100 and "#31 " in lines[0] and "#130 " in lines[-1]
    assert " Я: текст 32" in text and " Анна: текст 31" in text and "Последние сообщения: 100" in text


def test_recent_labels_media_and_group_members():
    message = Message(id=1, date=START, outgoing=False, text="подпись", media="MessageMediaPhoto", forwarded=True)
    text = recent_text(Chat(id="42", title="Группа", kind="group", messages=[message]))
    assert "Участник [переслано]: [фото] подпись" in text


def test_full_history_is_complete_and_says_so():
    text = run_history(History(250), CHAT, 250)
    assert text.count("\n[") == 250 and "Выгрузка завершена. Сообщений в файле: 250." in text and "НЕПОЛНАЯ" not in text
    assert text.index("#1 ") < text.index("#250 ")


def test_short_flood_wait_resumes_without_duplicates(monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr("telegram_crm.chat_export.asyncio.sleep", sleep)
    client = History(10, fail_after=6, error=flood(5))
    text = run_history(client, CHAT, 10)
    assert slept == [5] and client.requests == [0, 5]
    assert text.count("\n[") == 10 and "Сообщений в файле: 10." in text


@pytest.mark.parametrize("error,part", [(flood(600), "пауза"), (OSError(), "OSError")])
def test_interrupted_download_is_marked_in_the_file(monkeypatch, error, part):
    text = run_history(History(10, fail_after=4, error=error), CHAT, 10)
    assert "ВЫГРУЗКА НЕПОЛНАЯ" in text and "Выгружено 3 из 10" in text and "завершена" not in text
    assert (part in text) or "600 сек" in text


def test_service_message_and_reply_flags():
    class One(History):
        def iter_messages(self, peer, limit, reverse, min_id):
            async def rows():
                yield SimpleNamespace(id=1, date=START, message=None, out=True, action=types.MessageActionPinMessage())
                yield SimpleNamespace(id=2, date=START, message="да", out=True, reply_to=SimpleNamespace(reply_to_msg_id=1), edit_date=START)
            return rows()
    text = run_history(One(2), CHAT, 2)
    assert "#1 Я: [служебное: PinMessage]" in text and "#2 Я [изменено] [ответ на #1]: да" in text


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as test:
        app.state.store.upsert(Chat(id="42", title="Анна / Пётр", messages=stored(3)))
        app.state.store.upsert(Chat(id="demo_1", title="Демо", demo=True, messages=stored(2)))
        telegram = app.state.telegram
        telegram.authorized = True

        async def get_client():
            return History(3)
        telegram.get_client = get_client
        yield test


def test_recent_needs_no_telegram_and_downloads(client):
    client.app.state.telegram.authorized = False
    answer = client.get("/api/chats/42/export")
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/plain")
    disposition = answer.headers["content-disposition"]
    assert disposition.startswith("attachment") and "/" not in disposition.split("''")[1]


def test_full_download_and_info(client):
    assert client.get("/api/chats/42/export/info").json() == {"total": 3}
    answer = client.get("/api/chats/42/export", params={"scope": "full"})
    assert answer.status_code == 200 and "Сообщений в файле: 3." in answer.text and "attachment" in answer.headers["content-disposition"]


def test_full_export_refused_when_impossible(client):
    assert client.get("/api/chats/demo_1/export", params={"scope": "full"}).status_code == 400
    assert client.get("/api/chats/demo_1/export").status_code == 200
    client.app.state.telegram.authorized = False
    assert client.get("/api/chats/42/export/info").status_code == 400
    assert client.get("/api/chats/404/export").status_code == 404
    assert client.get("/api/chats/42/export", params={"scope": "other"}).status_code == 422
    assert client.get("/api/chats/42/export", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
