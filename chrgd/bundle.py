"""Bulk download — every post's content, one folder each, in one zip.

The studio already hands you a post at a time: the manual-post modal downloads
the slides one by one and copies the caption to the clipboard. That is fine for
the post you are about to put up and useless for the other forty. When you want
the *whole* body of work — to hand it to an editor, to back it up off the box,
to move it to another machine — you want one file.

This builds that file. The zip has one folder per post, named so the folders
sort in posting order and read as content rather than as ids::

    chrgd_posts_20260824_1430/
      README.txt                  what's in here, and when it was made
      index.csv                   one row per post (id, date, hook, files…)
      001_G-0007_the-gym-lied-to-you/
        slide_01.jpg              the rendered carousel, in slide order
        slide_02.jpg
        caption.txt               caption + blank line + hashtags, paste-ready
        first_comment.txt         the comment to pin (when the post has one)
        post.md                   the whole post as readable copy
        post.json                 the full record, for anything programmatic
        video.mp4                 (video posts)
      002_G-0009_.../

Everything is derived from what the database and `output/<idea_id>/` already
hold, so this reads and never writes into the studio's own state — running it
twice is free, and it can't disturb an export.

Two safety rules matter here, because this walks paths that came out of a
database and copies them somewhere a user will open:

* An asset is only ever added if it resolves **inside** `output_dir`. A row
  carrying an absolute path to somewhere else on the box is skipped, not zipped.
* Names inside the archive are rebuilt from scratch (`slide_01.jpg`, a slugged
  folder), never taken from a stored string, so nothing can write outside the
  folder it was extracted into.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Iterable, Sequence

from .config import Settings
from .db import Store
from .logging_setup import get_logger
from .models import Idea, Status

log = get_logger("bundle")

#: Media that belongs to a post as *content* (as opposed to working files).
_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".gif"}

#: How long a slugged hook may run inside a folder name. Long enough to tell
#: two posts apart at a glance, short enough that the full path stays under
#: Windows' 260-character limit once the user extracts it three levels deep.
_SLUG_MAX = 48


@dataclass
class BundleEntry:
    """One post's folder in the archive."""

    idea_id: str
    folder: str
    hook: str = ""
    status: str = ""
    scheduled_for: str = ""
    images: int = 0
    files: int = 0
    bytes: int = 0


@dataclass
class BundleResult:
    """What went into the zip, and what didn't."""

    name: str = ""
    entries: list[BundleEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def posts(self) -> int:
        return len(self.entries)

    @property
    def files(self) -> int:
        return sum(e.files for e in self.entries)

    @property
    def bytes(self) -> int:
        return sum(e.bytes for e in self.entries)

    def summary(self) -> str:
        parts = [f"{self.posts} post(s)", f"{self.files} file(s)", _human(self.bytes)]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_name(text: str) -> str:
    """A single path segment, with every way out of it removed.

    Ids reach here from a URL and from database rows, and they end up as
    folder names inside an archive someone will extract. Nothing but letters,
    digits, dash and underscore survives, so an id can never climb out of the
    folder it was extracted into.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip(".-")
    return cleaned or "post"


def _slug(text: str) -> str:
    """A short, filesystem-safe fragment of a hook — ascii, lowercase, dashed."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(cleaned) > _SLUG_MAX:
        cleaned = cleaned[:_SLUG_MAX].rsplit("-", 1)[0] or cleaned[:_SLUG_MAX]
    return cleaned.strip("-")


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _hashtag_line(idea: Idea) -> str:
    tags = _loads(idea.hashtags, [])
    if not isinstance(tags, list):
        return ""
    return " ".join(t if str(t).startswith("#") else f"#{t}" for t in tags if t).strip()


def caption_text(idea: Idea) -> str:
    """The caption exactly as it should be pasted: copy, blank line, hashtags.

    Same shape the manual-post modal puts on the clipboard, so what comes out
    of the zip and what comes out of the studio are the same text.
    """
    caption = (idea.caption or "").strip()
    tags = _hashtag_line(idea)
    return caption + (f"\n\n{tags}" if tags else "")


def _sort_key(idea: Idea) -> tuple:
    """Posting order: scheduled posts by slot, then the rest by capture date.

    Unscheduled posts sort *after* scheduled ones — a folder listing then opens
    on the run of work that has a date on it, with the tray behind it.
    """
    when = idea.scheduled_for
    return (
        0 if when else 1,
        when.replace(tzinfo=None) if when else idea.created_at.replace(tzinfo=None),
        idea.idea_id,
    )


def is_built(idea: Idea) -> bool:
    """Has this idea got a post on it, as opposed to being a bare seed?"""
    return bool((idea.hook or "").strip() or idea.slides_json or (idea.caption or "").strip())


# --- selection --------------------------------------------------------------


def select(
    store: Store,
    *,
    ids: Sequence[str] | None = None,
    status: Status | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int | None = None,
) -> list[Idea]:
    """The posts a bundle should contain, in posting order.

    `ids` names posts explicitly (and then nothing else filters them — asking
    for a post by id means you want that post). Otherwise `status` and the
    scheduled-date window narrow the backlog. `date_to` is exclusive, so the
    caller adds a day for an inclusive calendar range, exactly as the CSV
    export route does.
    """
    if ids:
        wanted = list(dict.fromkeys(ids))  # de-dupe, keep the caller's order
        found = {i: store.get_idea(i) for i in wanted}
        return [found[i] for i in wanted if found[i] is not None]

    ideas = store.list_ideas(status=status)
    if date_from or date_to:
        picked = []
        for idea in ideas:
            when = idea.scheduled_for
            if when is None:
                continue
            naive = when.replace(tzinfo=None)
            if date_from and naive < date_from.replace(tzinfo=None):
                continue
            if date_to and naive >= date_to.replace(tzinfo=None):
                continue
            picked.append(idea)
        ideas = picked
    ideas.sort(key=_sort_key)
    return ideas[:limit] if limit else ideas


# --- the readable brief -----------------------------------------------------


def post_markdown(idea: Idea) -> str:
    """The whole post as one readable document — copy first, context after."""
    route = _loads(idea.route_json, {}) or {}
    slides = _loads(idea.slides_json, []) or []
    lines: list[str] = []

    title = (idea.hook or idea.concept_note or idea.idea_id).strip()
    lines += [f"# {title}", ""]

    meta = [
        ("ID", idea.idea_id),
        ("Status", idea.status.value if isinstance(idea.status, Status) else str(idea.status)),
        ("Type", idea.post_type.value if idea.post_type else "carousel"),
        ("Scheduled", idea.scheduled_for.strftime("%d %b %Y %H:%M") if idea.scheduled_for else "—"),
        ("Category", idea.content_category or "—"),
        ("Show", str(route.get("show") or "—")),
        ("Mechanic", str(route.get("mechanic") or "—")),
    ]
    lines += [f"- **{k}:** {v}" for k, v in meta] + [""]

    if slides:
        lines += ["## Slides", ""]
        for n, slide in enumerate(slides, 1):
            if not isinstance(slide, dict):
                continue
            role = (slide.get("role") or "").strip()
            head = f"### Slide {n}" + (f" — {role}" if role else "")
            lines += [head, "", (slide.get("headline") or "").strip()]
            for key in ("supporting", "body"):
                text = (slide.get(key) or "").strip()
                if text:
                    lines += ["", text]
            trigger = (slide.get("swipe_trigger") or "").strip()
            if trigger:
                lines += ["", f"*Swipe trigger:* {trigger}"]
            brief = (slide.get("image_prompt") or slide.get("visual_intent") or "").strip()
            if brief:
                lines += ["", f"*Visual:* {brief}"]
            lines.append("")

    caption = (idea.caption or "").strip()
    if caption:
        lines += ["## Caption", "", caption, ""]
    tags = _hashtag_line(idea)
    if tags:
        lines += ["## Hashtags", "", tags, ""]
    if (idea.comment_trigger or "").strip():
        lines += ["## First comment (pin it)", "", idea.comment_trigger.strip(), ""]

    story = route.get("story")
    if isinstance(story, dict) and (story.get("prose") or "").strip():
        lines += ["## Episode", ""]
        if (story.get("logline") or "").strip():
            lines += [f"*{story['logline'].strip()}*", ""]
        lines += [story["prose"].strip(), ""]

    qa = route.get("qa")
    if isinstance(qa, dict) and qa:
        lines += ["## QA scores", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(qa.items())] + [""]

    metrics = _loads(idea.metrics_json, {}) or {}
    if metrics:
        lines += ["## Results", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(metrics.items())] + [""]

    return "\n".join(lines).rstrip() + "\n"


def post_record(idea: Idea) -> dict:
    """The full post as JSON — everything `post.md` renders, unflattened."""
    return {
        "idea_id": idea.idea_id,
        "status": idea.status.value if isinstance(idea.status, Status) else str(idea.status),
        "post_type": idea.post_type.value if idea.post_type else None,
        "priority": idea.priority,
        "content_category": idea.content_category,
        "target_viewer": idea.target_viewer,
        "pain_point": idea.pain_point,
        "core_tension": idea.core_tension,
        "concept_note": idea.concept_note,
        "learning_tag": idea.learning_tag,
        "hook": idea.hook,
        "slides": _loads(idea.slides_json, []),
        "caption": idea.caption,
        "caption_text": caption_text(idea),
        "comment_trigger": idea.comment_trigger,
        "hashtags": _loads(idea.hashtags, []),
        "route": _loads(idea.route_json, {}),
        "metrics": _loads(idea.metrics_json, {}),
        "platform_urls": _loads(idea.platform_urls_json, {}),
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
        "processed_at": idea.processed_at.isoformat() if idea.processed_at else None,
        "exported_at": idea.exported_at.isoformat() if idea.exported_at else None,
        "scheduled_for": idea.scheduled_for.isoformat() if idea.scheduled_for else None,
    }


# --- writing the archive ----------------------------------------------------


def _asset_paths(idea: Idea, settings: Settings) -> list[Path]:
    """Rendered slides, in slide order, that really live under `output_dir`.

    Paths come out of the database, so each one is resolved and checked against
    `output_dir` before it is read: a row pointing at `/etc/passwd` yields
    nothing rather than a file in someone's download.
    """
    base = Path(settings.output_dir).resolve()
    out: list[Path] = []
    for raw in _loads(idea.asset_paths_json, []) or []:
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            continue
        if base != path and base not in path.parents:
            continue
        if path.is_file():
            out.append(path)
    return out


def _extra_media(idea: Idea, settings: Settings, already: set[Path]) -> list[Path]:
    """The finished video, which lives on disk rather than in the asset list.

    Only `video*.mp4` and friends: the slide list is the source of truth for
    the carousel (it carries the order), so stray variant renders and the
    `clips/` working files stay out of a folder someone is about to open.
    """
    folder = Path(settings.output_dir) / idea.idea_id
    if not folder.is_dir():
        return []
    found = []
    for child in sorted(folder.iterdir()):
        if not child.is_file() or child.resolve() in already:
            continue
        if child.suffix.lower() in _MEDIA_EXTS and child.name.startswith("video"):
            found.append(child)
    return found


def _folder_name(idea: Idea, position: int, taken: set[str]) -> str:
    slug = _slug(idea.hook or idea.concept_note)
    name = f"{position:03d}_{_safe_name(idea.idea_id)}" + (f"_{slug}" if slug else "")
    if name in taken:  # two posts, same id-and-slug shouldn't happen — be sure
        name = f"{name}_{len(taken)}"
    taken.add(name)
    return name


def _readme(result: BundleResult, made_at: datetime) -> str:
    lines = [
        "CHRGD content studio — post export",
        f"Made {made_at.strftime('%d %b %Y %H:%M')}",
        "",
        f"{result.posts} post(s), {result.files} file(s), {_human(result.bytes)}.",
        "",
        "One folder per post, in posting order. Inside each:",
        "  slide_01.jpg …    the carousel, in slide order",
        "  caption.txt       caption + hashtags, ready to paste",
        "  first_comment.txt the comment to pin right after posting",
        "  post.md           the whole post as readable copy",
        "  post.json         the same record, for anything programmatic",
        "  video.mp4         video posts only",
        "",
        "index.csv lists every post in the same order.",
        "",
    ]
    if result.skipped:
        lines += ["Not included:"]
        lines += [f"  · {idea_id} — {why}" for idea_id, why in result.skipped]
        lines.append("")
    return "\n".join(lines)


def _index_csv(result: BundleResult) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["folder", "idea_id", "status", "scheduled_for", "hook", "images", "files", "bytes"]
    )
    for e in result.entries:
        writer.writerow(
            [e.folder, e.idea_id, e.status, e.scheduled_for, e.hook, e.images, e.files, e.bytes]
        )
    return buf.getvalue()


def write_bundle(
    dest: IO[bytes] | str | Path,
    ideas: Iterable[Idea],
    settings: Settings,
    *,
    name: str | None = None,
    include_unbuilt: bool = False,
) -> BundleResult:
    """Write every post's content into one zip at `dest`.

    `dest` is a path or any writable binary file object, so the CLI can write a
    file and the web app can stream into a temp file with the same call. The
    archive's single top-level folder is `name` (defaulted to a timestamp), so
    extracting it never scatters folders across a Downloads directory.

    Bare seeds — ideas captured but never built — are skipped with a reason
    unless `include_unbuilt` is set: a folder holding nothing but an id helps
    nobody.
    """
    made_at = datetime.now()
    root = _safe_name(name) if name else f"chrgd_posts_{made_at.strftime('%Y%m%d_%H%M')}"
    result = BundleResult(name=root)
    taken: set[str] = set()

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for idea in ideas:
            if not include_unbuilt and not is_built(idea):
                result.skipped.append((idea.idea_id, "not built yet"))
                continue

            entry = BundleEntry(
                idea_id=idea.idea_id,
                folder=_folder_name(idea, len(result.entries) + 1, taken),
                hook=(idea.hook or idea.concept_note or "").strip(),
                status=idea.status.value if isinstance(idea.status, Status) else str(idea.status),
                scheduled_for=idea.scheduled_for.isoformat() if idea.scheduled_for else "",
            )
            base = f"{root}/{entry.folder}"

            def add_bytes(arcname: str, data: str) -> None:
                raw = data.encode("utf-8")
                zf.writestr(f"{base}/{arcname}", raw)
                entry.files += 1
                entry.bytes += len(raw)

            def add_file(arcname: str, path: Path) -> None:
                try:
                    size = path.stat().st_size
                    zf.write(path, arcname=f"{base}/{arcname}")
                except OSError as exc:  # a file that vanished mid-run
                    log.warning("bundle: skipped %s (%s)", path, exc)
                    return
                entry.files += 1
                entry.bytes += size

            slides = _asset_paths(idea, settings)
            for n, path in enumerate(slides, 1):
                # Rebuilt names, never the stored one: two digits so the zip
                # listing stays in slide order past slide 9.
                add_file(f"slide_{n:02d}{path.suffix.lower() or '.jpg'}", path)
                entry.images += 1

            for path in _extra_media(idea, settings, {p.resolve() for p in slides}):
                add_file(path.name, path)

            text = caption_text(idea)
            if text:
                add_bytes("caption.txt", text + "\n")
            if (idea.comment_trigger or "").strip():
                add_bytes("first_comment.txt", idea.comment_trigger.strip() + "\n")
            add_bytes("post.md", post_markdown(idea))
            add_bytes("post.json", json.dumps(post_record(idea), indent=2) + "\n")

            result.entries.append(entry)

        zf.writestr(f"{root}/index.csv", _index_csv(result))
        zf.writestr(f"{root}/README.txt", _readme(result, made_at))

    log.info("bundle %s: %s", root, result.summary())
    return result


def bundle_for(
    store: Store,
    settings: Settings,
    *,
    ids: Sequence[str] | None = None,
    status: Status | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int | None = None,
    include_unbuilt: bool = False,
    dest: IO[bytes] | str | Path,
    name: str | None = None,
) -> BundleResult:
    """Select posts and write them, in one call — what every caller wants."""
    ideas = select(
        store, ids=ids, status=status, date_from=date_from, date_to=date_to, limit=limit
    )
    return write_bundle(
        dest, ideas, settings, name=name, include_unbuilt=include_unbuilt
    )


def estimate(
    store: Store,
    settings: Settings,
    *,
    ids: Sequence[str] | None = None,
    status: Status | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int | None = None,
    include_unbuilt: bool = False,
) -> dict:
    """How big this bundle would be, without building it.

    The download screen asks first: a studio that has been running for months
    can hold gigabytes of renders, and a user deserves to see that number
    before their browser starts pulling it.
    """
    ideas = select(
        store, ids=ids, status=status, date_from=date_from, date_to=date_to, limit=limit
    )
    posts = images = total = 0
    for idea in ideas:
        if not include_unbuilt and not is_built(idea):
            continue
        posts += 1
        paths = _asset_paths(idea, settings)
        paths += _extra_media(idea, settings, {p.resolve() for p in paths})
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        images += len(paths)
    return {
        "posts": posts,
        "files": images,
        "bytes": total,
        "size": _human(total),
    }
