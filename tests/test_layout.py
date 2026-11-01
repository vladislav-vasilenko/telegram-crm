from types import SimpleNamespace

from fastapi.testclient import TestClient
from telethon import types

from telegram_crm.layout import fetch_layout, folder_chats
from telegram_crm.server import create_app


def row(chat_id, kind="user", contact=False, muted=False, unread=0, archived=False):
    return {"id": str(chat_id), "kind": kind, "contact": contact, "muted": muted, "unread": unread,
            "unread_mark": False, "archived": archived}


def user(chat_id):
    return types.InputPeerUser(chat_id, 1)


def make(pinned=(), include=(), exclude=(), **flags):
    return types.DialogFilter(id=2, title=types.TextWithEntities("Папка", []), pinned_peers=[user(i) for i in pinned],
                              include_peers=[user(i) for i in include], exclude_peers=[user(i) for i in exclude], **flags)


def test_folder_keeps_pins_first_then_telegram_order():
    dialogs = [row(1), row(2), row(3), row(4)]
    pinned, chats = folder_chats(make(pinned=[3], include=[1, 4]), dialogs)
    assert pinned == ["3"] and chats == ["3", "1", "4"]


def test_folder_ignores_pins_it_cannot_show():
    assert folder_chats(make(pinned=[9], include=[1]), [row(1)]) == ([], ["1"])


def test_type_flags_and_exclusions():
    dialogs = [row(1, contact=True), row(2), row(3, kind="bot"), row(4, contact=True, muted=True), row(5, contact=True, archived=True)]
    assert folder_chats(make(contacts=True), dialogs)[1] == ["1", "4", "5"]
    assert folder_chats(make(non_contacts=True, bots=True), dialogs)[1] == ["2", "3"]
    assert folder_chats(make(contacts=True, exclude_muted=True, exclude_archived=True), dialogs)[1] == ["1"]
    assert folder_chats(make(contacts=True, exclude=[1]), dialogs)[1] == ["4", "5"]


def test_explicit_include_beats_exclusion_flags():
    assert folder_chats(make(include=[4], exclude_muted=True), [row(4, muted=True)])[1] == ["4"]


def test_exclude_read_keeps_unread_only():
    dialogs = [row(1, contact=True), row(2, contact=True, unread=3)]
    assert folder_chats(make(contacts=True, exclude_read=True), dialogs)[1] == ["2"]


def test_shared_folder_has_no_exclude_list():
    shared = types.DialogFilterChatlist(id=3, title=types.TextWithEntities("Общая", []), pinned_peers=[], include_peers=[user(1)])
    assert folder_chats(shared, [row(1), row(2)])[1] == ["1"]


def test_endpoint_saves_and_falls_back(tmp_path, monkeypatch):
    import telegram_crm.server as server
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as client:
        assert client.get("/api/telegram/layout").json() == {"dialogs": [], "folders": []}  # not connected: no request
        app.state.telegram.authorized = True
        calls = []

        async def get_client():
            return SimpleNamespace()

        async def fetch(telegram_client):
            calls.append(1)
            return {"dialogs": [{"id": "1"}], "folders": [{"id": "all"}], "fetched_at": server.utcnow().isoformat()}
        app.state.telegram.get_client = get_client
        monkeypatch.setattr(server, "fetch_layout", fetch)
        assert client.get("/api/telegram/layout").json()["dialogs"] == [{"id": "1"}]
        client.get("/api/telegram/layout")
        assert len(calls) == 1  # answered from the saved copy for a while
        assert client.get("/api/telegram/layout", params={"cached": "true"}).json()["dialogs"] == [{"id": "1"}]

        async def broken(telegram_client):
            raise OSError
        monkeypatch.setattr(server, "fetch_layout", broken)
        monkeypatch.setattr(server, "LAYOUT_FRESH_SECONDS", 0)
        answer = client.get("/api/telegram/layout").json()
        assert answer["stale"] is True and answer["dialogs"] == [{"id": "1"}]


def test_main_pins_returned_with_the_archive_are_not_archived():
    import asyncio

    def dialog(chat_id, pinned=False):
        return SimpleNamespace(id=chat_id, entity=SimpleNamespace(), pinned=pinned, is_user=True, is_channel=False, is_group=False,
                               unread_count=0, dialog=SimpleNamespace(), message=None)

    class Client:
        def iter_dialogs(self, folder, ignore_migrated):
            async def rows():
                for item in ([dialog(1, True), dialog(2)] if folder == 0 else [dialog(1, True), dialog(3)]):
                    yield item
            return rows()

        async def __call__(self, request):
            return SimpleNamespace(filters=[], dialogs=[types.Dialog(peer=types.PeerUser(9), top_message=0, read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0, unread_mentions_count=0, unread_reactions_count=0, unread_poll_votes_count=0, notify_settings=types.PeerNotifySettings(), pinned=True),
                                                        types.Dialog(peer=types.PeerUser(1), top_message=0, read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0, unread_mentions_count=0, unread_reactions_count=0, unread_poll_votes_count=0, notify_settings=types.PeerNotifySettings(), pinned=True)],
                                   users=[types.User(id=9, access_hash=1, first_name="Девять")], chats=[], messages=[])

    result = asyncio.run(fetch_layout(Client()))
    assert [(d["id"], d["archived"]) for d in result["dialogs"]] == [("9", False), ("1", False), ("2", False), ("3", True)]  # a pin the list omitted comes back, in pin order
