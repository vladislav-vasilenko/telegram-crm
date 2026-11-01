"""Download a chat as a text file: the last messages from the local store, or the whole history from Telegram."""
import asyncio
from datetime import timedelta, timezone

from telethon import errors, types, utils

from .i18n import translate

RECENT_LIMIT = 100
FLOOD_WAIT_MAX = 60
FLOOD_RETRIES = 5
MOSCOW = timezone(timedelta(hours=3))
STORED_MEDIA = {"MessageMediaPhoto": "[фото]", "MessageMediaDocument": "[файл, видео или аудио]",
                "MessageMediaContact": "[контакт]", "MessageMediaGeo": "[геопозиция]", "MessageMediaGeoLive": "[геопозиция]",
                "MessageMediaPoll": "[опрос]", "MessageMediaWebPage": ""}


def header(title, chat_id, description, language="ru"):
    return (f"{translate('Переписка:', language)} {title} (Telegram id {chat_id})\n{translate(description, language)}\n"
            + translate("Время московское (UTC+3). Фото, видео и файлы не сохранены, указан только их тип.", language) + "\n")


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


def recent_text(chat, limit=RECENT_LIMIT, language="ru"):
    messages = sorted(chat.messages, key=lambda m: (m.date, m.id))[-limit:]
    other = translate("Участник", language) if chat.kind == "group" else chat.title
    me = translate("Я", language)
    note = f"Последние сообщения: {len(messages)}, из последнего сканирования приложения."
    lines, parts = Lines(), [header(chat.title, chat.id, note, language)]
    for message in messages:
        label = translate(STORED_MEDIA.get(message.media, f"[{message.media}]"), language) if message.media else ""
        parts.append(lines.format(message.date, message.id, me if message.outgoing else other,
                                  [translate("переслано", language)] if message.forwarded else [],
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


def sender_name(message, chat, language="ru"):
    if getattr(message, "out", False):
        return translate("Я", language)
    sender = getattr(message, "sender", None)
    name = utils.get_display_name(sender) if sender else ""
    return name or (chat.title if chat.kind != "group" else translate("Участник", language))


async def full_history(client, peer, chat, total, language="ru"):
    """Oldest first. A failure can no longer change the HTTP status, so it is written into the file itself."""
    yield header(chat.title, chat.id, f"Вся история. В Telegram сообщений: {total}.", language)
    lines, written, last_id, retries = Lines(), 0, 0, 0
    while True:
        try:
            async for message in client.iter_messages(peer, limit=None, reverse=True, min_id=last_id):
                flags = [translate(flag, language) for flag, on in (("переслано", getattr(message, "fwd_from", None)),
                                               ("изменено", getattr(message, "edit_date", None) and not getattr(message, "action", None))) if on]
                reply = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
                if reply:
                    flags.append(translate("ответ на", language) + f" #{reply}")
                body = " ".join(part for part in (translate(media_label(message), language), getattr(message, "message", None) or "") if part)
                yield lines.format(message.date, message.id, sender_name(message, chat, language), flags, body)
                written, last_id = written + 1, message.id
            break
        except errors.FloodWaitError as error:
            retries += 1
            if error.seconds > FLOOD_WAIT_MAX or retries > FLOOD_RETRIES:
                yield translate(f"\n!!! ВЫГРУЗКА НЕПОЛНАЯ: Telegram запросил паузу {error.seconds} сек. Выгружено {written} из {total}. Повторите позже.\n", language)
                return
            await asyncio.sleep(error.seconds)
        except (errors.RPCError, OSError) as error:
            yield translate(f"\n!!! ВЫГРУЗКА НЕПОЛНАЯ: ошибка {type(error).__name__}. Выгружено {written} из {total}. Повторите позже.\n", language)
            return
    yield translate(f"\nВыгрузка завершена. Сообщений в файле: {written}.\n", language)
