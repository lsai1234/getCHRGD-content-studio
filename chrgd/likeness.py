"""The likeness gate — the rules THE MULTIVERSE cannot be allowed to forget.

Recognisable public figures, AI-generated, on a supplement brand's account is
the one part of this plan carrying real outside risk: publicity and personality
rights, plus platform rules on synthetic likenesses of real people. Parody of
public figures in caricature is well-trodden ground, but only while the lines
hold — and lines that live in a prompt hold right up until the week the prompt
gets edited.

So they live here, checked against the finished post, in the same two-layer
shape as `chrgd/claims.py`:

  * **the lint** — deterministic, offline, no key. It knows the roster, so it
    can do the checks a general model can't: is this character even approved,
    is a protected figure being put next to a product, is a real person being
    quoted, is the artwork asking for photorealism.
  * **the judge** — a cheap LLM pass for the rest: a joke about appearance, an
    implied endorsement, a fabricated news framing.

Like the claims gate and unlike the concept gate, a failure **holds the post
for review** rather than letting it render. That asymmetry is deliberate:
sharpening a weak hook costs a few pence, and publishing a fake quote from a
real footballer costs considerably more.

The rules enforced, from THEMES_PLAN.md:
  1. caricature, never photoreal
  2. no real person using, holding or endorsing a product
  3. no fabricated statements, quotes or news framing
  4. punch at status and situation — never appearance, protected
     characteristics, sex, crime or health
  5. cast only from the approved roster, and no more than the world's max
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from .claims import ClaimFlag, ClaimsJudge
from .config import Settings
from .models import Idea

log = logging.getLogger(__name__)

#: The gate profile that turns these rules on.
LIKENESS_PROFILE = "continuity_likeness"

# --- deterministic patterns -------------------------------------------------

#: Words that turn a scene into an endorsement. Near a protected character,
#: any of these is the single sharpest legal edge in the whole show.
_PRODUCT = re.compile(
    r"\b(?:getchrgd|chrgd|shaker|supplement|pre-?workout|protein|creatine|"
    r"our\s+(?:product|range|tub)|sponsor(?:ed|ship)?|link\s+in\s+bio|"
    r"discount\s+code|swipe\s+up|buy\s+now)\b", re.I,
)
#: Words that turn a caricature into a quote or a news report.
_ATTRIBUTION = re.compile(
    r"\b(?:said|says|announced|confirmed|revealed|told\s+reporters|"
    r"has\s+admitted|breaking)\b", re.I,
)
#: Art direction that would produce a likeness rather than a drawing.
_PHOTOREAL = re.compile(
    r"\b(?:photo-?real(?:istic)?|photograph(?:ic|y)?|hyper-?real(?:istic)?|"
    r"lifelike|8k|dslr|shot\s+on|candid\s+photo|real\s+person)\b", re.I,
)
#: Territory rule 4 puts off-limits.
_OFF_LIMITS = re.compile(
    r"\b(?:fat|ugly|obese|bald(?:ing)?|wrinkl\w+|plastic\s+surgery|"
    r"sex(?:y|ual)?|nude|naked|onlyfans|porn|"
    r"arrest(?:ed)?|convict(?:ed|ion)|criminal|prison|jail|fraud|"
    r"cancer|illness|disease|addict(?:ion|ed)?|rehab|overdose|died|death)\b",
    re.I,
)


class ContinuityVerdict(BaseModel):
    safe: bool = True
    flags: list[ClaimFlag] = []
    note: str = ""


@dataclass
class LikenessResult:
    checked: bool = False
    safe: bool = True
    blocking: bool = False
    flags: list[ClaimFlag] = field(default_factory=list)
    note: str = ""
    cast: list[str] = field(default_factory=list)
    error: str = ""

    def as_payload(self) -> dict:
        return {
            "checked": self.checked, "safe": self.safe,
            "blocking": self.blocking, "note": self.note,
            "cast": self.cast, "error": self.error,
            "flags": [f.model_dump() for f in self.flags],
        }

    def reasons(self) -> list[str]:
        return [f.line() for f in self.flags]


class LikenessJudge(Protocol):
    def judge(self, system: str, user: str) -> str: ...


# --- layer 1: the lint ------------------------------------------------------


def _copy_fragments(post: dict) -> list[tuple[str, str]]:
    """Viewer-facing copy, with its location."""
    out: list[tuple[str, str]] = []
    for i, slide in enumerate(post.get("slides") or [], 1):
        for key in ("headline", "supporting", "body"):
            text = str((slide or {}).get(key) or "").strip()
            if text:
                out.append((f"slide {i}", text))
    for key, label in (("caption", "caption"), ("comment_trigger", "comment trigger")):
        text = str(post.get(key) or "").strip()
        if text:
            out.append((label, text))
    return out


def _art_fragments(post: dict) -> list[tuple[str, str]]:
    """The image briefs — where a photoreal instruction would hide."""
    out: list[tuple[str, str]] = []
    for i, slide in enumerate(post.get("slides") or [], 1):
        for key in ("image_prompt", "visual_intent"):
            text = str((slide or {}).get(key) or "").strip()
            if text:
                out.append((f"slide {i} artwork", text))
    return out


def named_characters(post: dict) -> list[str]:
    """Roster keys whose character is named anywhere in the post."""
    from .roster import name_index

    index = name_index()
    blob = " ".join(t for _w, t in _copy_fragments(post) + _art_fragments(post)).lower()
    return [char.key for name, char in index.items() if name in blob]


def lint_post(post: dict, *, cast_keys: list[str] | None = None) -> list[ClaimFlag]:
    """The deterministic likeness checks.

    Roster-aware, which is what lets it do things a general judge can't: it
    knows which names are approved and which of them are protected.
    """
    from .roster import get_character, load_world, name_index

    flags: list[ClaimFlag] = []
    index = name_index()
    present = named_characters(post)
    protected = {k for k in present if (get_character(k) or None) and get_character(k).protected}

    # Rule 5 — casting. An unapproved person is the failure the allow-list exists
    # to prevent, so a declared cast that isn't on the roster is flagged loudly.
    for key in cast_keys or []:
        if get_character(key) is None:
            flags.append(ClaimFlag(
                where="cast", text=str(key), rule="off_roster",
                why="not on the approved roster — the engine may only cast from it",
            ))
    max_cast = load_world().max_cast
    if len(present) > max_cast:
        names = ", ".join(sorted(index[n].name for n in index if index[n].key in present))
        flags.append(ClaimFlag(
            where="cast", text=names, rule="too_many_characters",
            why=f"{len(present)} characters — more than {max_cast} and a carousel can't hold them",
        ))

    for where, text in _copy_fragments(post):
        lowered = text.lower()
        here = [k for k in protected if index and get_character(k).name.lower() in lowered]
        if here:
            names = ", ".join(get_character(k).name for k in here)
            # Rule 2 — endorsement. The sharpest edge, and the easiest to avoid.
            if _PRODUCT.search(text):
                flags.append(ClaimFlag(
                    where=where, text=text[:120], rule="endorsement",
                    why=f"{names} appears alongside a product — a real person must "
                        "never use, hold or endorse anything on this account",
                ))
            # Rule 3 — fabricated statements.
            if _ATTRIBUTION.search(text) or '"' in text or "“" in text:
                flags.append(ClaimFlag(
                    where=where, text=text[:120], rule="fabricated_statement",
                    why=f"reads as a real quote or announcement from {names} — "
                        "absurd situations yes, realistic statements never",
                ))
        # Rule 4 — off-limits territory, for anyone in the cast.
        if present and _OFF_LIMITS.search(text):
            match = _OFF_LIMITS.search(text)
            flags.append(ClaimFlag(
                where=where, text=text[:120], rule="off_limits",
                why=f"“{match.group(0)}” — punch at status and situation only, "
                    "never appearance, protected characteristics, sex, crime or health",
            ))

    # Rule 1 — caricature, never photoreal. Checked on the artwork brief, which
    # is where a stray "shot on a phone" would actually reach the image model.
    for where, text in _art_fragments(post):
        if protected and _PHOTOREAL.search(text):
            match = _PHOTOREAL.search(text)
            flags.append(ClaimFlag(
                where=where, text=text[:120], rule="photoreal",
                why=f"“{match.group(0)}” asks for realism on a slide with a real "
                    "person — this show is caricature only, and that IS the line",
            ))
    return flags


# --- layer 2: the judge -----------------------------------------------------


JUDGE_SYSTEM = """You are the safety and continuity check for a comic serial published by a UK gym/supplement brand. The show puts recognisable characters — internet meme characters, and caricatured real public figures — into storylines at a knackered retail-park gym.

Check the finished episode against these rules. Each one is a HARD limit:
1. CARICATURE, NEVER PHOTOREAL. Real people appear as obvious cartoon drawings. Any instruction that would produce a realistic likeness is a fail.
2. NO ENDORSEMENT. A real person must never be shown using, holding, recommending or standing with a product. This account sells supplements; implied endorsement is the thing that cannot appear.
3. NO FABRICATED STATEMENTS. Absurd situations are the joke. Realistic quotes, fake announcements, or anything framed as real news or a real thing someone said, is a fail.
4. PUNCH AT STATUS AND SITUATION. Never at appearance, protected characteristics, or anything sexual, criminal or health-related.
5. IN-WORLD CONTINUITY. Characters must play their established traits and must not contradict the canon you are given.

You are checking for real risk and real continuity breaks, not for tone. Do NOT flag: a character being made to look foolish by the situation, a status contest, rudeness, or a joke at a character's ego. Those are the show. Over-flagging trains the editor to ignore you.

Return a SINGLE JSON object, no markdown:
{
  "safe": true or false,
  "note": "one short line: the overall verdict",
  "flags": [
    {"where": "slide 2 | caption | artwork", "text": "the exact offending words", "rule": "judge", "why": "which rule it breaks and how to fix it"}
  ]
}"""


def _judge_payload(post: dict, canon_block: str, cast_block: str) -> str:
    lines = []
    if cast_block:
        lines += [cast_block, ""]
    if canon_block:
        lines += ["THE CANON SO FAR:", canon_block, ""]
    lines.append("THE EPISODE:")
    for where, text in _copy_fragments(post):
        lines.append(f"[{where}] {text}")
    for where, text in _art_fragments(post):
        lines.append(f"[{where}] {text}")
    return "\n".join(lines)


def parse_verdict(text: str) -> ContinuityVerdict:
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the likeness verdict")
    return ContinuityVerdict.model_validate(json.loads(raw[start:end + 1]))


def is_blocking(idea: Idea) -> bool:
    from .shows import show_for_idea

    show = show_for_idea(idea)
    return bool(show and show.gate_profile == LIKENESS_PROFILE)


def check_likeness(
    post: dict,
    idea: Idea,
    settings: Settings,
    *,
    store=None,
    judge: ClaimsJudge | LikenessJudge | None = None,
    use_judge: bool | None = None,
) -> LikenessResult:
    """Run the likeness + continuity gate over a finished episode.

    The lint always runs. The judge runs on a likeness-gated show, and its
    failure is never fatal — but note that unlike the claims judge, a lint
    failure here is enough on its own to hold the post.
    """
    from .roster import cast_block, resolve

    route = {}
    if idea.route_json:
        try:
            route = json.loads(idea.route_json)
        except (json.JSONDecodeError, TypeError):
            route = {}
    cast_keys = [str(k) for k in (route.get("cast") or [])]

    result = LikenessResult(checked=True, blocking=is_blocking(idea))
    result.flags = lint_post(post, cast_keys=cast_keys)
    result.cast = named_characters(post)

    wants_judge = result.blocking if use_judge is None else use_judge
    if wants_judge:
        try:
            if judge is None:
                from .claims import OpenAIClaimsJudge

                judge = OpenAIClaimsJudge(settings)
            canon_block = ""
            if store is not None:
                from .series import load_canon

                canon_block = load_canon(store).brief_block(cast_keys=cast_keys or None)
            verdict = parse_verdict(judge.judge(
                JUDGE_SYSTEM,
                _judge_payload(post, canon_block, cast_block(resolve(cast_keys))),
            ))
            result.note = verdict.note
            seen = {(f.where, f.text) for f in result.flags}
            for flag in verdict.flags:
                if (flag.where, flag.text) not in seen:
                    result.flags.append(flag)
        except Exception as exc:  # noqa: BLE001 — a crashing gate gets switched off
            log.warning("likeness judge failed for %s: %s", idea.idea_id, exc)
            result.error = str(exc)

    result.safe = not result.flags
    return result
