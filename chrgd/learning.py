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

# One-tap outcome — the low-friction signal that actually gets logged.
RATINGS = {"hit", "meh", "flop"}

# Need at least this many logged posts before we claim to know anything.
MIN_POSTS_FOR_NOTES = 3
# A trait needs at least this many rated posts before we'll call it a pattern
# (small-n honesty — we don't pretend n=1 means anything).
MIN_TRAIT_POSTS = 2

# Categories whose wins are TOPICAL, not a repeatable formula — copying the
# post won't work, you re-run the radar for the next one.
_TOPICAL_CATEGORIES = {"moment", "trend", "trending"}


def _rating(idea: Idea) -> str | None:
    try:
        r = json.loads(idea.metrics_json or "{}").get("rating")
    except json.JSONDecodeError:
        return None
    return r if r in RATINGS else None


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


# The trait keys we hunt for winning/flopping patterns across — each post has
# exactly one value per key. "hook"/"style" are kept on the post for display but
# left out of the skew search (hook is free text; style is now almost always
# blank since the picker was removed). Pillar, engagement play and slide-count
# bucket are the levers the rubric says actually drive reach.
SKEW_TRAITS = (
    "category", "mechanic", "pillar", "engagement_play", "slide_count", "casting",
)


def _slide_bucket(idea: Idea) -> str:
    """Coarse slide-count band, so 5- and 6-slide posts compare as one shape."""
    try:
        n = len(json.loads(idea.slides_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        return ""
    if n <= 0:
        return ""
    if n <= 3:
        return "1-3 slides"
    if n <= 6:
        return "4-6 slides"
    return "7-10 slides"


def _pillar(route: dict) -> str:
    """The content pillar for a post — from the chosen take, else route-level."""
    take = route.get("take")
    if isinstance(take, dict) and take.get("pillar"):
        return take["pillar"]
    return route.get("pillar", "")


def _traits(idea: Idea) -> dict:
    """The comparable features of one post."""
    route = _route(idea)
    return {
        "category": idea.content_category or "uncategorised",
        "mechanic": route.get("mechanic", ""),
        "style": route.get("style", ""),
        "pillar": _pillar(route),
        "engagement_play": route.get("engagement_play", ""),
        "slide_count": _slide_bucket(idea),
        "casting": route.get("casting_intensity", ""),
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
        return {"posts_logged": 0, "baseline_views": 0, "top": [], "traits": [],
                "scroll_calibration": scroll_calibration(store),
                **rating_summary(store)}

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
    for key in SKEW_TRAITS:
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
        "scroll_calibration": scroll_calibration(store),
        **rating_summary(store),
    }


def rating_summary(store: Store) -> dict:
    """The honest, low-friction signal: 🔥/😐/💀 counts + which traits skew
    toward hits vs flops. Coarser than view maths, but far more reliable at
    small-account scale and it's what actually gets logged."""
    rated = [(i, _rating(i)) for i in store.ideas_with_metrics()]
    rated = [(i, r) for i, r in rated if r]
    counts = {"hit": 0, "meh": 0, "flop": 0}
    for _, r in rated:
        counts[r] += 1

    # Per trait-value: how many hits vs flops. Only report where we have enough
    # rated posts to mean anything.
    def _skew(key: str) -> list[dict]:
        buckets: dict[str, dict] = {}
        for idea, r in rated:
            value = _traits(idea)[key]
            if not value:
                continue
            b = buckets.setdefault(value, {"hit": 0, "meh": 0, "flop": 0})
            b[r] += 1
        out = []
        for value, b in buckets.items():
            total = b["hit"] + b["meh"] + b["flop"]
            if total >= MIN_TRAIT_POSTS:
                out.append({
                    "trait": key, "value": value, "hits": b["hit"],
                    "flops": b["flop"], "total": total,
                })
        return out

    all_skew = [t for key in SKEW_TRAITS for t in _skew(key)]
    hit_traits = sorted(
        [t for t in all_skew if t["hits"] > t["flops"]],
        key=lambda t: (t["hits"] - t["flops"], t["hits"]), reverse=True,
    )
    flop_traits = sorted(
        [t for t in all_skew if t["flops"] > t["hits"]],
        key=lambda t: (t["flops"] - t["hits"], t["flops"]), reverse=True,
    )
    return {
        "rated_count": len(rated),
        "ratings": counts,
        "hit_traits": hit_traits,
        "flop_traits": flop_traits,
    }


def scroll_calibration(store: Store) -> dict:
    """How well the pre-render scroll test predicted real outcomes.

    Among posts that carry BOTH a cold-scroll-test verdict and a hit/flop
    rating, count how often the judge agreed with reality (a 'stop' that hit,
    or a 'scroll' that flopped). Closes the loop: it tells the editor whether
    the studio's own judge is worth trusting yet, without any ML.
    """
    n = agreed = 0
    for idea in store.ideas_with_metrics():
        rating = _rating(idea)
        verdict = (_route(idea).get("scroll_verdict") or {}).get("verdict")
        if rating in ("hit", "flop") and verdict in ("stop", "scroll"):
            n += 1
            if (verdict == "stop" and rating == "hit") or (
                verdict == "scroll" and rating == "flop"
            ):
                agreed += 1
    return {"n": n, "agreed": agreed}


def _trait_phrase(t: dict) -> str:
    return f"{t['value']} ({t['trait']})"


def performance_notes(store: Store) -> str:
    """The engine-facing block: '' until there's enough data to be honest.

    Rating-first (the reliable signal), views only as backup. Deliberately
    coarse — it nudges the engine toward the shapes that have hit and away
    from those that flop, without pretending small-n stats are significant.
    """
    rs = rating_summary(store)
    hit_traits, flop_traits = rs["hit_traits"], rs["flop_traits"]

    if rs["rated_count"] >= MIN_POSTS_FOR_NOTES and (hit_traits or flop_traits):
        lines = [
            "WHAT WORKS FOR THIS ACCOUNT (from the editor's own hit/flop "
            f"ratings on {rs['rated_count']} posts — lean this way where it "
            "fits the idea, it's a nudge not a rule):",
        ]
        if hit_traits:
            hits = ", ".join(
                f"{_trait_phrase(t)} [{t['hits']}/{t['total']} hit]"
                for t in hit_traits[:3]
            )
            lines.append(f"- Tends to HIT: {hits}. Favour these shapes.")
        if flop_traits:
            flops = ", ".join(
                f"{_trait_phrase(t)} [{t['flops']}/{t['total']} flop]"
                for t in flop_traits[:3]
            )
            lines.append(f"- Tends to FLOP: {flops}. Avoid or sharpen hard.")
        # The honest caveat the editor flagged: topical wins aren't repeatable.
        if any(t["value"] in _TOPICAL_CATEGORIES for t in hit_traits):
            lines.append(
                "- Note: 'moment'/'trend' wins came from a live cultural moment, "
                "not a copyable formula — bring that energy, but the topic must "
                "be its own fresh, currently-relevant moment."
            )
        return "\n".join(lines)

    # Fallback: view-based top posts, only once enough have real numbers.
    digest = insights(store, top_n=3)
    viewed = [p for p in digest["top"] if p["views"] > 0]
    if len(viewed) < MIN_POSTS_FOR_NOTES:
        return ""
    lines = [
        "WHAT WORKS FOR THIS ACCOUNT (top posts by views — lean this way where "
        "it fits, not a rule):",
    ]
    for p in viewed:
        bits = [f"{p['multiple']}x average"]
        if p["category"]:
            bits.append(p["category"])
        if p["mechanic"]:
            bits.append(p["mechanic"])
        lines.append(f"- \"{p['hook']}\" — " + ", ".join(bits))
    return "\n".join(lines)
