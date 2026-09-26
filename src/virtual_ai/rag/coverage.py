"""Explicit Korean question facets, not a semantic relevance classifier."""

import re

# Keep value requirements next to topic keywords: mentioning a price isn't a price.
FACETS = {
    "fee": ("참가비|가격|요금|얼마", r"(?:무료|\d[\d,]*\s*(?:만\s*)?원)", "비용"),
    "deadline": (
        "마감|언제까지",
        r"(?:마감|신청|접수).{0,70}(?:\d+\s*(?:월|일|시)|까지|상시)",
        "신청 마감",
    ),
    "schedule": (
        r"몇\s*시|시작\s*시간",
        r"(?:시작|진행|방송).{0,50}\d+\s*시",
        "시작 시간",
    ),
    "refund": ("환불", r"환불.{0,70}(?:가능|불가|않|없|반환|취소)", "환불 조건"),
}


def coverage(question, rows):
    requested, covered = [], []
    for key, (query, value, _) in FACETS.items():
        if re.search(query, question):
            requested.append(key)
            if any(re.search(value, row["text"]) for row in rows):
                covered.append(key)
    return {
        "method": "explicit_facets_v1",
        "requested": requested,
        "covered": covered,
        "missing": [k for k in requested if k not in covered],
        "semantic_relevance": "unverified",
    }


def missing_notice(result):
    names = ", ".join(FACETS[k][2] for k in result["missing"])
    return f"{names}은 현재 자료로 확인하지 못했어요." if names else ""
