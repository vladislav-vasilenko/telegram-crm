"""Download a chat as a text file: the last messages from the local store, or the whole history from Telegram."""
import asyncio
from datetime import timedelta, timezone

from telethon import errors, types, utils

RECENT_LIMIT = 100
FLOOD_WAIT_MAX = 60
FLOOD_RETRIES = 5
MOSCOW = timezone(timedelta(hours=3))
STORED_MEDIA = {"MessageMediaPhoto": "[фото]", "MessageMediaDocument": "[файл, видео или аудио]",
                "MessageMediaContact": "[контакт]", "MessageMediaGeo": "[геопозиция]", "MessageMediaGeoLive": "[геопозиция]",
                "MessageMediaPoll": "[опрос]", "MessageMediaWebPage": ""}


def header(title, chat_id, description):
    return (f"Переписка: {title} (Telegram id {chat_id})\n{description}\n"
            "Время московское (UTC+3). Фото, видео и файлы не сохранены, указан только их тип.\n")


class Lines:
    """Formats messages in order and starts a new section for each day."""
    def __init__(self):
        self.day = None

    def format(self, date, message_id, who, flags, body):
        local = date.astimezone(MOSCOW)
        prefix = ""
        if local.date() != self.day:
            self.day = local.date()
            prefix = f"\n--- {local:%d.%m.%Y} ---\n"
        return f"{prefix}[{local:%H:%M}] #{message_id} {who}{''.join(f' [{flag}]' for flag in flags)}: {body}\n"


def recent_text(chat, limit=RECENT_LIMIT):
    messages = sorted(chat.messages, key=lambda m: (m.date, m.id))[-limit:]
    other = chat.title if chat.kind == "user" else "Участник" if chat.kind == "group" else chat.title
    note = f"Последние сообщения: {len(messages)}, из последнего сканирования приложения."
    lines, parts = Lines(), [header(chat.title, chat.id, note)]
    for message in messages:
        label = STORED_MEDIA.get(message.media, f"[{message.media}]") if message.media else ""
        parts.append(lines.format(message.date, message.id, "Я" if message.outgoing else other,
                                  ["переслано"] if message.forwarded else [],
                                  " ".join(part for part in (label, message.text) if part)))
    return "".join(parts)


def media_label(message):
    action = getattr(message, "action", None)
    if action:
        return f"[служебное: {type(action).__name__.replace('MessageAction', '')}]"
    media = getattr(message, "media", None)
    if not media or isinstance(media, types.MessageMediaWebPage):
        return ""
    name = getattr(getattr(message, "file", None), "name", None)
    named = f": {name}" if name else ""
    for attribute, label in (("photo", "[фото]"), ("video_note", "[видеосообщение]"), ("voice", "[голосовое]"), ("gif", "[GIF]"),
                             ("sticker", "[стикер]"), ("video", f"[видео{named}]"), ("audio", f"[аудио{named}]"), ("document", f"[файл{named}]")):
        if getattr(message, attribute, None):
            return label
    return f"[{type(media).__name__.replace('MessageMedia', '')}]"


def sender_name(message, chat):
    if getattr(message, "out", False):
        return "Я"
    sender = getattr(message, "sender", None)
    name = utils.get_display_name(sender) if sender else ""
    return name or (chat.title if chat.kind != "group" else "Участник")


async def full_history(client, peer, chat, total):
    """Oldest first. A failure can no longer change the HTTP status, so it is written into the file itself."""
    yield header(chat.title, chat.id, f"Вся история. В Telegram сообщений: {total}.")
    lines, written, last_id, retries = Lines(), 0, 0, 0
    while True:
        try:
            async for message in client.iter_messages(peer, limit=None, reverse=True, min_id=last_id):
                flags = [flag for flag, on in (("переслано", getattr(message, "fwd_from", None)),
                                               ("изменено", getattr(message, "edit_date", None) and not getattr(message, "action", None))) if on]
                reply = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
                if reply:
                    flags.append(f"ответ на #{reply}")
                body = " ".join(part for part in (media_label(message), getattr(message, "message", None) or "") if part)
                yield lines.format(message.date, message.id, sender_name(message, chat), flags, body)
                written, last_id = written + 1, message.id
            break
        except errors.FloodWaitError as error:
            retries += 1
            if error.seconds > FLOOD_WAIT_MAX or retries > FLOOD_RETRIES:
                yield f"\n!!! ВЫГРУЗКА НЕПОЛНАЯ: Telegram запросил паузу {error.seconds} сек. Выгружено {written} из {total}. Повторите позже.\n"
                return
            await asyncio.sleep(error.seconds)
        except (errors.RPCError, OSError) as error:
            yield f"\n!!! ВЫГРУЗКА НЕПОЛНАЯ: ошибка {type(error).__name__}. Выгружено {written} из {total}. Повторите позже.\n"
            return
    yield f"\nВыгрузка завершена. Сообщений в файле: {written}.\n"
