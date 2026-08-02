"""The copy lint — stop art direction being printed on the artwork.

In `ai_design` mode every character of a slide's `headline`, `supporting` and
`body` is painted onto the image by the model (`images.compose_design_prompt`).
That makes those three fields the most visible text in the whole product — and
until now they were also the only fields in the output contract with no
definition at all, so the engine used `supporting` as a scratchpad and the
renderer faithfully printed the scratchpad.

The failure that prompted this, from a real published slide:

    Velvet rope = lifting straps. Wonky '20:00' sign.

That is a note to the illustrator, on the artwork, in shorthand, with an equals
sign in it. Nothing about it reads as a post a person made — it is the single
clearest "this is AI" tell the account can emit.

The contract now defines those fields properly, which stops most of it. This
catches the rest, because a prompt instruction is a request and a lint is a
guarantee. Deterministic, offline, no key, no cost — the same shape as
`chrgd/claims.py`, and deliberately narrow: it only matches things that cannot
be legitimate on-slide copy, because a lint that fires on real writing is one
the editor learns to ignore.
"""

from __future__ import annotations

import re

from .claims import ClaimFlag


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


#: Each entry is a tell that a human would never write as copy a reader reads.
LEAKS: tuple[tuple[str, re.Pattern, str], ...] = (
    (
        "gloss_equals",
        _rx(r"\w\s*=\s*\w"),
        "an '=' gloss — this is a note about the picture, not something a "
        "reader reads. Put it in the image brief.",
    ),
    (
        "camera_language",
        _rx(r"\b(?:close-?up|wide shot|establishing shot|in the (?:back|fore)ground"
            r"|top-?left|bottom-?right|centre frame|center frame)\b"),
        "camera or composition language on the artwork — that belongs in the "
        "image brief, never in the copy.",
    ),
    (
        "panel_talk",
        _rx(r"\b(?:this (?:panel|frame|slide)|the panel shows|pictured|depicted"
            r"|illustration of|rendered as)\b"),
        "the copy is talking about the image instead of telling the story.",
    ),
    (
        "prop_note",
        _rx(r"\b(?:wonky|battered|flickering|grubby|hand-?written|taped-?up|neon)\s+"
            r"['‘“]?[\w:'’]+['’”]?\s+sign\b"),
        "a set-dressing note describing a prop — draw it, don't caption it.",
    ),
    (
        "art_direction_verbs",
        _rx(r"\b(?:cut to|zoom (?:in|out)|pan (?:to|across)|cue |sfx:|caption:"
            r"|on-?screen text:)"),
        "a stage direction — the reader is not the illustrator.",
    ),
    (
        "colour_notation",
        _rx(r"#[0-9a-f]{6}\b|\b(?:palette|halftone|line ?weight|colour grade)\b"),
        "art-direction vocabulary on the artwork.",
    ),
)


def _fragments(post: dict) -> list[tuple[str, str]]:
    """The three fields that get painted onto the image, with their location."""
    out: list[tuple[str, str]] = []
    for i, slide in enumerate(post.get("slides") or [], 1):
        for key in ("headline", "supporting", "body"):
            text = str((slide or {}).get(key) or "").strip()
            if text:
                out.append((f"slide {i} {key}", text))
    return out


def lint_post(post: dict) -> list[ClaimFlag]:
    """Art-direction leaks in the copy that will be printed on the artwork."""
    flags: list[ClaimFlag] = []
    seen: set[tuple[str, str]] = set()
    for where, text in _fragments(post):
        for name, rx, why in LEAKS:
            match = rx.search(text)
            if not match or (where, name) in seen:
                continue
            seen.add((where, name))
            flags.append(ClaimFlag(
                where=where, text=text[:120], rule=name, why=why,
            ))
    return flags


def reasons(post: dict) -> list[str]:
    """Flags as rewrite instructions for the engine's second attempt."""
    return [f.line() for f in lint_post(post)]
