"""Last mile — Metricool CSV export (milestone 4, the PRIMARY OUTPUT).

Produces a Metricool bulk-import CSV plus a matching `output/ready/` asset
folder: one row per scheduled post with date/time, target networks, caption,
and media references. Column headers and the filename-vs-URL media style are
read from `config/metricool_columns.toml` (they differ by Metricool plan), and
`export --sample` emits a sample CSV to diff against Metricool's own template.

Rows are stamped `exported_at` in SQLite so nothing exports twice.
"""

from __future__ import annotations

import csv
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Protocol

try:  # Python 3.9+ stdlib; present on 3.11
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from pydantic import BaseModel, Field

from .config import Settings
from .db import Store
from .models import Idea, PostType, Status

COLUMNS_FILE = Path(__file__).resolve().parent.parent / "config" / "metricool_columns.toml"


# --- config models ----------------------------------------------------------


class FormatCfg(BaseModel):
    datetime_format: str = "%d/%m/%Y %H:%M"
    caption_max_length: int = 2200
    media_reference: str = "filename"
    media_base_url: str = ""
    network_on_value: str = "TRUE"
    draft_true: str = "TRUE"
    draft_false: str = "FALSE"


class HeaderCfg(BaseModel):
    text: str = "Text"
    datetime: str = "Date"
    draft: str = "Draft"


class MediaCfg(BaseModel):
    # Ten picture columns cover the longest Playbook carousel; Sketch rows
    # just leave the trailing columns blank.
    image_columns: list[str] = Field(
        default_factory=lambda: [f"Picture {i}" for i in range(1, 11)]
    )
    video_column: str = "Video"


class ScheduleCfg(BaseModel):
    timezone: str = "Europe/London"
    start_offset_days: int = 1
    times: list[str] = Field(default_factory=lambda: ["18:00"])
    per_day: int = 1


class TargetsCfg(BaseModel):
    default_networks: list[str] = Field(default_factory=lambda: ["tiktok", "instagram"])


class MetricoolColumns(BaseModel):
    format: FormatCfg = Field(default_factory=FormatCfg)
    header: HeaderCfg = Field(default_factory=HeaderCfg)
    networks: dict[str, str] = Field(
        default_factory=lambda: {"tiktok": "Tiktok", "instagram": "Instagram"}
    )
    media: MediaCfg = Field(default_factory=MediaCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)
    targets: TargetsCfg = Field(default_factory=TargetsCfg)


def load_columns(path: Path = COLUMNS_FILE) -> MetricoolColumns:
    if path.exists():
        return MetricoolColumns.model_validate(tomllib.loads(path.read_text("utf-8")))
    return MetricoolColumns()


# --- helpers ----------------------------------------------------------------


def build_caption(caption: str, hashtags: list[str], max_length: int) -> str:
    """Append hashtags inline, strip line breaks, respect the length limit.

    TikTok's API doesn't support line breaks, so newlines collapse to spaces.
    """
    text = " ".join((caption or "").split())  # collapse all whitespace/newlines
    tag_str = " ".join(
        t if t.startswith("#") else f"#{t}" for t in hashtags if t
    ).strip()
    combined = f"{text} {tag_str}".strip() if tag_str else text
    if len(combined) > max_length:
        combined = combined[: max_length - 1].rstrip() + "…"
    return combined


def _schedule_datetimes(cfg: ScheduleCfg, count: int) -> list[datetime]:
    """Generate `count` posting datetimes spread across upcoming days."""
    tz = ZoneInfo(cfg.timezone) if ZoneInfo else None
    today = datetime.now(tz).date()
    start = today + timedelta(days=cfg.start_offset_days)
    times = [time.fromisoformat(t) for t in cfg.times] or [time(18, 0)]
    out: list[datetime] = []
    for i in range(count):
        day = start + timedelta(days=i // cfg.per_day)
        t = times[i % len(times)]
        dt = datetime.combine(day, t)
        out.append(dt.replace(tzinfo=tz) if tz else dt)
    return out


# --- publisher interface ----------------------------------------------------


@dataclass
class ExportResult:
    csv_path: str | None = None
    ready_dir: str | None = None
    # Manual-steps sheet: pinned comments to post right after each post goes
    # live (Metricool can't pin comments, so this is a human step).
    pinned_comments_path: str | None = None
    exported_ids: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)


class Publisher(Protocol):
    def export(self, store: Store, settings: Settings, *, limit: int | None) -> ExportResult: ...


class MetricoolCSVPublisher:
    """Default backend: bulk-import CSV + ready/ asset folder."""

    def __init__(self, columns: MetricoolColumns | None = None):
        self.cols = columns or load_columns()

    def _ordered_headers(self, include_video: bool) -> list[str]:
        c = self.cols
        headers = [c.header.text, c.header.datetime, c.header.draft]
        headers += list(c.networks.values())
        headers += c.media.image_columns
        if include_video:
            headers.append(c.media.video_column)
        return headers

    def _media_ref(self, filename: str) -> str:
        if self.cols.format.media_reference == "url" and self.cols.format.media_base_url:
            base = self.cols.format.media_base_url.rstrip("/")
            return f"{base}/{filename}"
        return filename

    def _row_for(self, idea: Idea, when: datetime, ready_dir: Path) -> dict[str, str]:
        import json

        c = self.cols
        row: dict[str, str] = {}
        hashtags = json.loads(idea.hashtags) if idea.hashtags else []
        row[c.header.text] = build_caption(
            idea.caption or "", hashtags, c.format.caption_max_length
        )
        row[c.header.datetime] = when.strftime(c.format.datetime_format)
        row[c.header.draft] = c.format.draft_false

        for net_col in c.networks.values():
            row[net_col] = ""
        for net in c.targets.default_networks:
            col = c.networks.get(net)
            if col:
                row[col] = c.format.network_on_value

        # Copy assets into ready/ with lined-up names, reference them per config.
        paths = json.loads(idea.asset_paths_json) if idea.asset_paths_json else []
        is_video = idea.post_type == PostType.video
        if is_video and paths:
            fname = f"{idea.idea_id}{Path(paths[0]).suffix}"
            shutil.copyfile(paths[0], ready_dir / fname)
            row[c.media.video_column] = self._media_ref(fname)
        else:
            for i, src in enumerate(paths[: len(c.media.image_columns)]):
                fname = f"{idea.idea_id}_slide_{i + 1}{Path(src).suffix}"
                shutil.copyfile(src, ready_dir / fname)
                row[c.media.image_columns[i]] = self._media_ref(fname)
        return row

    def _selectable(self, store: Store, limit: int | None) -> list[Idea]:
        import json

        out: list[Idea] = []
        for idea in store.list_ideas(status=Status.done):
            if idea.exported_at:
                continue
            if not idea.asset_paths_json or not json.loads(idea.asset_paths_json):
                continue
            out.append(idea)
            if limit and len(out) >= limit:
                break
        return out

    def export(
        self, store: Store, settings: Settings, *, limit: int | None = None
    ) -> ExportResult:
        result = ExportResult()

        # Flag done-but-unrendered posts so the user knows why they're absent.
        import json

        for idea in store.list_ideas(status=Status.done):
            if not idea.exported_at and (
                not idea.asset_paths_json or not json.loads(idea.asset_paths_json)
            ):
                result.skipped.append((idea.idea_id, "not rendered — run `chrgd render`"))

        ideas = self._selectable(store, limit)
        if not ideas:
            return result

        ready_dir = Path(settings.output_dir) / "ready"
        ready_dir.mkdir(parents=True, exist_ok=True)

        whens = [
            idea.scheduled_for or dt
            for idea, dt in zip(
                ideas, _schedule_datetimes(self.cols.schedule, len(ideas))
            )
        ]
        include_video = any(i.post_type == PostType.video for i in ideas)
        headers = self._ordered_headers(include_video)

        rows = [self._row_for(idea, when, ready_dir) for idea, when in zip(ideas, whens)]

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = ready_dir / f"metricool_{stamp}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

        pinned_path = self._write_pinned_comments(ideas, whens, ready_dir, stamp)
        result.pinned_comments_path = pinned_path

        for idea, when in zip(ideas, whens):
            store.conn.execute(
                "UPDATE ideas SET scheduled_for = ? WHERE idea_id = ?",
                (when.isoformat(), idea.idea_id),
            )
            store.mark_exported(idea.idea_id)
            result.exported_ids.append(idea.idea_id)
        store.conn.commit()

        result.csv_path = str(csv_path)
        result.ready_dir = str(ready_dir)
        return result

    def _write_pinned_comments(
        self,
        ideas: list[Idea],
        whens: list[datetime],
        ready_dir: Path,
        stamp: str,
    ) -> str | None:
        """Write the manual pinned-comments sheet alongside the CSV.

        Metricool publishes the post but can't pin comments, so this file is
        the checklist: after each post goes live, paste + pin these.
        """
        import json

        entries: list[str] = []
        for idea, when in zip(ideas, whens):
            comments = (
                json.loads(idea.pinned_comments_json)
                if idea.pinned_comments_json
                else []
            )
            if not comments:
                continue
            lines = [f"{idea.idea_id} — scheduled {when.strftime('%d/%m/%Y %H:%M')}"]
            lines += [f"  pin {i}: {c}" for i, c in enumerate(comments, 1)]
            entries.append("\n".join(lines))
        if not entries:
            return None

        path = ready_dir / f"metricool_{stamp}_pinned_comments.txt"
        header = (
            "Pinned comments — post and pin these right after each post goes "
            "live (Metricool can't do this bit).\n"
        )
        path.write_text(header + "\n" + "\n\n".join(entries) + "\n", encoding="utf-8")
        return str(path)

    def write_sample(self, settings: Settings) -> str:
        """Emit a one-row sample CSV to diff against Metricool's template."""
        ready_dir = Path(settings.output_dir) / "ready"
        ready_dir.mkdir(parents=True, exist_ok=True)
        headers = self._ordered_headers(include_video=True)
        c = self.cols
        sample = {h: "" for h in headers}
        sample[c.header.text] = "Example caption with hashtags #gym #uk"
        sample[c.header.datetime] = datetime.now().strftime(c.format.datetime_format)
        sample[c.header.draft] = c.format.draft_false
        for net in c.targets.default_networks:
            if net in c.networks:
                sample[c.networks[net]] = c.format.network_on_value
        for i, col in enumerate(c.media.image_columns, 1):
            sample[col] = self._media_ref(f"G-0001_slide_{i}.jpg")
        path = ready_dir / "metricool_sample.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            writer.writerow(sample)
        return str(path)


def get_publisher(name: str = "metricool_csv") -> Publisher:
    """Publisher factory — future backends (unified_api, tiktok_direct) slot here."""
    if name == "metricool_csv":
        return MetricoolCSVPublisher()
    raise ValueError(f"unknown publisher backend: {name}")
