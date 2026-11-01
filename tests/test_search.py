import asyncio
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from telethon import errors, types

from telegram_crm.models import Chat, Message
from telegram_crm.search import history_window, query_words, rank, search_history, snippet, stem, stem_pattern
from telegram_crm.server import create_app
from telegram_crm.store import utcnow

NOW = utcnow()


def message(message_id, chat_id, text, days=0, out=False):
    return types.Message(id=message_id, peer_id=types.PeerUser(chat_id), date=NOW - timedelta(days=days), message=text, out=out)


ROWS = {"1": Chat(id="1", title="Анна"), "2": Chat(id="2", title="Иван"), "3": Chat(id="3", title="Канал", kind="channel")}


class Searcher:
    def __init__(self, by_query, flood_on=None):
        self.by_query, self.flood_on, self.requests = by_query, flood_on, []

    async def __call__(self, request):
        self.requests.append(request)
        if request.q == self.flood_on:
            error = errors.FloodWaitError(request=None, capture=30)
            raise error
        return types.messages.Messages(messages=self.by_query.get(request.q, []), topics=[], chats=[], users=[])


def test_words_drop_filler_and_stems_cover_word_forms():
    assert query_words("где мы обсуждали встречу на ВДНХ") == ["встречу", "вднх"]
    assert query_words("на в") == ["на", "в"]  # nothing but filler: keep what was asked
    pattern = stem_pattern([stem("встреча")])[0]
    assert all(pattern.search(word) for word in ("встречу", "встретиться", "встречались"))
    assert not pattern.search("повстречались")
    assert stem("вднх") == "вднх"


def test_ё_matches_е():
    assert query_words("Ёлка") == ["елка"]


def test_chats_with_all_words_rank_first_even_across_messages():
    found = {("1", 1): message(1, 1, "вднх", days=1), ("2", 2): message(2, 2, "встретимся завтра"), ("2", 3): message(3, 2, "там на вднх", days=5),
             ("1", 4): message(4, 1, "вднх снова")}
    results, skipped = rank(found, ROWS, ["встре", "вднх"], True)
    assert [item["chat_id"] for item in results] == ["2", "1"] and results[0]["covered"] == 2 and skipped == 0


def test_message_with_all_words_beats_split_matches():
    found = {("1", 1): message(1, 1, "встреча"), ("1", 2): message(2, 1, "вднх"), ("2", 3): message(3, 2, "встреча на вднх", days=40)}
    results, _ = rank(found, ROWS, ["встре", "вднх"], True)
    assert results[0]["chat_id"] == "2" and results[0]["best"] == 2


def test_private_only_and_unknown_chats():
    found = {("3", 1): message(1, 3, "вднх"), ("9", 2): message(2, 9, "вднх"), ("1", 3): message(3, 1, "вднх")}
    results, skipped = rank(found, ROWS, ["вднх"], True)
    assert [item["chat_id"] for item in results] == ["1"] and skipped == 1
    assert {item["chat_id"] for item in rank(found, ROWS, ["вднх"], False)[0]} == {"1", "3"}


def test_snippet_shows_the_match_in_a_long_message():
    text = "x " * 200 + "встреча на ВДНХ" + " y" * 200
    assert "встреча на ВДНХ" in snippet(text, stem_pattern(["вднх"])) and snippet(text, stem_pattern(["вднх"])).startswith("…")


def test_search_asks_phrase_and_each_word_private_only():
    client = Searcher({"вднх": [message(1, 1, "гуляем на вднх")], "встречу вднх": [message(2, 2, "встречу на вднх")]})
    answer = asyncio.run(search_history(client, "встречу на вднх", ROWS))
    assert [request.q for request in client.requests] == ["встречу вднх", "встречу", "вднх"]
    assert all(request.users_only for request in client.requests)
    assert answer["results"][0]["chat_id"] == "2" and answer["partial"] is False


def test_flood_wait_keeps_earlier_results_and_says_partial():
    client = Searcher({"встречу вднх": [message(2, 2, "встречу на вднх")]}, flood_on="встречу")
    answer = asyncio.run(search_history(client, "встречу на вднх", ROWS))
    assert answer["partial"] is True and answer["results"][0]["chat_id"] == "2"


def test_pagination_continues_from_the_last_message():
    class Pages(Searcher):
        async def __call__(self, request):
            self.requests.append(request)
            if request.offset_id:
                return types.messages.Messages(messages=[message(1, 1, "вднх")], topics=[], chats=[], users=[])
            page = [message(i, 1, "вднх") for i in range(200, 100, -1)]
            return types.messages.MessagesSlice(count=101, messages=page, topics=[], chats=[], users=[types.User(id=1, access_hash=7)], next_rate=55)
    client = Pages({})
    answer = asyncio.run(search_history(client, "вднх", ROWS))
    assert client.requests[1].offset_rate == 55 and client.requests[1].offset_id == 101
    assert client.requests[1].offset_peer.user_id == 1 and answer["results"][0]["total"] == 101


def test_history_window_requests():
    calls = []

    class Client:
        async def get_messages(self, peer, **kwargs):
            calls.append(kwargs)
            return [message(5, 1, "б"), message(4, 1, "а"), SimpleNamespace(id=3)]
    convert = lambda m: Message(id=m.id, date=m.date, outgoing=bool(m.out), text=m.message)
    rows = asyncio.run(history_window(Client(), 1, convert, around=5))
    assert [row["id"] for row in rows] == [4, 5]  # oldest first, service messages skipped
    assert calls[0] == {"limit": 40, "offset_id": 5, "add_offset": -20}
    asyncio.run(history_window(Client(), 1, convert, before=9))
    asyncio.run(history_window(Client(), 1, convert, after=9))
    assert calls[1] == {"limit": 40, "offset_id": 9} and calls[2] == {"limit": 40, "min_id": 9, "reverse": True}


def test_endpoints_guard_and_media_asks_to_retry_while_connecting(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as client:
        app.state.store.upsert(Chat(id="1", title="Анна"))
        assert client.get("/api/telegram/search", params={"q": "вднх"}).status_code == 400  # not connected
        assert client.get("/api/telegram/search", params={"q": "в"}).status_code == 400
        assert client.get("/api/chats/1/history", params={"around": 5}).status_code == 400
        assert client.get("/api/chats/404/history").status_code == 404
        assert client.get("/api/chats/1/media", params={"ids": "1"}).json() == {"items": {}, "retry": True}
        assert client.get("/api/chats/1/history", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
