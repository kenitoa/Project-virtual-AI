"""Extraction, content policy and speech formatting are separate steps."""

import re
import unicodedata

from virtual_ai.schemas import Response

FALLBACK = "이 답변은 출력할 수 없어요. 다른 이야기로 이어가 주세요."


def contains_secret(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return bool(
        re.search(
            r"-----begin .*private key-----|\bsk-[a-z0-9_-]{16,}|"
            r"\b(?:api[_ -]?key|password|token)[\"']?\s*[:=]\s*[\"']?\S+|"
            r"\bbearer\s+[a-z0-9._~-]{8,}|\bgh[pousr]_[a-z0-9]{20,}",
            normalized,
        )
    )


def extract_final(raw: str) -> str:
    # A closing tag without an opening tag can occur with a prefilled template.
    if re.search(r"</think\s*>", raw, re.I):
        raw = re.split(r"</think\s*>", raw, flags=re.I)[-1]
    # An incomplete thinking block has no safe final answer after it.
    raw = re.split(r"<think\b", raw, maxsplit=1, flags=re.I)[0]
    raw = re.sub(
        r"^\s*(?:<\|im_start\|>assistant\s*|<\|start_header_id\|>assistant<\|end_header_id\|>\s*|assistant\s*:)\s*",
        "",
        raw,
        flags=re.I,
    )
    # Never narrate a generated next role, including its contents.
    raw = re.split(
        r"<\|(?:im_start|im_end|eot_id|endoftext|start_header_id)\|>|\[/?INST\]|(?m:^\s*(?:user|system|assistant|developer|human)\s*:)",
        raw,
        maxsplit=1,
        flags=re.I,
    )[0]
    return raw.strip()


def check_output(text: str, blocked_terms: tuple[str, ...]) -> bool:
    """Baseline content gate, not a comprehensive semantic moderation model."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    if not normalized.strip():
        return False
    if any(
        unicodedata.normalize("NFKC", term).casefold() in normalized
        for term in blocked_terms
    ):
        return False
    # Avoid reading common credential formats even when surrounded by prose.
    return not contains_secret(text)


def clean_speech(text: str) -> str:
    text = re.sub(r"(```|~~~).*?(?:\1|\Z)", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*(?:`|$)", " ", text)
    text = re.sub(r"<\|[^>]*\|>|<[^>]*>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|/(?:no_think|think)\b", " ", text)
    text = re.sub(r"(?m)^\s*[#>*-]+\s*", "", text)
    text = re.sub(r"[*_~]", "", text)
    text = "".join(
        c for c in text if not unicodedata.category(c).startswith("C") or c.isspace()
    )
    return " ".join(text.split())


def prepare_response(response_id: str, raw: str, settings) -> Response:
    final = extract_final(raw)
    allowed = check_output(final, settings.blocked_terms)
    if not allowed:
        final = FALLBACK
    # Inspection happens before truncation, so an unsafe tail is still checked.
    final = final[: settings.max_output_chars].strip()
    sentences = re.split(r"(?<=[.!?。！？])\s+", final)
    final = " ".join(sentences[: settings.max_sentences])
    speech = clean_speech(final)
    return Response(response_id, raw, final, speech, not allowed)


def map_expression(value: str, allowed: tuple[str, ...]) -> str:
    """Only a known symbolic expression can reach a future avatar adapter."""
    return value if value in allowed else "neutral"
