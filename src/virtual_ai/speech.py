"""Operator-owned pronunciation of words; numeric/conditional text stays intact."""

import re


def validate_pronunciations(mapping):
    if not isinstance(mapping, dict) or len(mapping) > 100:
        raise ValueError("pronunciations must contain at most 100 words")
    from virtual_ai.safety import check_output

    for word, spoken in mapping.items():
        if not isinstance(word, str) or not re.fullmatch(r"[A-Za-z가-힣]{2,40}", word):
            raise ValueError("pronunciation keys must be plain words without numbers")
        if (
            not isinstance(spoken, str)
            or not re.fullmatch(r"[가-힣 ]{1,60}", spoken)
            or not check_output(spoken, ())
        ):
            raise ValueError("pronunciation values must be Korean words")


def pronunciation(text, mapping):
    if not mapping:
        return text
    # One substitution pass prevents cascading dictionary rewrites.
    pattern = (
        r"(?<![A-Za-z가-힣0-9])("
        + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
        + r")(?![A-Za-z가-힣0-9])"
    )
    return re.sub(pattern, lambda m: mapping[m[0]], text)
