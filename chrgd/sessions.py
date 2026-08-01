"""THE SESSION's variant matrix, and the coverage tracker that nudges it.

The show's whole identity is that it's segmented — over a month it should reach
the beginner in a hotel room and the experienced lifter in the 6pm rush, not
shout at one slice of the audience every week. So a session is defined by a
point in a small matrix (goal · when · how · where · level · supplement) rather
than by a topic, and the build brief is written from that point.

Two decisions shape this module and both are load-bearing:

* **D11 — goal-framed, never gender-labelled.** The "who" axis is a GOAL. A
  glute-focused session finds its audience without a "for the girls" label and
  stays useful to everybody else, and it segments better for the algorithm.
  There is deliberately no gender option to pick, so a post can't accidentally
  acquire one.
* **D12 — coverage nudges, it never enforces.** `stale_options` reports what
  hasn't been covered lately so the create screen can suggest it. Nothing here
  blocks, reorders or auto-selects: two lower-body sessions in a week is a
  legitimate editorial choice, and a rota that argues with the operator gets
  switched off.
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

VARIANTS_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "session_variants.toml"
)

#: Route key the chosen variant rides on.
ROUTE_KEY = "session_variant"


class Axis(BaseModel):
    key: str
    label: str
    options: dict[str, str] = Field(default_factory=dict)
    required: bool = False
    staleness_days: int = 28

    def label_for(self, option: str) -> str:
        return self.options.get(option, "")


@lru_cache(maxsize=1)
def load_axes(path: Path = VARIANTS_FILE) -> dict[str, Axis]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        key: Axis(key=key, **spec)
        for key, spec in (data.get("axes") or {}).items()
    }


def get_axis(key: str) -> Axis | None:
    return load_axes().get(key)


def validate(variant: dict) -> dict:
    """Keep only known axes and known options.

    Unknown keys are dropped rather than raising: a variant is an editorial
    choice, and a stale bookmark or a renamed option shouldn't 500 the journey.
    Callers that need strictness (the API) check `missing_required` instead.
    """
    axes = load_axes()
    out: dict[str, str] = {}
    for key, option in (variant or {}).items():
        axis = axes.get(str(key))
        if axis and str(option) in axis.options:
            out[str(key)] = str(option)
    return out


def missing_required(variant: dict) -> list[str]:
    """Required axes this variant hasn't chosen — the labels, for a message."""
    chosen = validate(variant)
    return [a.label for a in load_axes().values() if a.required and a.key not in chosen]


def describe(variant: dict) -> list[str]:
    """The variant as human lines, in the matrix's own order."""
    chosen = validate(variant)
    return [
        f"{axis.label}: {axis.label_for(chosen[axis.key])}"
        for axis in load_axes().values()
        if axis.key in chosen
    ]


def brief_block(variant: dict) -> str:
    """The variant, as the instruction THE SESSION's build is written from."""
    lines = describe(variant)
    if not lines:
        return ""
    out = [
        "THIS SESSION'S VARIANT — write for exactly this person and this "
        "session, and say who it's for on slide 1:",
    ]
    out += [f"- {line}" for line in lines]
    chosen = validate(variant)
    if chosen.get("where") == "no_equipment":
        out.append(
            "NO EQUIPMENT AT ALL: every exercise must need nothing but a floor "
            "and body weight. No bands, no dumbbells, no bench, no pull-up bar."
        )
    if chosen.get("supplement") == "none":
        out.append(
            "NO SUPPLEMENT TIE-IN this week: drop that slide and give the space "
            "to the session. A forced product mention is worse than none."
        )
    if chosen.get("level") == "first_month":
        out.append(
            "FIRST MONTH: assume no training history whatsoever. Name every "
            "exercise plainly, say what it should feel like, and never assume "
            "they know a piece of kit by its name."
        )
    return "\n".join(out)


# --- coverage: the nudge (D12) ----------------------------------------------


def recent_variants(store, *, days: int = 60) -> list[tuple[dict, datetime]]:
    """Variants used by recent SESSION posts, newest first."""
    rows = store.conn.execute(
        "SELECT route_json, created_at FROM ideas "
        "WHERE route_json LIKE ? ORDER BY idea_id DESC LIMIT 60",
        (f'%"{ROUTE_KEY}"%',),
    ).fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[tuple[dict, datetime]] = []
    for row in rows:
        try:
            route = json.loads(row["route_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        variant = route.get(ROUTE_KEY)
        if not isinstance(variant, dict):
            continue
        try:
            when = datetime.fromisoformat(str(row["created_at"]))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            when = datetime.now(timezone.utc)
        if when >= cutoff:
            out.append((validate(variant), when))
    return out


def stale_options(store, *, now: datetime | None = None) -> list[dict]:
    """Options that haven't been covered inside their staleness window.

    A SUGGESTION (D12). The create screen shows these as "not done in a while";
    nothing here excludes anything, and a covered option stays as selectable as
    it ever was.
    """
    now = now or datetime.now(timezone.utc)
    recent = recent_variants(store)
    last_used: dict[tuple[str, str], datetime] = {}
    for variant, when in recent:
        for axis_key, option in variant.items():
            key = (axis_key, option)
            if key not in last_used or when > last_used[key]:
                last_used[key] = when

    out: list[dict] = []
    for axis in load_axes().values():
        for option, label in axis.options.items():
            seen = last_used.get((axis.key, option))
            days = None if seen is None else (now - seen).days
            if days is None or days >= axis.staleness_days:
                out.append({
                    "axis": axis.key, "axis_label": axis.label,
                    "option": option, "label": label,
                    "days_since": days,
                })
    # Never-used first, then longest-since — the order the editor cares about.
    out.sort(key=lambda o: (o["days_since"] is not None, -(o["days_since"] or 0)))
    return out


def suggestion(store, *, now: datetime | None = None) -> str:
    """One short line for the create screen, or '' when nothing's gone stale."""
    stale = stale_options(store, now=now)
    if not stale:
        return ""
    top = stale[0]
    if top["days_since"] is None:
        return f"You've not done a “{top['label']}” session yet."
    return f"No “{top['label']}” session in {top['days_since']} days."
