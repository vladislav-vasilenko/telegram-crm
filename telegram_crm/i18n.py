"""Server-side English for files the browser layer cannot reach: the CSV and exported chats.

It uses the dictionary and the rules of static/i18n.js, so both stay in one place.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

CYRILLIC = re.compile("[А-Яа-яЁё]")
SOURCE = Path(__file__).parent / "static" / "i18n-en.js"


@lru_cache(maxsize=1)
def dictionary():
    text = SOURCE.read_text(encoding="utf-8")
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    keys = sorted(data["fragments"], key=len, reverse=True)
    pattern = re.compile("(?<![А-Яа-яЁё])(?:" + "|".join(map(re.escape, keys)) + ")(?![А-Яа-яЁё])")
    rules = [(re.compile(source), re.sub(r"\$(\d)", r"\\\1", target)) for source, target in data["patterns"]]
    return data["exact"], data["fragments"], pattern, rules


def translate(text, language="en"):
    if language != "en" or not CYRILLIC.search(text):
        return text
    exact, fragments, pattern, rules = dictionary()
    core = text.strip()
    if core in exact:
        return text.replace(core, exact[core])

    def phrases(part):
        part = pattern.sub(lambda match: fragments[match.group()], part)
        for rule, target in rules:
            part = rule.sub(target, part)
        return part
    for key in fragments:
        if "«" in key:  # fixed sentences that quote a button, before the split below
            text = text.replace(key, fragments[key])
    return "".join(part if part.startswith("«") else phrases(part) for part in re.split(r"(«[^»]*»)", text))
