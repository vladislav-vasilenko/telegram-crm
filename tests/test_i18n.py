import csv
import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from telegram_crm.analysis import CATEGORIES, classify
from telegram_crm.chat_export import recent_text
from telegram_crm.i18n import translate
from telegram_crm.llm import PROMPT_TEXT, SYSTEM_PROMPT, SYSTEM_PROMPT_EN, system_prompt
from telegram_crm.models import Annotation, Chat, Message, Settings
from telegram_crm.server import create_app
from telegram_crm.store import utcnow
from telegram_crm.triage import BUCKETS, GROUPS

CYRILLIC = re.compile("[А-Яа-яЁё]")
STATIC = Path(__file__).parent.parent / "telegram_crm" / "static"


@pytest.mark.parametrize("text", [
    *(name for name in CATEGORIES.values() if CYRILLIC.search(name)), *GROUPS.values(), *(item for bucket in BUCKETS.values() for item in bucket[1:]),
    "Telegram запросил паузу 30 сек. Попробуйте позже.", "Подключите Telegram в веб-панели", "Обновление: чат 3 из 10",
    "Полное обновление: чат 3 из 10", "Обновлено чатов: 1964, запрошено у Telegram: 2", "Нет сообщений более 90 дней",
    "Последнее сообщение старше 5 календарных месяцев", "Нет сообщений более 2 лет", "Ультракороткий чат: всего 4 сообщ. за всю историю",
    "Частое взаимное общение: 25 сообщений за 30 дней", "Спам: 25 входящих сообщений подряд без вашего ответа",
    "Все 6", "Написать 2", "Подождать 1", "Закрыть тему 1", "Неясно 1", "Без анализа 1", "Проверить на 5 чатах",
    "Знакомство: 40 дн. без отмеченного прогресса", "Знакомство: переписка неактивна 40 дн.; развитие требует проверки",
])
def test_server_text_has_english(text):
    english = translate(text)
    assert english != text and not CYRILLIC.search(english)


def test_russian_stays_unchanged():
    assert translate("Обновление: чат 3 из 10", "ru") == "Обновление: чат 3 из 10"


def test_quoted_chat_titles_are_kept():
    assert "«Работа»" in translate("Очистка 1 из 3: копия «Работа»")


def test_every_reason_from_a_real_classification_is_english():
    now = utcnow()
    chat = Chat(id="1", title="Тест", history_status="limited", telegram_scam=True,
                messages=[Message(id=1, date=now.replace(year=now.year - 3), outgoing=False, text="вакансия вакансия")])
    for reason in classify(chat, Annotation(dating=True), Settings())["reasons"]:
        assert not CYRILLIC.search(translate(re.sub(r"Работа: .*", "Работа: ", reason))), reason


def test_chat_content_is_not_translated_in_exports():
    message = Message(id=1, date=utcnow(), outgoing=True, text="Спам и Работа")
    text = recent_text(Chat(id="1", title="Спам", messages=[message]), language="en")
    assert "Me: Спам и Работа" in text and "Latest messages: 1" in text and "Moscow time" in text


def test_english_prompt_has_no_russian_outside_the_chats():
    assert not CYRILLIC.search(SYSTEM_PROMPT_EN) and system_prompt("en") == SYSTEM_PROMPT_EN and system_prompt("ru") == SYSTEM_PROMPT
    assert not any(CYRILLIC.search(value) for value in PROMPT_TEXT["en"].values())


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as test:
        test.headers["X-CRM-Token"] = test.get("/api/state").json()["token"]
        test.post("/api/demo")
        settings = test.get("/api/state").json()["settings"]
        assert settings["language"] == "ru"
        assert test.put("/api/settings", json={**settings, "language": "en"}).status_code == 200
        yield test


def test_llm_batch_is_english_apart_from_the_messages(client):
    prompt = client.post("/api/batches/export", json={"chat_ids": ["demo_1"]}).json()["prompt"]
    assert prompt.startswith("Analyze Telegram chats") and "Reply schema:" in prompt and "Data:" in prompt
    assert '"speaker": "Me"' in prompt and '"title": "Chat 1"' in prompt
    assert "Схема ответа" not in prompt and "Отвечай" not in prompt and "Собеседник" not in prompt


def test_csv_and_export_follow_the_language(client):
    rows = list(csv.reader(io.StringIO(client.get("/api/export.csv").text.lstrip("\ufeff"))))
    assert rows[0][0] == "Chat" and not any(CYRILLIC.search(cell) for cell in rows[0])
    assert all(not CYRILLIC.search(row[1]) for row in rows[1:])
    # The keywords after "Jobs:" are words found in the chat, so they stay as written.
    assert all(not CYRILLIC.search(re.sub(r"Jobs: [^;]*", "", row[5])) for row in rows[1:])
    answer = client.get("/api/chats/demo_1/export")
    assert "last%20100" in answer.headers["content-disposition"] and "Moscow time" in answer.text


def test_folder_names_follow_the_language(client):
    assert all(not CYRILLIC.search(folder["title"]) for folder in client.get("/api/folders/plan").json()["folders"])


def test_invalid_language_is_refused(client):
    settings = client.get("/api/state").json()["settings"]
    assert client.put("/api/settings", json={**settings, "language": "de"}).status_code == 422


def test_translation_never_rewrites_an_unchanged_attribute():
    # Writing the same value would trigger the observer again and freeze the page.
    assert "if (next !== value) node.setAttribute" in (STATIC / "i18n.js").read_text()
