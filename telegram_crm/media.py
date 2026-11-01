"""On-demand media from Telegram: nothing is fetched until a chat card asks for it.

Previews and photos are cached on disk per chat; video, audio and files stream straight through.
Files come from other people and are served from the panel's own origin, so only a fixed list of
image/video/audio types may render inline. Everything else (HTML and SVG included) is an attachment.
"""
import asyncio
import hashlib
import os
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

from telethon import types

CACHE_LIMIT_BYTES = 1024 ** 3
MESSAGE_TTL_SECONDS = 15 * 60  # file references expire, so messages are re-read after this
MESSAGE_CACHE_SIZE = 500
PREVIEW_MAX_SIDE = 800
PARALLEL_IMAGE_DOWNLOADS = 4
# Multiples of this never cross Telegram's 1 MB window for a single file request.
REQUEST_SIZE = 512 * 1024
INLINE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/webm", "video/quicktime",
                "audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/x-m4a", "audio/wav", "audio/flac"}


def media_dir(store_directory: Path, chat_id: str) -> Path:
    return store_directory / "media-cache" / hashlib.sha256(chat_id.encode()).hexdigest()[:24]


def parse_range(header: str | None, size: int):
    """(start, end) inclusive for a single byte range, None for a whole-file answer, ValueError if unsatisfiable."""
    if not header:
        return None
    unit, _, spec = header.partition("=")
    if unit.strip() != "bytes" or "," in spec:
        return None
    first, _, last = spec.strip().partition("-")
    try:
        if not first:  # suffix form: the last N bytes
            count = int(last)
            if count <= 0 or size == 0:
                raise ValueError
            return max(0, size - count), size - 1
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    except ValueError:
        raise ValueError("Неверный диапазон") from None
    if start >= size or end < start:
        raise ValueError("Неверный диапазон")
    return start, end


def attachment_header(name: str, inline: bool) -> str:
    safe = quote(name or "file", safe="")
    return f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{safe}"


def describe(message):
    """What the card should render for a message, or None when there is nothing to fetch."""
    media = getattr(message, "media", None)
    if getattr(media, "ttl_seconds", None):  # self-destructing: opening it here could burn it
        return None
    if isinstance(media, types.MessageMediaPhoto) and isinstance(media.photo, types.Photo):
        sized = [s for s in media.photo.sizes if hasattr(s, "w")]
        best = max(sized, key=lambda s: s.w * s.h, default=None)
        return {"kind": "image", "mime": "image/jpeg", "size": None, "name": f"photo_{message.id}.jpg",
                "width": best.w if best else None, "height": best.h if best else None, "preview": True}
    document = getattr(media, "document", None) if isinstance(media, types.MessageMediaDocument) else None
    if not isinstance(document, types.Document):
        return None
    mime = (document.mime_type or "application/octet-stream").split(";")[0].lower()
    video = next((a for a in document.attributes if isinstance(a, types.DocumentAttributeVideo)), None)
    audio = next((a for a in document.attributes if isinstance(a, types.DocumentAttributeAudio)), None)
    image = next((a for a in document.attributes if isinstance(a, types.DocumentAttributeImageSize)), None)
    named = next((a.file_name for a in document.attributes if isinstance(a, types.DocumentAttributeFilename)), None)
    kind = "file"
    if mime in INLINE_TYPES:
        kind = "video" if mime.startswith("video/") and video else "audio" if mime.startswith("audio/") and audio else (
            "image" if mime.startswith("image/") else "file")
    return {"kind": kind, "mime": mime, "size": document.size, "name": named or f"file_{message.id}",
            "width": (video or image).w if (video or image) else None, "height": (video or image).h if (video or image) else None,
            "duration": getattr(video or audio, "duration", None), "preview": bool(document.thumbs)}


class MediaService:
    def __init__(self, telegram, store):
        self.telegram, self.store = telegram, store
        self.messages_cache = OrderedDict()
        self.images = asyncio.Semaphore(PARALLEL_IMAGE_DOWNLOADS)

    async def client_and_peer(self, chat_id):
        self.telegram.require_ready()
        client = await self.telegram.get_client()
        return client, await client.get_input_entity(int(chat_id))

    def remember(self, chat_id, message):
        key = (chat_id, message.id)
        self.messages_cache[key] = (time.monotonic(), message)
        self.messages_cache.move_to_end(key)
        while len(self.messages_cache) > MESSAGE_CACHE_SIZE:
            self.messages_cache.popitem(last=False)

    async def info(self, chat_id, message_ids):
        """One Telegram request for the whole card."""
        client, peer = await self.client_and_peer(chat_id)
        items = {}
        for message in await client.get_messages(peer, ids=list(message_ids)):
            if message is None:
                continue
            self.remember(chat_id, message)
            described = describe(message)
            if described:
                items[str(message.id)] = described
        return items

    async def message(self, chat_id, message_id):
        cached = self.messages_cache.get((chat_id, message_id))
        if cached and time.monotonic() - cached[0] < MESSAGE_TTL_SECONDS:
            return cached[1]
        client, peer = await self.client_and_peer(chat_id)
        message = await client.get_messages(peer, ids=message_id)
        if message is None or describe(message) is None:
            raise KeyError(message_id)
        self.remember(chat_id, message)
        return message

    def prune_cache(self):
        files = [p for p in (self.store.directory / "media-cache").rglob("*") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= CACHE_LIMIT_BYTES:
                break
            total -= path.stat().st_size
            path.unlink(missing_ok=True)

    async def cached_image(self, chat_id, message_id, variant):
        """Path of a cached JPEG preview or full photo; downloads it the first time."""
        directory = media_dir(self.store.directory, chat_id)
        path = directory / f"{message_id}-{variant}.jpg"
        if path.exists():
            os.utime(path)
            return path
        message = await self.message(chat_id, message_id)
        client = await self.telegram.get_client()
        thumb = None
        if variant == "preview":
            sizes = getattr(getattr(message.media, "photo", None), "sizes", None)
            sized = [s for s in sizes or () if isinstance(s, types.PhotoSize) and max(s.w, s.h) <= PREVIEW_MAX_SIDE]
            thumb = max(sized, key=lambda s: s.w * s.h) if sized else -1
        async with self.images:
            data = await client.download_media(message, file=bytes, thumb=thumb)
        if not data:
            raise KeyError(message_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        self.prune_cache()
        return path

    async def open_stream(self, chat_id, message_id, byte_range):
        """Read the first chunk now, so a Telegram error becomes an HTTP error instead of a broken body."""
        message = await self.message(chat_id, message_id)
        client = await self.telegram.get_client()
        size = message.document.size
        start, end = byte_range if byte_range else (0, size - 1)
        aligned = start - start % REQUEST_SIZE
        chunks = client.iter_download(message.document, offset=aligned, request_size=REQUEST_SIZE, file_size=size)
        first = await chunks.__anext__()

        async def body():
            position = aligned
            chunk = first
            try:
                while True:
                    piece = chunk[max(0, start - position):end + 1 - position]
                    position += len(chunk)
                    if piece:
                        yield piece
                    if position > end:
                        return
                    chunk = await chunks.__anext__()
            except StopAsyncIteration:
                return
            finally:
                close = getattr(chunks, "close", None)
                if close:
                    await close()

        return body(), start, end, size
