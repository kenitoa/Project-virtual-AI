"""Bounded extractive notes; never infer facts from assistant output."""

import json


def summarize(store, user_id, session_id):
    rows = store.summary_inputs(user_id, session_id)
    selected = []
    for row in reversed(rows):
        if len(json.dumps(selected + [row], ensure_ascii=False)) <= 700:
            selected.append(row)
    if not selected:
        return None
    return store.add_summary(
        user_id,
        json.dumps(selected, ensure_ascii=False),
        [row["id"] for row in selected],
        confirmed=True,
        session_id=session_id,
        replace_existing=True,
    )
