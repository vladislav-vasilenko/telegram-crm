from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from telethon import types

from telegram_crm.media import attachment_header, describe, parse_range
from telegram_crm.models import Chat, Message
from telegram_crm.server import create_app
from telegram_crm.store import Store, utcnow

SIZE = 1_300_000
BLOB = bytes(i % 251 for i in range(SIZE))


def document(mime="video/mp4", attributes=None, size=SIZE):
    return types.Document(id=1, access_hash=1, file_reference=b"", date=utcnow(), mime_type=mime, size=size, dc_id=2,
                          attributes=attributes if attributes is not None else [types.DocumentAttributeVideo(duration=5, w=640, h=360)],
                          thumbs=[])


def message(doc, message_id=7, **media):
    return SimpleNamespace(id=message_id, document=doc, media=types.MessageMediaDocument(document=doc, **media))


class Chunks:
    """Telegram rejects requests that are not aligned to the request size, so the fake does too."""
    def __init__(self, offset, request_size):
        assert offset % request_size == 0
        self.position, self.step, self.closed = offset, request_size, False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.position >= SIZE:
            raise StopAsyncIteration
        chunk = BLOB[self.position:self.position + self.step]
        self.position += self.step
        return chunk


class FakeClient:
    def __init__(self, found):
        self.found, self.requests = found, 0

    async def get_input_entity(self, chat_id):
        return chat_id

    async def get_messages(self, peer, ids):
        self.requests += 1
        return [self.found.get(i) for i in ids] if isinstance(ids, list) else self.found.get(ids)

    def iter_download(self, file, offset, request_size, file_size):
        return Chunks(offset, request_size)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as test:
        app.state.store.upsert(Chat(id="42", title="Чат", messages=[Message(id=7, date=utcnow(), outgoing=False, media="MessageMediaDocument")]))
        app.state.store.upsert(Chat(id="demo_1", title="Демо", demo=True))
        telegram = app.state.telegram
        telegram.authorized = True
        test.fake = FakeClient({7: message(document()), 8: message(document("text/html", []), 8), 9: message(document(), 9, ttl_seconds=10)})

        async def get_client():
            return test.fake
        telegram.get_client = get_client
        yield test


@pytest.mark.parametrize("header,expected", [
    (None, None), ("bytes=0-99", (0, 99)), ("bytes=100-", (100, 999)), ("bytes=-10", (990, 999)),
    ("bytes=900-5000", (900, 999)), ("items=0-1", None), ("bytes=0-1,5-6", None),
])
def test_parse_range(header, expected):
    assert parse_range(header, 1000) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=5-2", "bytes=a-b", "bytes=-0"])
def test_parse_range_rejects(header):
    with pytest.raises(ValueError):
        parse_range(header, 1000)


def test_filename_cannot_break_the_header():
    assert "\r" not in attachment_header('a"\r\nX: y.mp4', True) and attachment_header("a b.mp4", False).startswith("attachment;")


def test_self_destructing_media_is_never_offered():
    assert describe(message(document(), ttl_seconds=10)) is None


def test_info_uses_one_request_and_skips_self_destructing(client):
    items = client.get("/api/chats/42/media", params={"ids": "7,8,9,10"}).json()["items"]
    assert client.fake.requests == 1
    assert items["7"]["kind"] == "video" and items["7"]["width"] == 640
    assert items["8"]["kind"] == "file" and "9" not in items and "10" not in items


def test_demo_chat_never_reaches_telegram(client):
    assert client.get("/api/chats/demo_1/media", params={"ids": "1"}).json() == {"items": {}}
    assert client.fake.requests == 0


def test_whole_file_and_ranges_return_exact_bytes(client):
    whole = client.get("/api/chats/42/media/7/file")
    assert whole.status_code == 200 and whole.content == BLOB and whole.headers["accept-ranges"] == "bytes"
    # Crosses a request boundary and starts at an unaligned offset.
    partial = client.get("/api/chats/42/media/7/file", headers={"Range": "bytes=524000-525000"})
    assert partial.status_code == 206 and partial.content == BLOB[524000:525001]
    assert partial.headers["content-range"] == f"bytes 524000-525000/{SIZE}" and partial.headers["content-length"] == "1001"
    tail = client.get("/api/chats/42/media/7/file", headers={"Range": "bytes=-5"})
    assert tail.content == BLOB[-5:]
    assert client.get("/api/chats/42/media/7/file", headers={"Range": f"bytes={SIZE}-"}).status_code == 416


def test_only_safe_types_render_inline(client):
    video = client.get("/api/chats/42/media/7/file", headers={"Range": "bytes=0-9"})
    assert video.headers["content-type"] == "video/mp4" and video.headers["content-disposition"].startswith("inline")
    html = client.get("/api/chats/42/media/8/file", headers={"Range": "bytes=0-9"})
    assert html.headers["content-type"] == "application/octet-stream" and html.headers["content-disposition"].startswith("attachment")
    forced = client.get("/api/chats/42/media/7/file", params={"download": "true"}, headers={"Range": "bytes=0-9"})
    assert forced.headers["content-disposition"].startswith("attachment")


def test_missing_and_cross_site_requests_are_refused(client):
    assert client.get("/api/chats/42/media/9/file").status_code == 404
    assert client.get("/api/chats/404/media", params={"ids": "1"}).status_code == 404
    assert client.get("/api/chats/42/media/7/file", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_removing_a_chat_clears_its_cached_photos(tmp_path):
    from telegram_crm.media import media_dir
    store = Store(tmp_path)
    store.upsert(Chat(id="42", title="Чат"))
    directory = media_dir(store.directory, "42")
    directory.mkdir(parents=True)
    (directory / "1-full.jpg").write_bytes(b"x")
    store.mark_removed("42")
    assert not directory.exists()
