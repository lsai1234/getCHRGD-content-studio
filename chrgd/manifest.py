"""The panel manifest — the continuity state the carousel never had.

Every image is generated blind to its siblings. `compose_design_prompt` gets the
slide list and an index, but the only thing it carries across is a one-line
paraphrase of the previous slide's `visual_intent`, so nothing in the engine
knows *who is in the room*, *where they are standing* or *what has already
happened to the props*. Each panel was composed independently against the whole
episode's cast, which is why every character appeared in every panel: the locked
design block listed all of them, on all of them, flagged as outranking the scene.

So the whole set is resolved ONCE, before any image is generated, into a list of
`Panel` records. Each panel's slice is then the only thing its prompt sees.

    build_manifest(post, cast_keys)   → Manifest, computed at build time
    manifest_for(idea)                → the stored one, recomputed if stale
    validate(manifest)                → blocking reasons, empty means go

Everything here is pure string and set work over data that already exists. It
costs nothing, it runs before a penny of image spend, and it is deliberately the
only automated safety net — visual correctness is confirmed by eye at the review
stop, and per-image inspection would multiply the cost of every carousel for
something a human is already looking at.

WHAT IS FILLED IN AND WHAT IS NOT. The schema is the full shape from the outset
so it doesn't churn under later work, but only the fields this stage owns are
populated: presence (`cast_present` / `cast_forbidden`) and the beat text.
`blocking`, `props_present`, `camera`, `crowd_count` and `text_elements` are
declared and left empty; the checks that depend on them stay out of `validate`
until something fills them, because a validator that fails on unpopulated fields
just teaches people to switch it off.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

#: Route key the manifest rides on.
ROUTE_KEY = "panels"


class TextElement(BaseModel):
    """A bounded piece of lettering. Populated by the text-specification stage.

    Declared now because presence and attribution are the same problem: a
    dialogue bubble's tail has to point at a character who is actually in the
    panel, and that check is only possible once both halves exist.
    """

    id: str = ""
    #: caption | dialogue | broadcast | prop_label
    role: str = "caption"
    content: str = ""
    placement: str = ""
    style: str = ""


class Panel(BaseModel):
    index: int = 0
    total: int = 0
    #: The copy this panel actually carries, for the operator reading the
    #: manifest at the review stop — presence is derived from it.
    beat_text: str = ""
    #: The allowlist. Only these characters may be drawn.
    cast_present: list[str] = Field(default_factory=list)
    #: The explicit negative. An empty `cast_present` is NOT sufficient: with
    #: the whole episode in context the generator pulls absent characters back
    #: in, so every one of them has to be named and refused by name.
    cast_forbidden: list[str] = Field(default_factory=list)

    # --- filled in by later stages, declared here so the shape is stable ---
    blocking: str = ""
    props_present: list[str] = Field(default_factory=list)
    camera: str = ""
    crowd_count: int | None = None
    text_elements: list[TextElement] = Field(default_factory=list)


class Manifest(BaseModel):
    #: The episode's declared cast, in the order the operator picked them.
    cast: list[str] = Field(default_factory=list)
    panels: list[Panel] = Field(default_factory=list)
    #: Fingerprint of the slides this was computed from. An operator editing
    #: copy after the build would otherwise leave a manifest describing an
    #: episode that no longer exists, and a stale allowlist is worse than none.
    source: str = ""

    def panel(self, index: int) -> Panel | None:
        for panel in self.panels:
            if panel.index == index:
                return panel
        return None


# --- deriving presence ------------------------------------------------------

#: A character named in a sentence carrying one of these has ARRIVED here, and
#: is therefore absent from every panel before it — which is the whole point.
#: An arrival that has already happened on panel 1 is not a payoff.
_ARRIVAL = re.compile(
    r"\b(?:arriv\w*|appear\w*|turn(?:s|ed)?\s+up|show(?:s|ed|ing)?\s+up"
    r"|walk(?:s|ed|ing)?\s+in|walk(?:s|ed|ing)?\s+through"
    r"|jog(?:s|ged|ging)?\s+in|run(?:s|ning)?\s+in|ran\s+in"
    r"|burst(?:s|ing)?\s+(?:in|through)|come(?:s)?\s+in|came\s+in"
    r"|step(?:s|ped|ping)?\s+(?:in|out\s+of)|enter(?:s|ed|ing)?"
    r"|is\s+suddenly|materialis\w*)\b",
    re.I,
)

#: The same, in reverse. Present through the beat that says they go, absent
#: after it — until an explicit arrival brings them back.
_DEPARTURE = re.compile(
    r"\b(?:leave(?:s)?|left|walk(?:s|ed|ing)?\s+(?:out|off|away)"
    r"|storm(?:s|ed|ing)?\s+(?:out|off)|disappear\w*|vanish\w*"
    r"|goe(?:s)?\s+home|went\s+home|is\s+gone|has\s+gone|exits?|exited"
    r"|drive(?:s)?\s+off|drove\s+off)\b",
    re.I,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _slide_copy(slide: dict) -> str:
    """What a reader reads on this panel."""
    return " ".join(
        str(slide.get(k) or "").strip()
        for k in ("headline", "supporting", "body")
    ).strip()


def _slide_art(slide: dict) -> str:
    """What the artwork was briefed to show."""
    return " ".join(
        str(slide.get(k) or "").strip()
        for k in ("image_prompt", "visual_intent")
    ).strip()


def _scan(text: str, cast: list[str]) -> tuple[set[str], set[str], set[str]]:
    """(named, arriving, departing) roster keys in one panel's text.

    Arrival and departure are scoped to the SENTENCE the character is named in.
    Scanning the whole panel would make "Clarkson is on the leg press. Molly-Mae
    walks in." read as both of them arriving.
    """
    from .likeness import _keys_in
    from .roster import name_index

    index = name_index()
    wanted = set(cast)
    named: set[str] = set()
    arriving: set[str] = set()
    departing: set[str] = set()
    for sentence in _SENTENCE.split(text or ""):
        here = _keys_in(sentence, index) & wanted
        if not here:
            continue
        named |= here
        if _ARRIVAL.search(sentence):
            arriving |= here
        if _DEPARTURE.search(sentence):
            departing |= here
    return named, arriving, departing


def _entrances(scans: list[tuple[set[str], set[str], set[str]]],
               cast: list[str]) -> dict[str, int]:
    """The panel each character is first actually IN.

    Two rules, and the second is the one that saves the reveals. A character is
    normally in from the panel that first names them — but if an arrival beat
    for them exists LATER in the episode, every earlier mention was the story
    talking ABOUT someone who is not in the room yet ("Clarkson gets here at
    six"), and drawing them there spends the surprise before it is written.
    """
    entrance: dict[str, int] = {}
    for key in cast:
        first_named = next(
            (i for i, (named, _a, _d) in enumerate(scans) if key in named), None
        )
        if first_named is None:
            continue
        first_arrival = next(
            (i for i, (_n, arriving, _d) in enumerate(scans) if key in arriving), None
        )
        entrance[key] = max(first_named, first_arrival or 0)
    return entrance


def build_manifest(post: dict, cast_keys: list[str]) -> Manifest:
    """Resolve the whole episode into per-panel state, before any generation.

    Presence carries forward: once a character is in the room they stay there
    until a departure beat, because a comic panel showing two people in a
    conversation is not improved by one of them evaporating on the beat their
    name happens not to appear.
    """
    cast = [str(k) for k in (cast_keys or []) if str(k).strip()]
    slides = [s for s in (post.get("slides") or []) if isinstance(s, dict)]
    total = len(slides)
    manifest = Manifest(cast=cast, source=fingerprint(slides))
    if not slides:
        return manifest

    scans = [_scan(f"{_slide_copy(s)} {_slide_art(s)}", cast) for s in slides]
    entrance = _entrances(scans, cast)

    in_room: set[str] = set()
    for i, slide in enumerate(slides):
        named, _arriving, departing = scans[i]
        # Anyone whose entrance is this panel or earlier, and who has been
        # named by now, is in the room.
        in_room |= {k for k in named if entrance.get(k, i) <= i}

        copy = _slide_copy(slide)
        # An explicitly person-free beat (an object, a sign, a wide of the room)
        # empties the frame without emptying the continuity state — they didn't
        # leave, the camera looked elsewhere. Copy naming somebody outranks it:
        # a caption about Tracy Beaker on a panel she isn't in is the other half
        # of the same bug.
        person_free = slide.get("feature_character") is False and not (
            _scan(copy, cast)[0]
        )
        here = set() if person_free else set(in_room)

        manifest.panels.append(Panel(
            index=i,
            total=total,
            beat_text=copy,
            cast_present=[k for k in cast if k in here],
            cast_forbidden=[k for k in cast if k not in here],
        ))
        in_room -= departing
    return manifest


# --- storage ----------------------------------------------------------------


def fingerprint(slides: list[dict]) -> str:
    """A cheap hash of the copy and briefs a manifest was derived from."""
    payload = [
        [str((s or {}).get(k) or "") for k in
         ("headline", "supporting", "body", "image_prompt", "visual_intent")]
        + [str((s or {}).get("feature_character"))]
        for s in slides
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def manifest_from_route(route: dict | None) -> Manifest | None:
    raw = (route or {}).get(ROUTE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return Manifest.model_validate(raw)
    except Exception:  # noqa: BLE001 — a bad manifest falls back, never crashes
        return None


def manifest_for(idea) -> Manifest | None:
    """The manifest for this idea's CURRENT slides, or None if it has no cast.

    Recomputed when the stored one doesn't match the copy on the idea. Editing a
    slide after the build would otherwise leave an allowlist describing an
    episode that no longer exists, and the render would forbid the wrong people.
    Recomputing is free, so there is no reason to risk the stale one.
    """
    import json as _json

    try:
        route = _json.loads(idea.route_json) if idea.route_json else {}
    except (TypeError, ValueError):
        route = {}
    cast = [str(k) for k in (route.get("cast") or [])]
    if not cast:
        return None
    try:
        slides = _json.loads(idea.slides_json) if idea.slides_json else []
    except (TypeError, ValueError):
        slides = []
    if not isinstance(slides, list) or not slides:
        return None

    stored = manifest_from_route(route)
    if stored is not None and stored.source == fingerprint(slides):
        return stored
    return build_manifest({"slides": slides}, cast)


# --- validation: rules 1 and 2 ----------------------------------------------


def validate(manifest: Manifest) -> list[str]:
    """Blocking reasons. Empty means the set is safe to generate.

    Only the presence rules are here — the rest of the suite arrives with the
    fields it checks. A validator that fires on unpopulated fields is one that
    gets switched off, and then it protects nothing.
    """
    from .roster import get_character

    def label(key: str) -> str:
        char = get_character(key)
        return char.name if char else key

    reasons: list[str] = []
    cast = set(manifest.cast)
    #: Anyone who is in frame at some point in the episode. A character the copy
    #: refers to while they are legitimately off-screen — not arrived yet, or
    #: already left — is normal storytelling and must not fail: the whole point
    #: of the arrival rule is to let an episode talk about someone before they
    #: walk in. So rule 1 fires only for a character the copy points at who is
    #: never drawn ANYWHERE, which is unambiguously broken. The strict
    #: per-element version of this check lives on `text_elements` below, where a
    #: bubble's anchor names exactly one character and there is no ambiguity.
    ever_present = {k for p in manifest.panels for k in p.cast_present}
    for panel in manifest.panels:
        where = f"panel {panel.index + 1}"
        present, forbidden = set(panel.cast_present), set(panel.cast_forbidden)

        # Rule 2 — every character is on exactly one of the two lists.
        both = present & forbidden
        if both:
            reasons.append(
                f"{where}: {', '.join(sorted(label(k) for k in both))} is both "
                "present and forbidden — the allowlist and the negative "
                "contradict each other"
            )
        missing = cast - present - forbidden
        if missing:
            reasons.append(
                f"{where}: {', '.join(sorted(label(k) for k in missing))} is on "
                "neither list — an unlisted character is one the generator will "
                "draw from story context"
            )
        stray = (present | forbidden) - cast
        if stray:
            reasons.append(
                f"{where}: {', '.join(sorted(label(k) for k in stray))} is not "
                "in this episode's cast"
            )

        # Rule 1 — the copy cannot point at somebody the episode never draws.
        named, _arriving, _departing = _scan(panel.beat_text, manifest.cast)
        never_drawn = (named & forbidden) - ever_present
        if never_drawn:
            reasons.append(
                f"{where}: the copy is about "
                f"{', '.join(sorted(label(k) for k in never_drawn))}, who is "
                "forbidden from every panel in the episode — the caption points "
                "at somebody who is never drawn"
            )

        # Rule 1, strict form: a bubble's tail is the attribution, so its anchor
        # has to be in the panel. Inert until the text stage populates these,
        # which is deliberate — a validator that fires on unpopulated fields is
        # one that gets switched off, and then it protects nothing.
        for element in panel.text_elements:
            anchors = _scan(element.placement, manifest.cast)[0]
            stranded = anchors - present
            if stranded:
                reasons.append(
                    f"{where}: the {element.role} \"{element.content[:40]}\" is "
                    f"anchored to {', '.join(sorted(label(k) for k in stranded))}, "
                    "who is not in the panel — the bubble would have nothing to "
                    "point at"
                )
    return reasons


# --- what the image prompt is told ------------------------------------------


def cast_block(panel: Panel) -> str:
    """The locked designs for THIS panel, plus the refusal of everyone else.

    The negative is not optional and it is not a summary. Each absent character
    is named, because the generator has the whole episode's story in front of it
    and will otherwise reinstate them from context — which is exactly what it
    was doing.
    """
    from .roster import get_character, resolve, visual_block

    parts: list[str] = []
    present = visual_block(resolve(panel.cast_present))
    if present:
        parts.append(present)
    elif panel.cast_forbidden:
        parts.append(
            "NOBODY FROM THE CAST IS IN THIS PANEL. Draw the scene with no "
            "recognisable character in it at all."
        )
    names = [
        get_character(k).name for k in panel.cast_forbidden if get_character(k)
    ]
    if names:
        parts.append(
            "NOT IN THIS PANEL — "
            + ", ".join(names)
            + ". They are absent from this moment of the story and must not "
            "appear anywhere in the frame: not in the background, not partly "
            "out of shot, not in a mirror, a poster, a photo or a reflection, "
            "not as a silhouette. Either the reader has not met them yet or "
            "they have already left, and drawing them here spends the story "
            "before it is told."
        )
    return "\n\n".join(parts)
