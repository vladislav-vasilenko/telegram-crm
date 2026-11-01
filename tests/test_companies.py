from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from telegram_crm.analysis import classify
from telegram_crm.companies import apply_companies, assign, folder_index, possible_companies
from telegram_crm.i18n import translate
from telegram_crm.models import Annotation, Chat, Message, Settings
from telegram_crm.server import create_app
from telegram_crm.store import utcnow

NOW = utcnow()


def chat(*texts, title="Тест", kind="user", username=None, chat_id="1"):
    return Chat(id=chat_id, title=title, kind=kind, username=username, messages=[
        Message(id=i, date=NOW - timedelta(days=1, minutes=i), outgoing=False, text=text) for i, text in enumerate(texts, 1)])


@pytest.mark.parametrize("title,username,expected", [
    ("Карьера в Сбере!", None, ["sber"]), ("GigaChat", None, ["sber"]), ("Денис (c R&D Sber) | FT", None, ["sber"]),
    ("Yandex Cup", None, ["yandex"]), ("Открытый Лекторий Яндекса", None, ["yandex"]), ("Все в ШАД!", None, ["yandex"]),
    ("Huawei Russia", None, ["huawei"]), ("Хуавей чат", None, ["huawei"]),
    ("VK Cloud", None, ["vk"]), ("Дзен для авторов", None, ["vk"]), ("Mail.ru Group", None, ["vk"]), ("Вконтакте", None, ["vk"]),
    ("Команда", "sber_team", ["sber"]),
    ("Сберегательный клуб", None, ["sber"]),  # a known cost of matching word starts; the card can correct it
    ("Василий", None, []), ("Шадрин", None, []), ("Вкусвилл", None, []), ("Дзена", None, []),
])
def test_titles_and_usernames(title, username, expected):
    assert assign(chat(title=title, username=username), Annotation(), {})[0] == expected


def test_own_folders_assign_and_name_match_is_exact():
    layout = {"folders": [{"id": "1", "title": "Сбербанк", "chats": ["1"]}, {"id": "2", "title": "Яндекс", "chats": ["2"]},
                          {"id": "3", "title": "Личные", "chats": ["3"]}, {"id": "all"}]}
    index = folder_index(layout)
    assert index == {"1": "sber", "2": "yandex"}
    assert assign(chat(), Annotation(), index) == (["sber"], [], "folder")
    assert folder_index(None) == {}


def test_mentions_only_make_a_chat_possible():
    assert assign(chat("в сбере", "сбер", "сбербанк", "ок"), Annotation(), {}) == ([], ["sber"], None)
    assert assign(chat("в сбере", "ок"), Annotation(), {}) == ([], [], None)


@pytest.mark.parametrize("text", ["пиши на vasya@mail.ru", "ссылка vk.com/club1", "напиши в вк", "user@yandex.ru", "пиши в vk"])
def test_email_addresses_and_vk_links_are_not_company_mentions(text):
    assert possible_companies(chat(text, text, text, text)) == []


def test_feeds_need_many_mentions_and_bots_are_never_possible():
    filler = ["новости"] * 80
    assert possible_companies(chat(*["сбербанк"] * 5, *filler, kind="channel")) == []
    assert possible_companies(chat(*["сбербанк"] * 20, *filler, kind="channel")) == ["sber"]
    assert possible_companies(chat(*["сбербанк"] * 20, kind="bot")) == []


def test_title_wins_over_mentions_of_another_company():
    assert assign(chat("яндекс", "яндекс", "яндекс", title="Карьера в Сбере"), Annotation(), {}) == (["sber"], [], "title")


def test_manual_choice_wins():
    assert assign(chat(title="Yandex Cup"), Annotation(company="sber"), {}) == (["sber"], [], "manual")
    assert assign(chat("сбер", "сбер", "сбер", title="Yandex Cup"), Annotation(company="none"), {}) == ([], [], None)


def row_for(item, annotation=None, folders=None):
    annotation = annotation or Annotation()
    return apply_companies(classify(item, annotation, Settings()), item, annotation, folders or {})


def test_category_replaces_the_no_label_fallback():
    row = row_for(chat(*["привет"] * 30, title="Sber AI", kind="channel"))
    assert "active" not in row["categories"] and row["categories"] == ["company_sber"] and row["primary_category"] == "company_sber"
    assert any("по названию чата" in reason for reason in row["reasons"])


def test_reasons_have_english():
    for annotation, item in ((Annotation(), chat(title="Sber AI")), (Annotation(company="vk"), chat()), (Annotation(), chat("сбер", "сбер", "сбер"))):
        for reason in row_for(item, annotation)["reasons"]:
            if "омпани" in reason:
                assert not any("а" <= char <= "я" for char in translate(reason).lower()), reason


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as test:
        test.headers["X-CRM-Token"] = test.get("/api/state").json()["token"]
        store = app.state.store
        store.upsert(chat(*["привет"] * 3, title="Yandex Cup", kind="channel", chat_id="10"))
        store.upsert(chat(*["привет"] * 3, title="Анна", chat_id="11"))
        yield test


def test_state_counts_card_and_manual_mark(client):
    state = client.get("/api/state").json()
    assert state["counts"]["company_yandex"] == 1 and state["counts"]["company_sber"] == 0
    assert client.put("/api/chats/11/annotation", json={"company": "sber"}).status_code == 200
    assert client.get("/api/state").json()["counts"]["company_sber"] == 1
    assert "company_sber" in client.get("/api/chats/11").json()["categories"]
    assert client.put("/api/chats/11/annotation", json={"company": "other"}).status_code == 422


def test_folder_plan_has_companies_but_not_the_possible_list(client):
    plan = {folder["category"]: folder for folder in client.get("/api/folders/plan").json()["folders"]}
    assert plan["company_yandex"]["title"] == "CRM Yandex" and plan["company_yandex"]["chat_ids"] == ["10"]
    assert "company_maybe" not in plan
