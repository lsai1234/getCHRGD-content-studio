"""In-app TikTok analytics import — bulk-fill logged results from a CSV export.

The learning loop is only as good as the numbers fed into it, and hand-typing
views for every post is exactly the friction that leaves it starving. TikTok's
desktop analytics (Content tab -> Download data) exports a per-post CSV; this
reads it, matches each row to the post it belongs to by caption, and fills in
`metrics_json` in one go — no LLM, no paid calls.

The parser is deliberately tolerant: TikTok's exact headers drift and differ by
locale/plan, so columns are detected by keyword rather than a fixed schema, and
counts like "1,234" or "1.2K" are normalised. Rows it can't confidently match
are returned as `unmatched` so the editor can log those few by hand.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

from .db import Store
from .learning import METRIC_FIELDS

# Header keyword -> metric. First header containing a keyword wins (a "total …"
# column is preferred when several match). "saves" covers TikTok's several names.
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "views": ("views", "plays"),
    "likes": ("likes",),
    "comments": ("comments",),
    "shares": ("shares",),
    "saves": ("saves", "favorites", "favourites", "bookmarks"),
}
_TITLE_ALIASES = ("title", "caption", "description")

# Require this many matching leading characters (normalised) before we trust a
# caption match — short enough to survive truncation, long enough to be safe.
_MIN_PREFIX = 12
_MAX_PREFIX = 24


def _pick_column(fieldnames: list[str], aliases: tuple[str, ...]) -> str | None:
    matches = [f for f in fieldnames if any(a in f.strip().lower() for a in aliases)]
    if not matches:
        return None
    for f in matches:  # prefer a cumulative "total …" column when present
        if "total" in f.strip().lower():
            return f
    return matches[0]


def _to_int(value: object) -> int:
    """Parse a count that may carry commas or a K/M/B suffix."""
    s = str(value or "").strip().replace(",", "")
    if not s:
        return 0
    mult = 1
    if s[-1:].lower() in ("k", "m", "b"):
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[s[-1].lower()]
        s = s[:-1]
    try:
        return int(round(float(s) * mult))
    except ValueError:
        return 0


def _norm(text: str) -> str:
    """Normalise a caption/title for matching: drop hashtags, punctuation and
    emoji, lowercase, collapse whitespace."""
    s = (text or "").lower()
    s = re.sub(r"#\w+", " ", s)          # hashtags aren't part of the caption body
    s = s.replace("'", "").replace("’", "")  # contractions collapse, not split
    s = re.sub(r"[^a-z0-9 ]", " ", s)    # remaining punctuation + emoji out
    return re.sub(r"\s+", " ", s).strip()


def parse_tiktok_csv(text: str) -> list[dict]:
    """Parse a TikTok analytics CSV into ``[{title, metrics}]`` rows.

    Rows with no non-zero metric are skipped. Unknown metric columns are simply
    absent from `metrics` rather than an error.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    cols = {m: _pick_column(reader.fieldnames, a) for m, a in _METRIC_ALIASES.items()}
    title_col = _pick_column(reader.fieldnames, _TITLE_ALIASES)
    rows: list[dict] = []
    for r in reader:
        metrics = {
            m: _to_int(r.get(col, "")) for m, col in cols.items() if col
        }
        if not any(metrics.values()):
            continue  # a header echo or an empty trailing line
        title = (r.get(title_col) or "").strip() if title_col else ""
        rows.append({"title": title, "metrics": metrics})
    return rows


def _match(rows: list[dict], ideas: list) -> tuple[list[tuple], list[str]]:
    """Pair parsed rows with ideas by caption prefix. Returns (matched, unmatched).

    `matched` is ``[(idea, metrics)]``; each idea is used at most once (the first
    row that claims it wins), so two posts with near-identical captions can't
    both grab the same row.
    """
    normed = [(i, _norm(i.caption or "")) for i in ideas]
    used: set[str] = set()
    matched: list[tuple] = []
    unmatched: list[str] = []
    for row in rows:
        rt = _norm(row["title"])
        best = None
        if rt:
            for idea, ncap in normed:
                if idea.idea_id in used or not ncap:
                    continue
                k = min(len(rt), len(ncap), _MAX_PREFIX)
                if k >= _MIN_PREFIX and rt[:k] == ncap[:k]:
                    best = idea
                    break
        if best is None:
            unmatched.append((row["title"] or "(untitled)")[:60])
        else:
            used.add(best.idea_id)
            matched.append((best, row["metrics"]))
    return matched, unmatched


def import_tiktok_csv(store: Store, text: str) -> dict:
    """Parse a TikTok analytics CSV and merge results into the matched posts.

    Merges (never clobbers) so any one-tap rating already logged is preserved.
    Only posts that already have a caption are matching candidates.
    """
    rows = parse_tiktok_csv(text)
    if not rows:
        return {"rows": 0, "updated": [], "unmatched": [], "error": "no rows found in CSV"}

    candidates = [i for i in store.list_ideas() if (i.caption or "").strip()]
    matched, unmatched = _match(rows, candidates)

    now = datetime.now(timezone.utc).isoformat()
    updated: list[str] = []
    for idea, metrics in matched:
        patch = {f: metrics[f] for f in METRIC_FIELDS if f in metrics}
        patch["logged_at"] = now
        patch["source"] = "tiktok_csv"
        store.merge_metrics(idea.idea_id, patch)
        updated.append(idea.idea_id)

    return {"rows": len(rows), "updated": updated, "unmatched": unmatched}
