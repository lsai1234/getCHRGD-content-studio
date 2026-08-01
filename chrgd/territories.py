"""LIVE WIRE's interest territories — what the topical scout actually scans.

The scout's stages are fine: `trends.scout_discover` surfaces signal and
`concepts` makes the gym leap from it. The **input** was the problem. Scanning
"what's trending in the UK" returns the stories every account is already
posting, so the leap starts from material the audience scrolled past twice
yesterday — which is exactly why the trending lane reads generic (D9).

A territory replaces the country with an audience: named, weighted slices of
what an 18-30 UK gym-goer is genuinely into. Deliberately broader than fitness,
because the person who trains five times a week also watches the same telly and
books the same holidays as everyone else. The gym leap is still the product;
this just stops it starting from the same three headlines.

Definitions live in `config/territories.toml` and load the way every other
config here does — cached, permissive, empty registry if the file is gone (in
which case the scout falls back to its old country-wide scan and nothing
breaks).
"""

from __future__ import annotations

import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

TERRITORIES_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "territories.toml"
)


class Territory(BaseModel):
    key: str
    label: str
    probe: str = ""
    #: Relative share of a scan's slots — not a percentage.
    weight: int = 1
    #: Months (1-12) this territory is live in. Empty = all year.
    months: list[int] = Field(default_factory=list)

    def in_season(self, when: date | None = None) -> bool:
        if not self.months:
            return True
        return (when or date.today()).month in self.months


@lru_cache(maxsize=1)
def load_territories(path: Path = TERRITORIES_FILE) -> dict[str, Territory]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        key: Territory(key=key, **spec)
        for key, spec in (data.get("territories") or {}).items()
    }


def get_territory(key: str) -> Territory | None:
    if not key:
        return None
    return load_territories().get(key)


def in_season(when: date | None = None) -> list[Territory]:
    """The territories live right now, heaviest first."""
    return sorted(
        (t for t in load_territories().values() if t.in_season(when)),
        key=lambda t: (-t.weight, t.label),
    )


def allocate(count: int, *, keys: list[str] | None = None,
             when: date | None = None) -> list[tuple[Territory, int]]:
    """How many of a scan's `count` slots each territory gets.

    Weighted by `weight`, with a floor of one slot each so a light territory
    still appears rather than being rounded out of existence — the point of the
    model is breadth, and a territory that never surfaces is just a comment.
    Any remainder goes to the heaviest territories.

    `keys` narrows to an explicit selection (the editor picking territories on
    the Live Wire screen); unknown or out-of-season keys are dropped.
    """
    live = in_season(when)
    if keys:
        chosen = [t for t in live if t.key in set(keys)]
        live = chosen or live
    if not live or count < 1:
        return []
    # More territories than slots: take the heaviest that fit, one each.
    if len(live) >= count:
        return [(t, 1) for t in live[:count]]

    total = sum(t.weight for t in live) or len(live)
    spare = count - len(live)
    out = [(t, 1 + spare * t.weight // total) for t in live]
    # Hand whatever integer division dropped to the heaviest territories.
    leftover = count - sum(n for _t, n in out)
    for i in range(leftover):
        t, n = out[i % len(out)]
        out[i % len(out)] = (t, n + 1)
    return out


def scan_brief(count: int, *, keys: list[str] | None = None,
               when: date | None = None) -> str:
    """The territory brief appended to the scout's ask.

    Returns '' when there are no territories, which is what lets the scout fall
    back to its previous country-wide behaviour untouched.
    """
    plan = allocate(count, keys=keys, when=when)
    if not plan:
        return ""
    lines = [
        "SCAN THESE TERRITORIES, not 'what's trending in the UK' generally. "
        "This account's audience is 18-30, UK, into the gym — and into "
        "everything else people that age are into. Generic national headlines "
        "everyone is already posting are exactly what to avoid.",
        "",
        "Bring back this many stories from each:",
    ]
    for territory, n in plan:
        lines.append(f"- {territory.label} — {n} " + ("story" if n == 1 else "stories"))
        lines.append(f"  {territory.probe.strip()}")
    lines.append("")
    lines.append(
        "Every story must be REAL, current and dateable. A story that isn't "
        "about fitness is fine and wanted — the gym angle is invented "
        "afterwards, and a story this audience already cares about makes a "
        "far better leap than one they don't."
    )
    return "\n".join(lines)
