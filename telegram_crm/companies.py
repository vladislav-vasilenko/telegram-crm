"""Company categories: Sber, Huawei, Yandex and VK (with Mail.ru and Dzen).

A chat title, a username or your own Telegram folder is reliable, so it assigns the category. Message text is
not: bank adverts, link footers and email addresses mention these companies all the time, so a match there
only makes the chat a "possible" one to check by hand.
"""
import re
from functools import lru_cache

NAMES = {"sber": "Sber", "huawei": "Huawei", "yandex": "Yandex", "vk": "VK"}
MAYBE = "company_maybe"
START = r"(?<![\w@.])"
END = r"(?![\w])"
# Message text: words that mean the company. A bare "vk" or "вк" usually means a link or "write me on VK".
TEXT = {
    "sber": re.compile(START + r"(?:сбер\w*|sber\w*|гигачат\w*|gigachat\w*|salute\s?speech|салют\s?спич)", re.I),
    "huawei": re.compile(START + r"(?:huawei\w*|хуавей\w*|хуавэй\w*)", re.I),
    "yandex": re.compile(START + r"(?:яндекс\w*|yandex\w*|кинопоиск\w*|kinopoisk\w*)", re.I),
    "vk": re.compile(START + r"(?:vk\s?(?:cloud|tech|group|team|company|lab|видео|video)|вк\s?(?:тех|клауд|групп\w*|видео)"
                     r"|mail\.ru|mailru|майл\.?\s?ру|мейл\.?\s?ру|мэйл\.?\s?ру|dzen\w*|яндекс\.?\s?дзен)" + END, re.I),
}
# Titles and usernames are short and deliberate, so a bare name counts here.
TITLE = {
    **TEXT,
    "yandex": re.compile(START + r"(?:яндекс\w*|yandex\w*|кинопоиск\w*|kinopoisk\w*|шад)" + END, re.I),
    "vk": re.compile(START + r"(?:vk|вк|вконтакте|vkontakte|mail\.ru|mailru|майл\.?\s?ру|мейл\.?\s?ру|дзен|dzen\w*)" + END, re.I),
}
# Your own Telegram folders with these names already say where a chat belongs.
OWN_FOLDERS = {"сбербанк": "sber", "сбер": "sber", "sber": "sber", "huawei": "huawei", "хуавей": "huawei",
               "яндекс": "yandex", "yandex": "yandex", "vk": "vk", "вк": "vk", "mail.ru": "vk"}
PRIVATE_MIN, PRIVATE_SHARE = 3, 0.03
FEED_MIN, FEED_SHARE = 15, 0.15  # channels and groups repost adverts, so they need far more


@lru_cache(maxsize=250_000)
def text_companies(text):
    return tuple(key for key, pattern in TEXT.items() if pattern.search(text))


def folder_index(layout):
    """{chat_id: company} from your Telegram folders, as last read from Telegram."""
    index = {}
    for folder in (layout or {}).get("folders", []):
        company = OWN_FOLDERS.get((folder.get("title") or "").strip().lower())
        if company:
            index.update(dict.fromkeys(folder.get("chats", ()), company))
    return index


def possible_companies(chat):
    counts = {}
    for message in chat.messages:
        for key in text_companies(message.text):
            counts[key] = counts.get(key, 0) + 1
    total = len(chat.messages)
    if chat.kind == "bot" or not total:
        return []
    minimum, share = (PRIVATE_MIN, PRIVATE_SHARE) if chat.kind == "user" else (FEED_MIN, FEED_SHARE)
    return sorted((key for key, count in counts.items() if count >= minimum and count / total >= share), key=lambda key: -counts[key])


def assign(chat, annotation, folders):
    """(companies, possible, why): your choice wins, then the title, then your folder, then message text."""
    if annotation.company == "none":
        return [], [], None
    if annotation.company:
        return [annotation.company], [], "manual"
    label = f"{chat.title} {chat.username or ''}"
    named = [key for key, pattern in TITLE.items() if pattern.search(label)]
    if named:
        return named, [], "title"
    if chat.id in folders:
        return [folders[chat.id]], [], "folder"
    return [], possible_companies(chat), None


def apply_companies(row, chat, annotation, folders):
    """Add company categories to a classified row. Categories overlap, as all others do."""
    companies, possible, why = assign(chat, annotation, folders)
    row["company_possible"] = possible
    keys = [f"company_{key}" for key in companies]
    if possible:
        keys.append(MAYBE)
    if keys and row["categories"] == ["active"]:
        row["categories"] = []
        row["primary_category"] = keys[0]
    row["categories"] = [*row["categories"], *keys]
    names = ", ".join(NAMES[key] for key in companies)
    if why == "manual":
        row["reasons"] = [*row["reasons"], f"Компания: {names} (ваша отметка)"]
    elif why == "title":
        row["reasons"] = [*row["reasons"], f"Компания: {names} (по названию чата)"]
    elif why == "folder":
        row["reasons"] = [*row["reasons"], f"Компания: {names} (ваша папка Telegram)"]
    if possible:
        row["reasons"] = [*row["reasons"], "Возможно, компания: " + ", ".join(NAMES[key] for key in possible) + " (упоминания в сообщениях)"]
    return row
