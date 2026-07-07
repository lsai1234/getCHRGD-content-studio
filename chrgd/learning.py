"""The learning loop — real TikTok results feeding back into future builds.

The account's own history is the best predictor it has (an England-game post
did ~20k views against a ~300 baseline). Once results are logged per post,
two things happen:

* `insights(store)` — a human-readable digest: top posts, what the winners
  have in common (category, mechanic, style), the account's baseline.
* `performance_notes(store)` — a compact text block injected into every
  engine build/revision so the writing is steered by what has actually
  worked for THIS account, not generic best practice.

No LLM calls here — this is honest arithmetic over logged numbers.
"""

from __future__ import annotations

import json
from statistics import median

from .db import Store
from .models import Idea

# Fields the UI logs per post. Views is the anchor; the rest are optional.
METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves")

# Need at least this many logged posts before we claim to know anything.
MIN_POSTS_FOR_NOTES = 3


def _metrics(idea: Idea) -> dict:
    try:
        raw = json.loads(idea.metrics_json or "{}")
    except json.JSONDecodeError:
        return {}
    out = {}
    for f in METRIC_FIELDS:
        try:
            out[f] = int(raw.get(f) or 0)
        except (TypeError, ValueError):
            out[f] = 0
    return out


def _route(idea: Idea) -> dict:
    try:
        return json.loads(idea.route_json or "{}")
    except json.JSONDecodeError:
        return {}


def _traits(idea: Idea) -> dict:
    """The comparable features of one post."""
    route = _route(idea)
    return {
        "category": idea.content_category or "uncategorised",
        "mechanic": route.get("mechanic", ""),
        "style": route.get("style", ""),
        "hook": idea.hook or "",
    }


def insights(store: Store, top_n: int = 5) -> dict:
    """The digest shown to the human: baseline, top posts, winning traits."""
    logged = [
        (idea, _metrics(idea))
        for idea in store.ideas_with_metrics()
    ]
    logged = [(i, m) for i, m in logged if m.get("views", 0) > 0]
    if not logged:
        return {"posts_logged": 0, "baseline_views": 0, "top": [], "traits": []}

    views = [m["views"] for _, m in logged]
    baseline = int(median(views))
    ranked = sorted(logged, key=lambda im: im[1]["views"], reverse=True)

    top = []
    for idea, m in ranked[:top_n]:
        t = _traits(idea)
        top.append(
            {
                "idea_id": idea.idea_id,
                "hook": t["hook"] or idea.concept_note,
                "views": m["views"],
                "likes": m.get("likes", 0),
                "shares": m.get("shares", 0),
                "saves": m.get("saves", 0),
                "multiple": round(m["views"] / baseline, 1) if baseline else 0,
                "category": t["category"],
                "mechanic": t["mechanic"],
                "style": t["style"],
            }
        )

    # Which traits actually move the needle: median views per trait value,
    # only where we have 2+ posts to compare.
    traits: list[dict] = []
    for key in ("category", "mechanic", "style"):
        buckets: dict[str, list[int]] = {}
        for idea, m in logged:
            value = _traits(idea)[key]
            if value:
                buckets.setdefault(value, []).append(m["views"])
        for value, vs in buckets.items():
            if len(vs) >= 2:
                traits.append(
                    {
                        "trait": key,
                        "value": value,
                        "posts": len(vs),
                        "median_views": int(median(vs)),
                        "vs_baseline": round(median(vs) / baseline, 1) if baseline else 0,
                    }
                )
    traits.sort(key=lambda t: t["median_views"], reverse=True)

    return {
        "posts_logged": len(logged),
        "baseline_views": baseline,
        "top": top,
        "traits": traits,
    }


def performance_notes(store: Store) -> str:
    """The engine-facing block: '' until there's enough data to be honest."""
    digest = insights(store, top_n=3)
    if digest["posts_logged"] < MIN_POSTS_FOR_NOTES:
        return ""

    lines = [
        "PERFORMANCE NOTES — real results from THIS account (median "
        f"{digest['baseline_views']} views across {digest['posts_logged']} logged posts). "
        "Let what actually worked here outweigh generic best practice:",
    ]
    for p in digest["top"]:
        bits = [f"{p['views']} views ({p['multiple']}x baseline)"]
        if p["category"]:
            bits.append(f"category: {p['category']}")
        if p["mechanic"]:
            bits.append(f"mechanic: {p['mechanic']}")
        lines.append(f"- \"{p['hook']}\" — " + ", ".join(bits))
    strong = [t for t in digest["traits"] if t["vs_baseline"] >= 1.5][:3]
    for t in strong:
        lines.append(
            f"- {t['trait']} '{t['value']}' runs {t['vs_baseline']}x baseline "
            f"over {t['posts']} posts — lean into it when it fits"
        )
    return "\n".join(lines)
