import calendar
from functools import lru_cache
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.parse import quote

from .models import Annotation, Chat, Settings


CATEGORIES = {
    "important": "Важные",
    "stalled": "Знакомства без развития",
    "suspected_scam": "Подозрение на мошенничество",
    "inactive": "Неактивные",
    "very_old": "Очень старые",
    "dead": "Мёртвые",
    "active": "Остальные",
}

DATING = re.compile(r"свидан\w*|дайвинчик|тиндер|tinder|баду|bumble|рад[аы]? познакомиться|будем знакомы|сайт[ае]? знакомств", re.I)
WARNING = re.compile(r"(?:не|никогда не|никому не)\s+(?:пришл\w*|присыл\w*|отправ\w*|перевод\w*|сообщ\w*|передав\w*|давай\w*)|(?:осторожно|мошенник\w*|скам)\s*[:!]", re.I)
REQUEST = re.compile(r"(?:пришли|отправь|скинь|сообщи|продиктуй|перешли|введи|send|share|give|enter)\b", re.I)
CREDENTIAL = re.compile(r"(?:код.{0,45}(?:телеграм|telegram|смс|sms|вход|авторизац)|(?:смс|sms|verification|login|2fa|otp).{0,20}(?:код|code)|пароль|seed.{0,8}(?:фраз|phrase))", re.I)
PAYMENT = re.compile(r"(?:переведи|скинь|пришли|оплати|внеси|отправь).{0,50}(?:деньг|руб|₽|аванс|предоплат|залог|депозит|usdt|крипт)|(?:предоплат|залог|аванс|депозит).{0,40}(?:встреч|свидан|билет|брон)", re.I)
PROFIT = re.compile(r"(?:гарантирован\w*|без риска).{0,50}(?:доход|прибыл|заработ)|(?:удвою|удвоим|иксы|x\d{2,}).{0,40}(?:вклад|деньг|крипт|депозит)", re.I)
URL = re.compile(r"https?://[^\s<>]+", re.I)


# Regex verdicts per message text are cached: /api/state re-classifies every chat on each poll,
# message texts rarely change, and regex matching dominated that cost.
@lru_cache(maxsize=250_000)
def is_dating_text(text: str) -> bool:
    return bool(DATING.search(text))


@lru_cache(maxsize=250_000)
def scam_text_verdict(text: str):
    """(reason, weight, urls_in_text) for one incoming message, or None if the text is itself a warning."""
    if WARNING.search(text):
        return None
    reason, weight = None, 0
    if REQUEST.search(text) and CREDENTIAL.search(text):
        reason, weight = "Входящее сообщение просит код входа или пароль", 85
    elif PAYMENT.search(text):
        reason, weight = "Просьба о переводе или предоплате — нужна проверка контекста", 55
    elif PROFIT.search(text):
        reason, weight = "Обещание гарантированного заработка", 65
    return reason, weight, tuple(URL.findall(text))


def subtract_months(value: datetime, count: int):
    month_index = value.year * 12 + value.month - 1 - count
    year, month = divmod(month_index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def fingerprint(chat: Chat):
    return hashlib.sha256(json.dumps(
        [m.model_dump(mode="json") for m in sorted(chat.messages, key=lambda m: (m.date, m.id))[-20:]],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scam_signals(chat: Chat, annotation: Annotation):
    signals = []
    if chat.telegram_scam or chat.telegram_fake:
        signals.append({"reason": "Аккаунт помечен Telegram как SCAM/FAKE", "message_id": None, "weight": 95})
    if annotation.risk_review == "scam":
        signals.append({"reason": "Вы подтвердили подозрение вручную", "message_id": None, "weight": 100})
    if annotation.risk_review == "trusted":
        return signals
    # Group/channel posts and forwarded warnings are not accusations against a person.
    if chat.kind not in ("user", "bot"):
        return signals
    for message in chat.messages:
        if message.outgoing or message.forwarded:
            continue
        verdict = scam_text_verdict(message.text)
        if verdict is None:  # the message itself is a scam warning
            continue
        reason, weight, text_urls = verdict
        links = message.urls + list(text_urls)
        for link in links:
            host = (urlparse(link).hostname or "").lower().rstrip(".")
            official = any(host == d or host.endswith("." + d) for d in ("telegram.org", "t.me", "telegram.me"))
            if host and not official and ("telegram" in host or "tg-auth" in host):
                reason, weight = "Ссылка с имитацией домена Telegram", 80
        if reason:
            signals.append({"reason": reason, "message_id": message.id, "weight": weight})
    # Repeated copies of one rule do not inflate certainty.
    unique = {signal["reason"]: signal for signal in signals}
    return list(unique.values())[:8]


def classify(chat: Chat, annotation: Annotation, settings: Settings, llm=None, now=None):
    now = now or datetime.now(timezone.utc)
    messages = sorted(chat.messages, key=lambda m: (m.date, m.id))
    last = messages[-1] if messages else None
    last_at = last.date if last else None
    idle_days = max(0, int((now - last_at).total_seconds() // 86400)) if last_at else None
    recent = [m for m in messages if now - timedelta(days=30) <= m.date <= now]
    incoming, outgoing = sum(not m.outgoing for m in recent), sum(m.outgoing for m in recent)
    active_days = len({m.date.date() for m in recent})
    # Frequency dominates pinning, with both sides required for automatic importance.
    frequent = incoming >= 2 and outgoing >= 2 and len(recent) >= settings.frequent_messages
    score = min(75, len(recent) * 1.5 + active_days * 2) + (15 if chat.pinned else 0)
    if annotation.important is True:
        score += 25
    llm_current = bool(llm and llm.get("fingerprint") == fingerprint(chat))
    ai = llm["result"] if llm_current else None
    confident = bool(ai and ai.get("confidence", 0) >= 0.75)
    important = annotation.important if annotation.important is not None else bool(
        frequent or chat.pinned or (confident and ai["importance"] == "important"))
    categories, reasons = [], []
    if important:
        categories.append("important")
        if frequent:
            reasons.append(f"Частое взаимное общение: {len(recent)} сообщений за 30 дней")
        if chat.pinned:
            reasons.append("Чат закреплён в Telegram")
        if annotation.important is True:
            reasons.append("Вы отметили чат как важный")
        if confident and ai["importance"] == "important":
            reasons.append("LLM рекомендует считать чат важным")

    dating_evidence = [m.id for m in messages if is_dating_text(m.text)]
    can_be_dating = chat.kind == "user" and not chat.deleted
    dating_candidate = can_be_dating and bool(dating_evidence or (confident and ai["dating"] == "yes"))
    dating = can_be_dating and (annotation.dating if annotation.dating is not None else dating_candidate)
    anchor = annotation.last_progress_at or last_at
    stalled_days = max(0, int((now - anchor).total_seconds() // 86400)) if anchor else None
    if dating and annotation.stage not in ("met", "relationship", "closed") and anchor and now - anchor > timedelta(days=settings.stalled_days):
        categories.append("stalled")
        reasons.append(
            f"Знакомство: {stalled_days} дн. без отмеченного прогресса" if annotation.last_progress_at
            else f"Знакомство: переписка неактивна {idle_days} дн.; развитие требует проверки")
        if annotation.dating is None:
            reasons.append("Тип знакомства определён предположительно по тексту, подтвердите вручную")

    risks = scam_signals(chat, annotation)
    if confident and ai["scam_risk"] in ("medium", "high") and annotation.risk_review != "trusted":
        risks.append({"reason": "LLM обнаружила признаки риска: " + ai["reason"], "weight": 65 if ai["scam_risk"] == "medium" else 80, "message_id": None})
    if risks:
        categories.append("suspected_scam")
        reasons.extend(signal["reason"] for signal in risks)
    if last_at:
        if now - last_at > timedelta(days=settings.inactive_days):
            categories.append("inactive")
            reasons.append(f"Нет сообщений более {settings.inactive_days} дней")
        if last_at < subtract_months(now, settings.old_months):
            categories.append("very_old")
            reasons.append(f"Последнее сообщение старше {settings.old_months} календарных месяцев")
        if last_at < subtract_months(now, settings.dead_years * 12):
            categories.append("dead")
            reasons.append(f"Нет сообщений более {settings.dead_years} лет")
    if not categories:
        categories.append("active")
    if not messages:
        reasons.append("История недоступна; возраст переписки определить нельзя")
    if chat.history_status != "ok":
        reasons.append("История загружена частично; выводы ограничены доступными сообщениями")
    order = ("suspected_scam", "important", "stalled", "dead", "very_old", "inactive", "active")
    return {
        "id": chat.id, "title": chat.title, "username": chat.username, "kind": chat.kind,
        "pinned": chat.pinned, "archived": chat.archived, "unread_count": chat.unread_count,
        "demo": chat.demo, "deleted": chat.deleted, "last_message_at": last_at.isoformat() if last_at else None,
        "last_message": last.text[:180] if last else "", "last_outgoing": last.outgoing if last else False,
        "idle_days": idle_days, "stalled_days": stalled_days, "recent_messages": len(recent),
        "incoming_30d": incoming, "outgoing_30d": outgoing, "active_days_30d": active_days,
        "frequency_is_lower_bound": len(messages) >= chat.history_limit,
        "priority_score": round(score, 1), "categories": categories,
        "primary_category": next(k for k in order if k in categories), "reasons": reasons,
        "risk_signals": risks, "dating_candidate": dating_candidate, "dating_evidence_ids": dating_evidence[:5],
        "history_status": chat.history_status, "loaded_messages": len(messages),
        "annotation": annotation.model_dump(mode="json"), "llm": llm,
        "llm_stale": bool(llm and not llm_current),
        "telegram_url": (f"tg://resolve?domain={quote(chat.username, safe='')}" if chat.username
                         else f"tg://openmessage?user_id={chat.id}" if chat.kind in ("user", "bot")
                         else f"tg://privatepost?channel={chat.id[4:]}&post={last.id}" if chat.id.startswith('-100') and last
                         else f"tg://openmessage?chat_id={chat.id.lstrip('-')}"),
    }

