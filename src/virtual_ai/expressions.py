"""Conservative phrase policy over inspected answers, never model actions."""

import re

from virtual_ai.safety import map_expression


def conservative_expression(response):
    text = response.final.strip()
    # Questions, quotes, uncertainty and negation cannot be reliably classified.
    if any(
        word in text
        for word in (
            "?",
            "？",
            '"',
            "'",
            "“",
            "”",
            "아니",
            "않",
            "못",
            "모르",
            "불확실",
            "아마",
            "하지만",
        )
    ):
        return "neutral"
    happy = bool(
        re.search(r"^(?:축하해요|합격을 축하해요|정말 기뻐요|반가워요)[.!。！\s]", text)
    )
    sad = bool(
        re.search(
            r"^(?:마음이 아프네요|정말 속상하겠어요|많이 힘드셨겠어요)[.!。！\s]", text
        )
    )
    if happy and not any(w in text for w in ("슬프", "속상", "아프", "힘드")):
        return "happy"
    if sad and not any(w in text for w in ("축하", "기뻐", "즐거")):
        return "sad"
    return "neutral"


def select_expression(response, allowed, policy=None):
    if response.blocked or policy is None:
        return "neutral"
    try:
        return map_expression(policy(response), allowed)
    except Exception:
        return "neutral"
