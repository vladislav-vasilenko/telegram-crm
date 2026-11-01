"""Search the whole Telegram history through MTProto and group the matches by chat.

Telegram matches a query literally, so "встреча на вднх" finds little. Each meaningful word is searched
on its own as well, and the results are ranked by how many of the words a message and a chat contain.
"""
import re

from telethon import errors, functions, types, utils

STOP_WORDS = {"а", "в", "во", "где", "для", "до", "за", "и", "из", "к", "как", "когда", "ко", "мы", "на", "но", "о", "об",
              "обсуждали", "от", "по", "про", "с", "со", "у", "что", "я", "ты", "the", "a", "an", "in", "on", "at", "of", "to"}
PAGE_SIZE = 100
PAGES_PER_WORD = 3
MAX_WORDS = 5
MAX_CHATS = 60
MATCHES_PER_CHAT = 10
WORD = re.compile(r"[\w+#.-]*\w", re.UNICODE)


def normalize(text):
    return (text or "").lower().replace("ё", "е")


def query_words(query):
    words = list(dict.fromkeys(WORD.findall(normalize(query))))
    return ([word for word in words if word not in STOP_WORDS] or words)[:MAX_WORDS]


def stem(word):
    """A rough prefix, so "встреча", "встречу" and "встретиться" all match. Short words stay whole."""
    return word if len(word) <= 4 else word[:max(4, len(word) - 2)]


def stem_pattern(stems):
    return [re.compile(rf"(?<!\w){re.escape(item)}\w*", re.UNICODE) for item in stems]


def snippet(text, patterns):
    normalized = normalize(text)
    found = [match.start() for pattern in patterns if (match := pattern.search(normalized))]
    start = max(0, min(found) - 80) if found else 0
    part = " ".join(text[start:start + 240].split())
    return ("…" if start else "") + part + ("…" if start + 240 < len(text) else "")


def rank(found, rows, stems, private_only):
    """found: {(chat_id, message_id): message}. rows: known chats. Best chats first."""
    patterns = stem_pattern(stems)
    chats, skipped = {}, 0
    for (chat_id, _), message in found.items():
        row = rows.get(chat_id)
        if row is None:
            skipped += 1
            continue
        if private_only and row.kind != "user":
            continue
        normalized = normalize(message.message)
        words = {index for index, pattern in enumerate(patterns) if pattern.search(normalized)}
        if not words:
            continue
        chats.setdefault(chat_id, []).append((len(words), message.date, message, words))
    results = []
    for chat_id, matches in chats.items():
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        covered = set().union(*(item[3] for item in matches))
        results.append((len(covered), matches[0][0], max(item[1] for item in matches), chat_id, matches))
    results.sort(key=lambda item: item[:3], reverse=True)
    output = []
    for covered, best, _, chat_id, matches in results[:MAX_CHATS]:
        output.append({"chat_id": chat_id, "covered": covered, "best": best, "total": len(matches), "matches": [
            {"id": message.id, "date": date.isoformat(), "outgoing": bool(message.out), "snippet": snippet(message.message, patterns), "words": count}
            for count, date, message, _ in matches[:MATCHES_PER_CHAT]]})
    return output, skipped


async def search_word(client, text, private_only, found):
    """One Telegram query, up to a few pages. Pagination follows what Telegram returns."""
    rate, peer, offset = 0, types.InputPeerEmpty(), 0
    for _ in range(PAGES_PER_WORD):
        answer = await client(functions.messages.SearchGlobalRequest(
            q=text, filter=types.InputMessagesFilterEmpty(), min_date=None, max_date=None, offset_rate=rate,
            offset_peer=peer, offset_id=offset, limit=PAGE_SIZE, users_only=private_only or None))
        messages = [message for message in answer.messages if isinstance(message, types.Message)]
        for message in messages:
            found[(str(utils.get_peer_id(message.peer_id)), message.id)] = message
        if not messages or not isinstance(answer, types.messages.MessagesSlice) or len(answer.messages) < PAGE_SIZE:
            return
        entities = {utils.get_peer_id(entity): entity for entity in [*answer.users, *answer.chats]}
        last = messages[-1]
        rate, offset = answer.next_rate or 0, last.id
        peer = utils.get_input_peer(entities[utils.get_peer_id(last.peer_id)])


async def search_history(client, query, rows, private_only=True):
    words = query_words(query)
    stems = [stem(word) for word in words]
    found, partial = {}, False
    queries = list(dict.fromkeys([" ".join(words), *words])) if words else []
    for text in queries:
        try:
            await search_word(client, text, private_only, found)
        except errors.FloodWaitError:
            partial = True  # keep what Telegram already returned, and say the list may be incomplete
            break
    results, skipped = rank(found, rows, stems, private_only)
    return {"query": query, "stems": stems, "results": results, "partial": partial, "not_in_crm": skipped}


async def history_window(client, peer, convert, around=None, before=None, after=None, limit=40):
    """A slice of a chat straight from Telegram, oldest first, for reading past the loaded messages."""
    if around is not None:
        answer = await client.get_messages(peer, limit=limit, offset_id=around, add_offset=-limit // 2)
    elif after is not None:
        answer = await client.get_messages(peer, limit=limit, min_id=after, reverse=True)
    else:
        answer = await client.get_messages(peer, limit=limit, offset_id=before or 0)
    messages = sorted((message for message in answer if isinstance(message, types.Message)), key=lambda message: message.id)
    return [convert(message).model_dump(mode="json") for message in messages]
