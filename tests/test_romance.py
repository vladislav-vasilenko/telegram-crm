import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from telegram_crm.models import Annotation, Chat, Message, RomanceBatchResult, RomanceChatResult
from telegram_crm.romance import PROMPT_EN, PROMPT_RU, analyze_romance, apply_safeguards, extend_chat, unanswered_in_a_row
from telegram_crm.server import create_app
from telegram_crm.store import utcnow

NOW = utcnow()


def message(i, outgoing, text="текст"):
    return Message(id=i, date=NOW - timedelta(days=30, minutes=-i), outgoing=outgoing, text=text)


def verdict(**fields):
    base = dict(chat_id="chat_1", promise=70, mutual_interest="high", ending="they_stopped", recommendation="write",
                confidence=0.8, reason="Оценка по фрагменту.", opener="Как прошла поездка?", evidence_message_ids=[])
    return RomanceChatResult(**{**base, **fields})


def chat_with(*outgoing):
    return Chat(id="5", title="Анна", last_message_id=len(outgoing), messages=[message(i, out) for i, out in enumerate(outgoing, 1)])


def test_unanswered_counts_only_the_tail():
    assert unanswered_in_a_row(chat_with(True, False, True, True).messages) == 2
    assert unanswered_in_a_row(chat_with(True, False).messages) == 0


def test_refusal_always_means_let_it_go():
    value, held = apply_safeguards(verdict(ending="declined"), chat_with(False))
    assert value["recommendation"] == "close" and value["opener"] == "" and held == "declined"


def test_three_unanswered_messages_stop_the_advice_to_write():
    value, held = apply_safeguards(verdict(), chat_with(False, True, True, True))
    assert value["recommendation"] == "wait" and value["opener"] == "" and held == "unanswered"
    value, held = apply_safeguards(verdict(), chat_with(False, True, True))
    assert value["recommendation"] == "write" and value["opener"] and held is None


def test_opener_only_comes_with_the_advice_to_write():
    value, held = apply_safeguards(verdict(recommendation="wait"), chat_with(False))
    assert value["opener"] == "" and held is None


def test_facts_about_the_ending_are_counted_for_the_model():
    item = {}
    extend_chat(item, chat_with(False, True, True), now=NOW)
    assert item["last_message_from"] == "me" and item["my_unanswered_in_a_row"] == 2
    assert item["my_messages_loaded"] == 2 and item["their_messages_loaded"] == 1 and item["days_since_last_message"] >= 29


def test_prompts_carry_the_rules_in_each_language():
    for prompt in (PROMPT_RU, PROMPT_EN):
        assert "declined" in prompt and "my_unanswered_in_a_row" in prompt and "opener" in prompt and "18" in prompt
    assert not any("а" <= char <= "я" for char in PROMPT_EN.lower())


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as test:
        test.headers["X-CRM-Token"] = test.get("/api/state").json()["token"]
        test.post("/api/demo")
        yield test


def row_of(client, chat_id):
    return next(row for row in client.get("/api/state").json()["chats"] if row["id"] == chat_id)


def answer(chat_id="chat_1", **fields):
    return {"results": [verdict(chat_id=chat_id, **fields).model_dump()]}


def test_candidates_follow_marks_and_never_include_groups(client):
    assert row_of(client, "demo_3")["romance_candidate"]  # marked as an acquaintance
    assert not row_of(client, "demo_2")["romance_candidate"]  # a group
    client.put("/api/chats/demo_3/annotation", json={"dating": False})
    assert not row_of(client, "demo_3")["romance_candidate"]


def test_export_is_romance_specific_and_import_saves_with_safeguards(client):
    exported = client.post("/api/romance/export", json={"chat_ids": ["demo_3"]}).json()
    prompt = exported["prompt"]
    assert "promise" in prompt and "my_unanswered_in_a_row" in prompt and "importance (important" not in prompt
    saved = client.post("/api/romance/import", json={"batch_id": exported["batch_id"], "result": answer(ending="declined")})
    assert saved.json() == {"imported": 1}
    romance = row_of(client, "demo_3")["romance"]
    assert romance["result"]["recommendation"] == "close" and romance["held_back"] == "declined" and not romance["stale"]
    assert client.get("/api/chats/demo_3").json()["romance"]["result"]["opener"] == ""


def test_import_checks_the_answer_before_saving_anything(client):
    batch = client.post("/api/romance/export", json={"chat_ids": ["demo_3"]}).json()["batch_id"]
    assert client.post("/api/romance/import", json={"batch_id": batch, "result": answer("chat_9")}).status_code == 400
    assert client.post("/api/romance/import", json={"batch_id": batch, "result": answer(evidence_message_ids=[99999])}).status_code == 400
    assert client.post("/api/romance/import", json={"batch_id": batch, "result": {"results": [{"chat_id": "chat_1"}]}}).status_code == 422
    assert row_of(client, "demo_3")["romance"] is None


def test_new_messages_mark_the_analysis_stale(client):
    batch = client.post("/api/romance/export", json={"chat_ids": ["demo_3"]}).json()["batch_id"]
    client.post("/api/romance/import", json={"batch_id": batch, "result": answer()})
    store = client.app.state.store
    chat = store.get("demo_3")["chat"]
    chat.messages.append(Message(id=500, date=utcnow(), outgoing=False, text="Привет"))
    store.upsert(chat)
    assert row_of(client, "demo_3")["romance"]["stale"]


def test_english_mode_sends_the_english_prompt(client):
    settings = client.get("/api/state").json()["settings"]
    client.put("/api/settings", json={**settings, "language": "en"})
    prompt = client.post("/api/romance/export", json={"chat_ids": ["demo_3"]}).json()["prompt"]
    assert prompt.startswith("Help a person review") and "Reply schema:" in prompt


def test_estimate_and_analyze_needs_a_key(client):
    estimate = client.post("/api/romance/estimate", json={"chat_ids": ["demo_3"]}).json()
    assert estimate["chats"] == 1 and estimate["messages"] > 0
    assert client.post("/api/romance/analyze", json={"chat_ids": ["demo_3"]}).status_code == 400


def test_api_analysis_uses_the_romance_schema(client, monkeypatch):
    calls = []

    @asynccontextmanager
    async def backend(key, model):
        async def call(system, user, max_tokens, schema):
            calls.append((system, schema))
            return RomanceBatchResult(results=[verdict()])
        yield call
    monkeypatch.setattr("telegram_crm.llm.backend_for", lambda model: backend)
    store = client.app.state.store
    count = asyncio.run(analyze_romance(store, type("R", (), {"chat_ids": ["demo_3"], "redact_contacts": True})(), "key", "gpt-test"))
    assert count == 1 and calls[0][0] == PROMPT_RU and calls[0][1] is RomanceBatchResult
    assert store.metadata("romance")["demo_3"]["source"] == "OpenAI / gpt-test"
