"""The UK market spine (Bet 2).

CHRGD is UK-only for now, so Britain is hardcoded — not modelled as one of
several configurable markets (that's the "cross it later" bridge). This module
loads `config/uk_context.toml` and turns it into two prompt-facing pieces:

* `uk_context_block()` — the market pack (named gyms, £ pricing, GMT/BST, British
  spelling) threaded into every discovery scan, take and build so the engine
  reasons UK-native instead of drifting American.
* `uk_calendar_seed()` — the recurring British cultural beats live around *now*,
  seeded into the MOMENTS scan BEFORE web search so it opens knowing what week
  it is in Britain. It seeds CONTEXT, not live fixtures — the scan still
  web-searches for the exact result/headline.

Pure data + string assembly; no LLM calls, no spend. Everything degrades to ''
if the config file is missing, so nothing here can break a build.
"""

from __future__ import annotations

import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path

UK_CONTEXT_FILE = Path(__file__).resolve().parent.parent / "config" / "uk_context.toml"

# The order the market-pack rows read in the prompt (concrete-first).
_CONTEXT_ORDER = ("market", "spelling", "currency", "time", "gyms", "texture")


@lru_cache(maxsize=1)
def _load(path: Path = UK_CONTEXT_FILE) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def uk_context_block() -> str:
    """The UK market pack as a prompt block. '' if the file is missing/empty."""
    ctx = _load().get("context") or {}
    rows = [f"- {ctx[k]}" for k in _CONTEXT_ORDER if str(ctx.get(k, "")).strip()]
    if not rows:
        return ""
    return (
        "UK MARKET SPINE (this brand is British — reason, price and write "
        "UK-native, never American):\n" + "\n".join(rows)
    )


def _md(spec: str) -> tuple[int, int] | None:
    """Parse a 'MM-DD' fragment into a (month, day) tuple, or None if malformed."""
    try:
        mm, dd = spec.split("-")
        m, d = int(mm), int(dd)
    except (ValueError, AttributeError):
        return None
    return (m, d) if 1 <= m <= 12 and 1 <= d <= 31 else None


def _in_window(window: str, today: date) -> bool:
    """Is `today` inside an inclusive 'MM-DD:MM-DD' window? Wrap-around aware
    (a window like '12-27:02-10' spans the year end)."""
    try:
        lo_s, hi_s = window.split(":")
    except (ValueError, AttributeError):
        return False
    lo, hi = _md(lo_s), _md(hi_s)
    if lo is None or hi is None:
        return False
    now = (today.month, today.day)
    if lo <= hi:
        return lo <= now <= hi
    # Wraps the year end: live if we're past the start OR before the end.
    return now >= lo or now <= hi


def uk_calendar_beats(today: date | None = None) -> list[dict]:
    """The curated British beats live around `today` (defaults to today).

    A beat with no window is always live; a windowed beat is live only inside
    its (wrap-aware) month-day range."""
    today = today or date.today()
    beats = []
    for beat in _load().get("calendar") or []:
        if not str(beat.get("title", "")).strip():
            continue
        window = str(beat.get("window", "")).strip()
        if not window or _in_window(window, today):
            beats.append(beat)
    return beats


def uk_calendar_seed(today: date | None = None) -> str:
    """Seed for the MOMENTS scan: the British beats live around now, so the scan
    opens knowing the season instead of paying to rediscover it. '' if none."""
    beats = uk_calendar_beats(today)
    if not beats:
        return ""
    rows = []
    for b in beats:
        title = str(b["title"]).strip()
        note = str(b.get("note", "")).strip()
        rows.append(f"- {title}" + (f" — {note}" if note else ""))
    return (
        "UK CULTURAL CALENDAR (recurring British beats live around now — this is "
        "seeded context, NOT from a live search; treat it as known background and "
        "STILL web-search for the specific current detail: exact fixtures, "
        "results, today's headlines):\n" + "\n".join(rows)
    )
