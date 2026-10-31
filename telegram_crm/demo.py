from datetime import timedelta

from .models import Annotation, Chat, Message
from .store import utcnow


def load_demo(store):
    if any(not row["chat"].demo for row in store.all()):
        raise ValueError("Демо нельзя смешивать с реальными переписками")
    now = utcnow()
    fixtures = [
        ("demo_1", "Алина", 0, 46, True, "user", "Давай в субботу в той кофейне?", Annotation(dating=True, stage="meeting_planned", notes="Обсуждаем встречу на выходных.")),
        ("demo_2", "Команда продукта", 0, 72, True, "group", "Макет посмотрели, всё готово к понедельнику.", Annotation()),
        ("demo_3", "Соня", 29, 20, False, "user", "Было бы здорово как-нибудь выбраться на кофе)", Annotation(dating=True, stage="talking", notes="Познакомились на мероприятии. Встречу пока не назначили.")),
        ("demo_4", "Мария", 37, 12, False, "user", "Я тоже рада познакомиться! Как проходят выходные?", Annotation()),
        ("demo_5", "Crypto Support", 2, 6, False, "user", "Пришли код входа из Telegram для подтверждения аккаунта на https://telegram-check.example/login", Annotation()),
        ("demo_6", "Дизайн / сообщество", 188, 10, False, "group", "Спасибо всем за встречу, материалы в закрепе.", Annotation()),
        ("demo_7", "Екатерина", 226, 18, False, "user", "Да, давай как-нибудь спишемся", Annotation(dating=True)),
        ("demo_8", "Старый проект", 813, 8, False, "group", "Проект закрыт, спасибо команде!", Annotation()),
        ("demo_9", "Никита", 117, 14, False, "user", "Спасибо, всё получил. Хорошего дня!", Annotation()),
        ("demo_10", "Дарья", 0, 38, False, "user", "Отправила подборку, посмотри, когда будет время", Annotation()),
        ("demo_11", "Полина", 4, 30, False, "user", "Давай расскажу, что нового за неделю", Annotation(dating=True, stage="talking", last_progress_at=now - timedelta(days=45))),
        ("demo_12", "Ирина", 56, 8, False, "user", "Спасибо за вечер! Рада была увидеться", Annotation(dating=True, stage="met")),
    ]
    for chat_id, title, days, size, pinned, kind, ending, annotation in fixtures:
        last_at = now - timedelta(days=days, hours=1)
        messages = [Message(id=i + 1, date=last_at - timedelta(hours=(size - i - 1) * 7),
                            outgoing=i % 2 == 0, text=("Как дела? Есть новости?" if i % 2 else "Привет! Да, сейчас расскажу."))
                    for i in range(size)]
        messages[-1].text = ending
        messages[-1].outgoing = False
        chat = Chat(id=chat_id, title=title, kind=kind, pinned=pinned, messages=messages,
                    last_message_id=size, unread_count=2 if days == 0 else 0, demo=True)
        store.upsert(chat, "demo")
        store.annotate(chat_id, annotation)
    store.set_metadata("demo", True)
    store.set_metadata("last_scan_at", now.isoformat())

