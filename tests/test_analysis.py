from datetime import datetime, timedelta, timezone

import pytest

from telegram_crm.analysis import classify, fingerprint, subtract_months
from telegram_crm.models import Annotation, Chat, Message, Settings

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def chat_at(at, text="Привет", outgoing=False, **kwargs):
    return Chat(id="123", title="Тест", messages=[Message(id=1, date=at, text=text, outgoing=outgoing)], **kwargs)


def analyze(chat, annotation=None, llm=None):
    return classify(chat, annotation or Annotation(), Settings(), llm, NOW)


def test_calendar_months_clamp_and_leap_year():
    assert subtract_months(NOW, 5).isoformat() == "2026-05-31T12:00:00+00:00"
    assert subtract_months(NOW, 8).day == 28
    leap = datetime(2024, 2, 29, tzinfo=timezone.utc)
    assert subtract_months(leap, 24).day == 28


@pytest.mark.parametrize("days,expected", [(20, []), (21, []), (22, ["stalled"]), (91, ["inactive"]), (180, ["very_old"]), (800, ["dead"])])
def test_age_categories(days, expected):
    row = analyze(chat_at(NOW - timedelta(days=days)), Annotation(dating=True))
    for category in expected:
        assert category in row["categories"]
    if days <= 21:
        assert "stalled" not in row["categories"]


def test_old_boundary_is_calendar_months():
    boundary = subtract_months(NOW, 5)
    assert "very_old" not in analyze(chat_at(boundary))["categories"]
    assert "very_old" in analyze(chat_at(boundary - timedelta(seconds=1)))["categories"]


def test_dead_boundary_is_two_calendar_years():
    boundary = subtract_months(NOW, 24)
    assert "dead" not in analyze(chat_at(boundary))["categories"]
    assert "dead" in analyze(chat_at(boundary - timedelta(seconds=1)))["categories"]


def test_frequent_mutual_conversation_overrides_pin_in_score():
    messages = [Message(id=i, date=NOW-timedelta(hours=i), outgoing=i % 2 == 0, text="Привет") for i in range(24)]
    frequent = analyze(Chat(id="1", title="Частый", messages=messages))
    pinned = analyze(chat_at(NOW-timedelta(days=200), pinned=True))
    assert "important" in frequent["categories"]
    assert frequent["priority_score"] > pinned["priority_score"]
    assert "important" not in analyze(Chat(id="1",title="Канал",messages=[m.model_copy(update={"outgoing":False}) for m in messages]))["categories"]


def test_manual_importance_and_dating_override():
    chat = chat_at(NOW-timedelta(days=30), "Я рада познакомиться", pinned=True)
    row = analyze(chat, Annotation(important=False, dating=False))
    assert "important" not in row["categories"]
    assert "stalled" not in row["categories"]


def test_ongoing_conversation_with_old_manual_progress_is_stalled():
    row = analyze(chat_at(NOW), Annotation(dating=True,last_progress_at=NOW-timedelta(days=40)))
    assert "stalled" in row["categories"]


@pytest.mark.parametrize("stage", ["met", "kissed", "sex_protected", "sex_unprotected", "regular_partner", "relationship", "closed"])
def test_developed_relationship_not_stalled(stage):
    row=analyze(chat_at(NOW-timedelta(days=40)), Annotation(dating=True,stage=stage))
    assert "stalled" not in row["categories"]


def test_name_does_not_infer_dating_or_gender():
    row=analyze(Chat(id="1",title="Анна",messages=[Message(id=1,date=NOW-timedelta(days=30),outgoing=False,text="Готов договор")]))
    assert not row["dating_candidate"]
    assert "stalled" not in row["categories"]


def test_missing_history_has_no_age_categories():
    row=analyze(Chat(id="1",title="Пустой",history_status="unavailable"))
    assert row["idle_days"] is None
    assert not set(row["categories"]) & {"inactive","dead","very_old"}


def test_only_incoming_nonforwarded_scam_signals():
    suspicious="Пришли код входа из Telegram"
    assert "suspected_scam" in analyze(chat_at(NOW,suspicious))["categories"]
    assert "suspected_scam" not in analyze(chat_at(NOW,suspicious,outgoing=True))["categories"]
    chat=chat_at(NOW,suspicious)
    chat.messages[0].forwarded=True
    assert "suspected_scam" not in analyze(chat)["categories"]


def test_warning_and_trusted_review_suppress_heuristics_but_not_official_flag():
    assert "suspected_scam" not in analyze(chat_at(NOW,"Никому не отправляй код входа из Telegram"))["categories"]
    chat=chat_at(NOW,"Пришли код входа из Telegram")
    assert "suspected_scam" not in analyze(chat,Annotation(risk_review="trusted"))["categories"]
    chat.telegram_scam=True
    assert "suspected_scam" in analyze(chat,Annotation(risk_review="trusted"))["categories"]


def test_fake_telegram_domain_and_official_domain():
    assert "suspected_scam" in analyze(chat_at(NOW,"https://telegram-login.example"))["categories"]
    assert "suspected_scam" not in analyze(chat_at(NOW,"https://telegram.org/faq"))["categories"]


def test_stale_llm_does_not_change_classification():
    chat=chat_at(NOW)
    result={"fingerprint":fingerprint(chat),"result":{"importance":"important","dating":"no","scam_risk":"low","confidence":0.9}}
    assert "important" in analyze(chat,llm=result)["categories"]
    chat.messages[0].text="Изменение"
    row=analyze(chat,llm=result)
    assert row["llm_stale"]
    assert "important" not in row["categories"]


def convo(pattern, kind="user", **kwargs):
    """pattern: string of 'i' (incoming) / 'o' (outgoing), oldest first."""
    messages = [Message(id=i + 1, date=NOW - timedelta(minutes=len(pattern) - i), outgoing=c == "o", text="текст")
                for i, c in enumerate(pattern)]
    return Chat(id="9", title="Поток", kind=kind, messages=messages, **kwargs)


def test_spam_needs_uninterrupted_incoming_streak():
    assert "spam" in analyze(convo("o" + "i" * 20 + "o"))["categories"]
    assert analyze(convo("o" + "i" * 20))["incoming_streak"] == 20
    row = analyze(convo("i" * 19 + "o" + "i" * 19))  # 38 incoming, but my reply breaks the run
    assert "spam" not in row["categories"] and row["incoming_streak"] == 19


def test_spam_only_for_private_user_chats_and_respects_manual_marks():
    for kind in ("group", "channel", "bot"):
        assert "spam" not in analyze(convo("i" * 25, kind=kind))["categories"]
    assert "spam" not in analyze(convo("i" * 25), Annotation(important=True))["categories"]
    assert "spam" not in analyze(convo("i" * 25), Annotation(risk_review="trusted"))["categories"]


def test_short_chat_counts_whole_history_only_when_fully_loaded():
    assert "short" in analyze(convo("io" * 9 + "i"))["categories"]  # 19 messages
    assert "short" not in analyze(convo("io" * 10))["categories"]  # 20 messages
    assert "short" in analyze(convo(""))["categories"]  # empty but fully loaded
    assert "short" not in analyze(convo("io" * 5, history_status="limited"))["categories"]
    # A loaded window that hit the per-chat limit says nothing about the true total.
    assert "short" not in analyze(convo("io" * 5, history_limit=10))["categories"]


def test_spam_and_short_are_separate_and_thresholds_configurable():
    spam = analyze(convo("i" * 20))
    assert "spam" in spam["categories"] and "short" not in spam["categories"]
    custom = classify(convo("i" * 8), Annotation(), Settings(spam_streak=8, short_chat_messages=5), None, NOW)
    assert "spam" in custom["categories"] and "short" not in custom["categories"]


def job_chat(*texts, title="Тест", kind="user"):
    return Chat(id="1", title=title, kind=kind, messages=[
        Message(id=i, date=NOW - timedelta(days=1, minutes=i), outgoing=False, text=text) for i, text in enumerate(texts, 1)])


@pytest.mark.parametrize("texts,expected", [
    (["Здравствуйте, у нас открыта вакансия", "Удобно завтра?"], True),  # strong word in a short chat
    (["Как прошло собеседование?", "Пришлю резюме"], True),  # strong and weak in separate messages
    (["Прочитал резюме статьи", "Хорошее интервью"], False),  # weak words alone are ambiguous
    (["Завтра собеседование"] + ["Привет"] * 40, False),  # one mention in a long unrelated chat
])
def test_job_rules_for_private_chats(texts, expected):
    assert ("job" in analyze(job_chat(*texts))["categories"]) is expected


def test_job_needs_several_posts_in_broadcast_chats():
    filler = ["Новости"] * 10
    assert "job" not in analyze(job_chat("Вакансия", *filler, kind="channel"))["categories"]
    assert "job" in analyze(job_chat("Вакансия", "Вакансия", "Вакансия", *filler, kind="channel"))["categories"]
    assert "job" not in analyze(job_chat("Вакансия", *filler, kind="bot"))["categories"]


def test_job_title_and_explained_reason():
    row = analyze(job_chat("Привет", title="Вакансии для продактов", kind="channel"))
    assert "job" in row["categories"] and "названию" in " ".join(row["reasons"])
    assert "job" not in analyze(job_chat("Привет", title="Тестирование работы модели", kind="group"))["categories"]
    reason = analyze(job_chat("Вакансия", "Вакансия"))["reasons"][0]
    assert reason.startswith("Работа: вакансия ×2")


def test_manual_job_mark_overrides_keywords():
    assert "job" not in analyze(job_chat("Вакансия", "Вакансия"), Annotation(job=False))["categories"]
    assert "job" in analyze(job_chat("Привет"), Annotation(job=True))["categories"]
