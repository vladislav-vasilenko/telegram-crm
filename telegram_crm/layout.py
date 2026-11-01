"""The chat list as Telegram shows it right now: order, pins, archive and the user's folders."""
from datetime import datetime, timezone

from telethon import functions, types, utils
from telethon.tl.custom import Dialog


def peer_ids(peers):
    ids = []
    for peer in peers or ():
        try:
            ids.append(str(utils.get_peer_id(peer)))
        except TypeError:  # InputPeerSelf: Saved Messages is not a CRM chat
            continue
    return ids


def dialog_kind(dialog):
    if getattr(dialog.entity, "bot", False):
        return "bot"
    if dialog.is_user:
        return "user"
    return "channel" if dialog.is_channel and not dialog.is_group else "group"


def is_muted(dialog, now):
    settings = getattr(dialog.dialog, "notify_settings", None)
    until = getattr(settings, "mute_until", None)
    if isinstance(until, datetime):
        return until.replace(tzinfo=until.tzinfo or timezone.utc) > now
    return bool(until)


def summarize(dialog, archived, now):
    message = dialog.message
    return {"id": str(dialog.id), "kind": dialog_kind(dialog), "pinned": bool(dialog.pinned), "archived": archived,
            "unread": dialog.unread_count, "unread_mark": bool(getattr(dialog.dialog, "unread_mark", False)),
            "muted": is_muted(dialog, now), "contact": bool(getattr(dialog.entity, "contact", False)),
            "last_text": (getattr(message, "message", "") or "")[:180],
            "last_at": message.date.isoformat() if getattr(message, "date", None) else None,
            "last_outgoing": bool(getattr(message, "out", False))}


def folder_chats(telegram_filter, dialogs):
    """Telegram's folder rule: pins and included chats always, excluded never, then the exclusions and type flags."""
    pinned = peer_ids(telegram_filter.pinned_peers)
    included = set(pinned) | set(peer_ids(telegram_filter.include_peers))
    excluded = set(peer_ids(getattr(telegram_filter, "exclude_peers", ())))
    get = lambda name: bool(getattr(telegram_filter, name, False))

    def belongs(row):
        if row["id"] in included:
            return True
        if row["id"] in excluded:
            return False
        if (get("exclude_muted") and row["muted"]) or (get("exclude_archived") and row["archived"]):
            return False
        if get("exclude_read") and not (row["unread"] or row["unread_mark"]):
            return False
        return {"bot": get("bots"), "group": get("groups"), "channel": get("broadcasts"),
                "user": get("contacts") if row["contact"] else get("non_contacts")}[row["kind"]]

    known = {row["id"] for row in dialogs}
    first = [chat_id for chat_id in pinned if chat_id in known]
    rest = [row["id"] for row in dialogs if belongs(row) and row["id"] not in set(first)]
    return first, first + rest


async def with_all_pins(client, dialogs):
    """Main-list dialogs with pins in Telegram's order. The regular dialog list can omit a pinned chat."""
    answer = await client(functions.messages.GetPinnedDialogsRequest(folder_id=0))
    entities = {utils.get_peer_id(entity): entity for entity in [*answer.users, *answer.chats]
                if not isinstance(entity, (types.UserEmpty, types.ChatEmpty))}
    messages = {}
    for message in answer.messages:
        message._finish_init(client, entities, None)
        messages[(utils.get_peer_id(message.peer_id), message.id)] = message
    known = {dialog.id: dialog for dialog in dialogs}
    pinned = []
    for item in answer.dialogs:
        if not isinstance(item, types.Dialog):
            continue
        peer_id = utils.get_peer_id(item.peer)
        if peer_id in known:
            pinned.append(known[peer_id])
        elif peer_id in entities:
            pinned.append(Dialog(client, item, entities, messages.get((peer_id, item.top_message))))
    first = {dialog.id for dialog in pinned}
    return pinned + [dialog for dialog in dialogs if dialog.id not in first]


async def fetch_layout(client, now=None):
    now = now or datetime.now(timezone.utc)
    main = await with_all_pins(client, [dialog async for dialog in client.iter_dialogs(folder=0, ignore_migrated=True)])
    seen, dialogs = {dialog.id for dialog in main}, []
    dialogs = [summarize(dialog, False, now) for dialog in main if not getattr(dialog.entity, "is_self", False)]
    async for dialog in client.iter_dialogs(folder=1, ignore_migrated=True):
        # Asking for the archive also returns the main list's pins, so a chat counts once.
        if dialog.id not in seen and not getattr(dialog.entity, "is_self", False):
            seen.add(dialog.id)
            dialogs.append(summarize(dialog, True, now))
    response = await client(functions.messages.GetDialogFiltersRequest())
    folders = []
    for item in response.filters:
        if isinstance(item, types.DialogFilterDefault):
            folders.append({"id": "all"})
        elif isinstance(item, (types.DialogFilter, types.DialogFilterChatlist)):
            pinned, chats = folder_chats(item, dialogs)
            folders.append({"id": str(item.id), "title": getattr(item.title, "text", item.title),
                            "emoticon": item.emoticon, "pinned": pinned, "chats": chats})
    if not any(folder["id"] == "all" for folder in folders):
        folders.insert(0, {"id": "all"})
    return {"dialogs": dialogs, "folders": folders, "fetched_at": now.isoformat()}
