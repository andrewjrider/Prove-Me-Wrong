"""Strongest-arguments summary for a claim.

Stubbed for the MVP: picks the longest responses on each side as a proxy
for "most developed argument". Swap generate_summary() for a real LLM call
(e.g. summarize responses via the Claude API) once that's wired in — the
call site in routes.py doesn't need to change.
"""


def _pick_top(responses, limit=3):
    return sorted(responses, key=lambda r: len(r["body"]), reverse=True)[:limit]


def generate_summary(responses):
    agree = [r for r in responses if r["side"] == "agree"]
    disagree = [r for r in responses if r["side"] == "disagree"]

    agree_top = _pick_top(agree)
    disagree_top = _pick_top(disagree)

    agree_summary = (
        " ".join(r["body"] for r in agree_top)
        if agree_top
        else "No arguments submitted yet."
    )
    disagree_summary = (
        " ".join(r["body"] for r in disagree_top)
        if disagree_top
        else "No arguments submitted yet."
    )

    return {"agree_summary": agree_summary, "disagree_summary": disagree_summary}
