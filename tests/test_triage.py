from datetime import timedelta

import pytest

from telegram_crm.analysis import classify
from telegram_crm.models import Annotation, Chat, Message, Settings
from telegram_crm.store import utcnow
from telegram_crm.triage import bucket_for, color_for


def verdict(texts=(), idle=500, kind="user", romantic=None, wrote=False, **fields):
    at = utcnow() - timedelta(days=idle)
    messages = [Message(id=i, date=at - timedelta(minutes=i), outgoing=wrote, text=t) for i, t in enumerate(texts, 1)]
    chat = Chat(id=fields.pop("id", "5"), title=fields.pop("title", "Тест"), kind=kind, messages=messages, **fields)
    row = {**classify(chat, Annotation(), Settings()), "written": sum(m.outgoing for m in messages)}
    return bucket_for(chat, row, romantic or {})


@pytest.mark.parametrize("kwargs,expected", [
    (dict(), "remove_empty"),
    (dict(texts=["Привет"]), "remove_cold"),
    (dict(texts=["Мой адрес такой"]), "review_old_dialogue"),  # personal data is never a first-wave removal
    (dict(texts=["Привет"], wrote=True), "remove_short_old"),
    (dict(texts=["Привет"] * 30, wrote=True), "review_old_dialogue"),
    (dict(texts=["Привет"], idle=5, wrote=True), "keep_active"),
    (dict(texts=["Привет"], id="777000"), "keep_important"),
    (dict(texts=["Привет"], title="Мама"), "keep_important"),
    (dict(texts=["Привет"], pinned=True), "keep_important"),
    (dict(texts=["Вакансия", "Вакансия"]), "review_job_archive"),
    (dict(texts=["Вакансия", "Вакансия"], idle=100), "keep_job"),
])
def test_private_chat_buckets(kwargs, expected):
    assert verdict(**kwargs) == expected


def test_romantic_chats_are_never_removed_automatically():
    invite = ["Может кофе выпьем?", "Можем прогуляться"]
    assert verdict(invite, wrote=True) == "review_romantic_possible"
    assert verdict(invite, wrote=True, romantic={"likely": {"5"}}) == "review_romantic_old"
    assert verdict(invite, idle=60, wrote=True) == "review_romantic_cooling"
    assert verdict(invite, idle=5, wrote=True) == "keep_active"
    assert verdict(invite * 15, wrote=True, romantic={"not": {"5"}}) == "review_old_dialogue"


@pytest.mark.parametrize("kwargs,expected", [
    (dict(texts=["x"], kind="bot"), "remove_bots"),
    (dict(texts=["x"], kind="bot", title="Помощник ВТБ"), "review_bots_accounts"),
    (dict(texts=["x"], kind="bot", title="BotFather", username="BotFather"), "keep_important"),
    (dict(texts=["x"], kind="channel"), "remove_feeds_dead"),
    (dict(texts=["x"], kind="channel", idle=200), "review_feeds_silent"),
    (dict(texts=["x"], kind="channel", idle=1, unread_count=500), "review_feeds_unread"),
    (dict(texts=["x"], kind="channel", idle=1), "keep_active"),
    (dict(texts=["x"], kind="channel", idle=1, title="Вакансии для PM", unread_count=5000), "keep_job"),
])
def test_bot_and_feed_buckets(kwargs, expected):
    assert verdict(**kwargs) == expected


@pytest.mark.parametrize("bucket,idle,title,expected", [
    ("keep_active", 1, "Чат", "green"),
    ("review_romantic_old", 900, "Чат", "yellow"),
    ("review_changed", 900, "Чат", "yellow"),
    ("remove_short_old", 900, "Чат", "red"),
    ("remove_cold", 900, "Чат", "red"),
    ("remove_bots", 400, "Бот", "red"),
    ("remove_bots", 800, "Бот", "crimson"),
    ("remove_bots", None, "Бот", "crimson"),
    ("remove_bots", 400, "Хентай Бот", "crimson"),
    ("remove_feeds_dead", 400, "Канал", "red"),
    ("remove_feeds_dead", 730, "Канал", "crimson"),
    ("remove_empty", None, "Чат", "crimson"),
    ("remove_deleted", 46, "Без имени", "crimson"),
])
def test_colors(bucket, idle, title, expected):
    assert color_for(bucket, {"idle_days": idle, "title": title}) == expected
