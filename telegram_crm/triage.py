"""One-off cleanup triage: which chats to remove first, review by hand, or keep.

Verdicts are stored locally and only drive a filtered view. Nothing here deletes anything:
removal still goes through the cleanup dialog and its backup. A verdict is tied to the newest
message it saw, so a chat that received something new falls back to manual review.
"""
import re

from .analysis import classify
from .store import utcnow

BUCKETS = {
    "remove_empty": ("remove", "Пустые личные чаты", "В чате нет сообщений: терять нечего."),
    "remove_deleted": ("remove", "Удалённые аккаунты", "Аккаунта больше нет, в чате меньше 20 сообщений."),
    "remove_cold": ("remove", "Вам писали, вы не отвечали", "Ни одного вашего сообщения, тишина 5+ месяцев."),
    "remove_short_old": ("remove", "Короткие старые чаты", "До 5 сообщений, тишина больше года."),
    "remove_scam": ("remove", "Метка SCAM/FAKE от Telegram", "Telegram сам пометил аккаунт как SCAM или FAKE."),
    "remove_bots": ("remove", "Заброшенные и взрослые боты", "Бот не использовался больше года или это взрослый/казино-бот."),
    "remove_feeds_dead": ("remove", "Мёртвые каналы и группы", "Нет постов больше года, вы там не писали."),
    "review_romantic_old": ("review", "Романтика: старые чаты", "Текст похож на приглашения или свидания, тишина 5+ месяцев."),
    "review_romantic_possible": ("review", "Романтика: возможно", "Только слова-сигналы; контекст неочевиден."),
    "review_romantic_cooling": ("review", "Романтика: остывают", "Романтический оттенок, тишина 1–5 месяцев."),
    "review_job_archive": ("review", "Работа: архив", "Про работу, но тишина больше года. Сверьте контакты рекрутеров."),
    "review_old_dialogue": ("review", "Старые живые диалоги", "Была настоящая переписка или личные данные. Решить может только человек."),
    "review_cold_recent": ("review", "Недавние без вашего ответа", "Вы не отвечали, но писали недавно."),
    "review_suspicious": ("review", "Подозрительные и спам", "Сигналы риска: проверьте, затем удаляйте."),
    "review_bots_accounts": ("review", "Боты с доступом и деньгами", "Банки, платежи, SMS, VPN, авторизация: может быть нужна история."),
    "review_bots_unused": ("review", "Боты без дела 5–12 месяцев", "Не использовались 5–12 месяцев."),
    "review_feeds_silent": ("review", "Затихшие каналы и группы", "Мало постов или вы там писали. В закрытые нужно приглашение."),
    "review_feeds_unread": ("review", "Активные, но не читаете", "100+ непрочитанных, вы не пишете."),
    "review_changed": ("review", "Изменились после разбора", "Новые сообщения или ваши отметки: прежний вердикт снят."),
    "keep_important": ("keep", "Важные и служебные", "Закреплённые, частые, семья и служебные чаты Telegram."),
    "keep_job": ("keep", "Работа: актуальное", "Рекрутеры и вакансии за последний год."),
    "keep_active": ("keep", "Активные", "Живое общение и то, что вы читаете."),
}
GROUPS = {"remove": "Удалить сначала", "review": "Проверить вручную", "keep": "Оставить"}

ROMANTIC_STRONG = re.compile(r"увидел[аи]? тебя|думаю о тебе|хочу позвать|хочу пригласить|свидан\w*", re.I)
ROMANTIC_WEAK = re.compile(r"прогуля\w*|встретиться|\bкофе\b|\bча[йюя]\b", re.I)
SENSITIVE = re.compile(r"парол|договор|паспорт|\bснилс\b|\bинн\b|реквизит|налог|банк|кошел[её]к|seed|invoice|сч[её]т на оплату|адрес", re.I)
FAMILY = re.compile(r"\b(?:мама|папа|брат|сестра|бабушка|дедушка|жена|муж|дочь|сын|mom|dad)\b", re.I)
SERVICE_IDS = {"777000"}  # Telegram's own account: login codes and security notices
ACCOUNT_BOTS = re.compile(r"втб|\bpay|payeer|pyypl|кошел|wallet|bank|alfa|sms|authoriz|авторизац|vpn|premium|email|дія|vfs|fragment|tonnel|portals|tribute|amvera|cards|btc|bitoto", re.I)
ADULT_BOTS = re.compile(r"хентай|анечк|мелисс|масаж|massage|богин|hottie|privat|казино|dating|знакомств", re.I)
CRIMSON_BUCKETS = {"remove_empty", "remove_deleted", "remove_scam"}
CRIMSON_DAYS = 730
SILENT_DAYS, YEAR_DAYS, COOLING_DAYS, UNREAD_BACKLOG = 150, 365, 30, 100


def user_bucket(chat, row, romantic):
    idle, written, loaded = row["idle_days"], row["written"], row["loaded_messages"]
    sensitive = any(SENSITIVE.search(m.text) for m in chat.messages)
    if chat.deleted and loaded < 20 and not sensitive:
        return "remove_deleted"
    if not loaded:
        return "remove_empty"
    likely = chat.id in romantic.get("likely", ())
    signal = likely or (chat.id not in romantic.get("not", ()) and "job" not in row["categories"] and (
        any(ROMANTIC_STRONG.search(m.text) for m in chat.messages)
        or sum(bool(ROMANTIC_WEAK.search(m.text)) for m in chat.messages) >= 2))
    if signal and idle >= COOLING_DAYS:
        if idle < SILENT_DAYS:
            return "review_romantic_cooling"
        return "review_romantic_old" if likely else "review_romantic_possible"
    if "job" in row["categories"]:
        return "keep_job" if idle < YEAR_DAYS else "review_job_archive"
    if idle < SILENT_DAYS:
        return "keep_active" if written else "review_cold_recent"
    if not written and not sensitive:
        return "remove_cold"
    if loaded <= 5 and idle >= YEAR_DAYS and not sensitive:
        return "remove_short_old"
    return "review_old_dialogue"


def bucket_for(chat, row, romantic):
    """row is the classify() result, extended with row["written"] (your message count)."""
    categories, idle = row["categories"], row["idle_days"]
    if ("important" in categories or chat.pinned or chat.id in SERVICE_IDS or FAMILY.search(chat.title)
            or (chat.username or "").lower() == "botfather"):
        return "keep_important"
    if chat.kind == "user" and (chat.telegram_scam or chat.telegram_fake):
        return "remove_scam"
    if "suspected_scam" in categories or "spam" in categories:
        return "review_suspicious"
    if chat.kind == "user":
        return user_bucket(chat, row, romantic)
    if "job" in categories:
        return "keep_job" if idle is not None and idle < YEAR_DAYS else "review_job_archive"
    if chat.kind == "bot":
        if ADULT_BOTS.search(chat.title):
            return "remove_bots"
        if ACCOUNT_BOTS.search(chat.title):
            return "review_bots_accounts"
        if idle is None or idle >= YEAR_DAYS:
            return "remove_bots"
        return "review_bots_unused" if idle >= SILENT_DAYS else "keep_active"
    if idle is None:
        return "review_feeds_silent"
    if idle >= YEAR_DAYS and not row["written"]:
        return "remove_feeds_dead"
    if idle >= SILENT_DAYS:
        return "review_feeds_silent"
    return "review_feeds_unread" if chat.unread_count >= UNREAD_BACKLOG and not row["written"] else "keep_active"


def build_triage(store, romantic=None):
    """Compute and save a verdict for every chat. romantic: {"likely": [ids], "not": [ids], "hold": [ids]}; hold keeps a chat out of the remove group."""
    romantic = {key: set(value) for key, value in (romantic or {}).items()}
    settings, chats = store.settings(), {}
    for stored in store.all():
        chat = stored["chat"]
        if chat.demo:
            continue
        row = {**classify(**stored, settings=settings), "written": sum(m.outgoing for m in chat.messages)}
        bucket = bucket_for(chat, row, romantic)
        if chat.id in romantic.get("hold", ()) and BUCKETS[bucket][0] == "remove":
            bucket = "review_old_dialogue" if chat.kind == "user" else "review_bots_unused" if chat.kind == "bot" else "review_feeds_silent"
        chats[chat.id] = {"bucket": bucket, "last_message_id": chat.last_message_id}
    result = {"created_at": utcnow().isoformat(), "chats": chats}
    store.set_metadata("triage", result)
    return result


def color_for(bucket, row):
    """Green keep, yellow review, red remove; crimson when there is next to nothing to lose."""
    group = BUCKETS[bucket][0]
    if group != "remove":
        return "green" if group == "keep" else "yellow"
    idle = row["idle_days"]
    long_dead = bucket in ("remove_bots", "remove_feeds_dead") and (idle is None or idle >= CRIMSON_DAYS)
    adult_bot = bucket == "remove_bots" and ADULT_BOTS.search(row["title"])
    return "crimson" if bucket in CRIMSON_BUCKETS or long_dead or adult_bot else "red"


def apply_triage(row, chat, annotation, saved):
    """Add the saved verdict to a classified row; anything that changed since goes to manual review."""
    verdict = saved.get("chats", {}).get(chat.id)
    if not verdict:
        row["triage"] = None
        return row
    bucket = verdict["bucket"]
    changed = verdict["last_message_id"] != chat.last_message_id
    protected = "important" in row["categories"] or annotation.job is True or "job" in row["categories"]
    if BUCKETS[bucket][0] != "keep" and (changed or (BUCKETS[bucket][0] == "remove" and protected)):
        bucket = "review_changed"
    group, label, why = BUCKETS[bucket]
    row["triage"] = {"bucket": bucket, "group": group, "label": label, "color": color_for(bucket, row)}
    row["reasons"] = [*row["reasons"], f"Разбор: {GROUPS[group]} → {label}. {why}"]
    return row


def triage_summary(rows, saved):
    if not saved:
        return None
    counts = {}
    for row in rows:
        if row.get("triage"):
            counts[row["triage"]["bucket"]] = counts.get(row["triage"]["bucket"], 0) + 1
    return {"created_at": saved.get("created_at"), "groups": GROUPS,
            "buckets": {key: {"group": g, "label": l, "why": w, "count": counts.get(key, 0)} for key, (g, l, w) in BUCKETS.items()}}
