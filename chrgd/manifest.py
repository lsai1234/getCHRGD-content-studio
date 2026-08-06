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
    """A bounded piece of lettering: exactly these words, in exactly this place.

    The words were previously *implied* — the beat text was concatenated into
    the image prompt as narrative context, so the model treated it as things
    that exist in the world and rendered it wherever it liked: as the caption,
    and again as a poster, and again on the wall. Declaring a closed set of
    bounded elements is what stops that.
    """

    id: str = ""
    #: caption | dialogue | broadcast | prop_label | title
    role: str = "caption"
    content: str = ""
    placement: str = ""
    style: str = ""
    #: Roster key or prop id this element's tail points at. The tail IS the
    #: attribution, which is what lets the name prefix come off the artwork.
    anchor: str = ""


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

    #: The closed set of words in this panel. Nothing else may be lettered.
    text_elements: list[TextElement] = Field(default_factory=list)
    #: Objects that must be in frame — the props the beat names, plus any
    #: source a broadcast line issues from.
    props_present: list[str] = Field(default_factory=list)
    #: Who is where, doing what, facing whom. Left implicit, the generator
    #: invented it — which is how a caption saying one thing ended up over
    #: artwork showing another.
    blocking: str = ""
    #: wide / medium / close, chosen from what the panel actually holds.
    camera: str = ""
    #: Background figures. Fixed per story unless the beat turns the room, and
    #: then it only ever goes up.
    crowd_count: int | None = None
    #: The panel's physical configuration, as a comparable string. This is what
    #: makes "two consecutive panels are the same moment" and "the prop is back
    #: in its before state" checkable without looking at an image.
    state: str = ""
    #: The escalating motif's exact configuration at this index. Improvised, it
    #: came out near-full on panel 1 and identical on three panels running.
    progress: str = ""
    #: Props whose configuration THIS beat causes to change. What makes the
    #: causal check well-founded: a state that moves with nothing in the panel
    #: moving it is the broken case, and a state that moves because the copy
    #: says so is the story working.
    state_changes: list[str] = Field(default_factory=list)
    #: `family/variant` — one template, with density variants picked by word
    #: count. Panels were switching treatment mid-carousel because the renderer
    #: chose per slide on character count and whether a `body` was set.
    layout: str = ""


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


# --- deriving the text ------------------------------------------------------

#: A glyph budget, enforced before generation. The model garbles text in
#: proportion to how much of it there is and how small it renders, and the only
#: cheap lever is constraining the input. These are the caps, not suggestions.
#: One narration box is TWO LINES of heavy all-caps across the safe width, and
#: two lines is about 52 characters. It was 90, which is four lines — the box
#: then grew past the bottom of the frame and the third line was sliced in half.
#: The cap is what makes "no more than two lines" achievable rather than a
#: request the model has to disobey.
MAX_ELEMENT_CHARS = 52
MAX_PANEL_CHARS = 150

#: THE SAFE AREA, and it is not the same as the frame. TikTok lays its own
#: furniture over a published photo: the account name, the caption and the music
#: row cover the bottom of the image, and the like/share/bookmark rail covers
#: the right-hand edge. Text drawn in those regions is not cropped so much as
#: buried. These are the bounds every word has to live inside.
SAFE_TOP = 8
SAFE_BOTTOM = 22
SAFE_SIDE = 8
SAFE_RIGHT = 12
#: Long, unusual words garble disproportionately. Set at 12 rather than the 10
#: the brief suggested, because 10 fires on ordinary English — "handwriting" and
#: "recognisable" are not the risk, and a check that refuses normal writing is
#: one that gets switched off, at which point it protects nothing. Character
#: names are exempt on top of that: "Chimpanzini Bananini" cannot be written
#: shorter and is the whole joke.
MAX_WORD_CHARS = 12

#: `Orangina:` at the head of a line. The writer is TOLD to mark the speaker
#: this way, because it is a reliable machine-readable signal — and then it is
#: stripped here so it never reaches the artwork. The prefix is an annotation,
#: not copy. This is what "attribute by position, not by prefix" means in
#: practice: the convention stays, the label comes off.
_SPEAKER_PREFIX = re.compile(r"^\s*([A-Za-z][\w'’\- ]{1,28}?)\s*:\s*(.+)$", re.S)

#: A "speaker" that is a device, not a person: the line is mediated sound and
#: needs its source object visibly in frame for the tail to point at.
_DEVICE = {
    "tannoy": "the tannoy", "intercom": "the intercom", "pa": "the PA speaker",
    "speaker": "the speaker", "speakers": "the speakers", "radio": "the radio",
    "phone": "the phone", "tv": "the TV", "television": "the television",
    "screen": "the screen", "loudspeaker": "the loudspeaker",
    "announcement": "the tannoy", "megaphone": "the megaphone",
}

#: Prefixes that mark narration. Stripped, and the box IS the attribution.
_NARRATION = {"narrator", "narration", "caption", "voiceover", "vo"}

_QUOTES = "\"'“”‘’"


def _split_speaker(text: str, cast: list[str]) -> tuple[str, str, str, str]:
    """(role, content, anchor, anchor_label) for one line of copy.

    An unrecognised prefix is left alone rather than guessed at — it comes back
    as a caption still carrying its label, and rule 10 refuses it. Silently
    printing "DAVE:" on the artwork is the failure this whole stage exists to
    stop, so an unattributable line has to be a blocker, not a shrug.
    """
    from .likeness import _keys_in
    from .roster import get_character, name_index

    raw = (text or "").strip()
    match = _SPEAKER_PREFIX.match(raw)
    if not match:
        return "caption", raw, "", ""
    label, said = match.group(1).strip(), match.group(2).strip()
    said = said.strip(_QUOTES).strip()

    if label.lower() in _NARRATION:
        return "caption", said, "", ""
    device = _DEVICE.get(label.lower())
    if device:
        return "broadcast", said, label.lower(), device
    speaking = _keys_in(label, name_index()) & set(cast)
    if len(speaking) == 1:
        key = next(iter(speaking))
        char = get_character(key)
        return "dialogue", said, key, (char.name if char else key)
    return "caption", raw, "", ""


def _text_for(slide: dict, cast: list[str], *, index: int,
              title_card: str = "", title_note: str = "") -> list[TextElement]:
    """This panel's closed set of lettering, in reading order."""
    elements: list[TextElement] = []
    if index == 0 and title_card.strip():
        # The show's masthead is words on the artwork, so it has to be IN the
        # closed set — otherwise the prompt grants it in one place and forbids
        # it in another, and the model resolves that by inventing more text.
        elements.append(TextElement(
            id="t0", role="title", content=title_card.strip(),
            placement=title_note.strip() or "a small mark in the top-left corner",
            style="the show's own lettering, set SMALLER than the narration "
                  "text — it is a corner mark and it must never be the largest "
                  "element in the panel",
        ))
    for field in ("headline", "supporting", "body"):
        raw = str(slide.get(field) or "").strip()
        if not raw:
            continue
        role, content, anchor, anchor_label = _split_speaker(raw, cast)
        if not content:
            continue
        if role == "dialogue":
            placement = (f"a speech bubble whose tail points to "
                         f"{anchor_label}'s mouth")
            style = "comic bubble lettering, bold all-caps, heavy outline"
        elif role == "broadcast":
            placement = (f"text issuing from {anchor_label}, its tail anchored "
                         f"to {anchor_label} with clear space around it")
            style = ("harder-edged than a speech bubble, slightly distorted, "
                     "so it reads as mediated sound rather than a voice in the "
                     "room")
        else:
            # NOT "the top band" / "the lower band". That wording put the boxes
            # flush against the frame edges, where the first one lost the tops
            # of its letters and the second ran off the bottom of the image.
            placement = (
                "a narration box in the UPPER THIRD, inset — its top edge sits "
                f"at least {SAFE_TOP}% of the frame height below the top of the "
                "image, with clear empty space above it"
                if field == "headline" else
                "a narration box in the LOWER MIDDLE — its bottom edge sits at "
                f"least {SAFE_BOTTOM}% of the frame height above the bottom of "
                "the image, never resting on the bottom edge"
            )
            style = "narration-box lettering, bold all-caps, heavy outline"
        elements.append(TextElement(
            id=f"t{len(elements) + 1}", role=role, content=content,
            placement=placement, style=style, anchor=anchor,
        ))
    return elements


# --- deriving the physical state --------------------------------------------

#: Directional relationships the artwork has to honour. Left implicit, the
#: generator invents them — a character points at the wrong person, an object
#: sits somewhere other than where the copy says it was put.
_DIRECTION = re.compile(
    r"\b(?:point(?:s|ed|ing)?\s+at|look(?:s|ed|ing)?\s+at|star(?:es|ed|ing)\s+at"
    r"|turn(?:s|ed|ing)?\s+to|hand(?:s|ed|ing)?|show(?:s|ed|ing)?\s+\w+\s+to"
    r"|next\s+to|behind|in\s+front\s+of|beside|between|under|over|on\s+top\s+of"
    r"|hold(?:s|ing)?|carr(?:ies|ying)|drop(?:s|ped|ping)?|put(?:s|ting)?)\b",
    re.I,
)

#: A number the reader can count in the frame. Left to the generator these come
#: out wrong, so the configuration is stated instead of implied.
_QUANTITY = re.compile(
    r"\b(\d+)\s*(kg|kilos?|plates?|reps?|sets?|minutes?|seconds?|people|of them)\b",
    re.I,
)

#: Prop configurations, as tokens that can be compared between panels. The
#: named failure this catches: an object shown in a "before" configuration
#: after it has already been moved.
_PROP_STATES: tuple[tuple[str, re.Pattern], ...] = (
    ("broken", re.compile(r"\b(?:break(?:s|ing)?|broke(?:n)?|bust(?:ed)?"
                          r"|out of order|snap(?:s|ped|ping)?|jam(?:s|med)?"
                          r"|packed in|stopped working)\b", re.I)),
    ("working", re.compile(r"\b(?:work(?:s|ed|ing)|fixed|repaired|running again"
                           r"|back on|mended)\b", re.I)),
    ("gone", re.compile(r"\b(?:gone|removed|taken (?:down|away)|missing"
                        r"|disappeared)\b", re.I)),
    ("occupied", re.compile(r"\b(?:taken|occupied|in use|being used)\b", re.I)),
    ("free", re.compile(r"\b(?:free|empty|nobody on it|unused)\b", re.I)),
)

#: Beats that turn the room. Crowd density only ever ramps from one of these.
_CROWD_TURN = re.compile(
    r"\b(?:everyone|the whole (?:gym|room|place)|the room (?:turns|turned|stops)"
    r"|heads turn\w*|nobody (?:says|said)|a crowd|people (?:gather|start)"
    r"|the queue)\b",
    re.I,
)


def _staging_text(slide: dict) -> str:
    """What the PICTURE is, as sentences — the source blocking comes from.

    Dialogue is dropped: a line somebody says is not a description of where
    they are standing, and leaving it in produced blocking that read
    "Tracy Beaker: I have never fixed anything". The image brief goes last so
    it wins on specificity, since it is the field that actually describes the
    shot.
    """
    lines: list[str] = []
    for field in ("headline", "supporting", "body"):
        raw = str(slide.get(field) or "").strip()
        if not raw:
            continue
        match = _SPEAKER_PREFIX.match(raw)
        if match and match.group(1).lower() not in _NARRATION:
            continue
        lines.append(raw.rstrip(".") + ".")
    art = _slide_art(slide).strip()
    if art:
        lines.append(art.rstrip(".") + ".")
    return " ".join(lines)


def _clause_for(name: str, text: str) -> str:
    """The WHOLE sentence that names this entity.

    Whole, not the tail from the match onwards: starting mid-string produced
    fragments like "BEAKER HAS GREASE ON BOTH HANDS" because a surname matched
    inside a word. A complete sentence is both readable and the thing a person
    reviewing the manifest can actually check.
    """
    clauses = _clauses_for(name, text)
    # Later sentences win: the image brief is appended last and describes the
    # shot, where the copy only describes the story.
    return clauses[-1] if clauses else ""


def _clauses_for(name: str, text: str) -> list[str]:
    """Every sentence that names this entity, in order."""
    pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
    out: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        if not pattern.search(sentence):
            continue
        clean = " ".join(sentence.split()).strip(" .")
        if clean:
            out.append(clean[:110])
    return out


def _props_in(text: str, props) -> list[str]:
    """Prop ids named anywhere in this panel."""
    lowered = (text or "").lower()
    return [p.key for p in props.values()
            if any(alias in lowered for alias in p.names())]


#: A cue inside a negation is not a state change. "I have never fixed anything"
#: was reading as the machine being fixed, which then propagated forward as the
#: prop's configuration for the rest of the episode.
_NEGATED = re.compile(r"\b(?:never|not|no|nobody|hasn't|haven't|didn't|isn't"
                      r"|wasn't|won't|can't|couldn't)\b", re.I)


def _prop_state(slide: dict, prop) -> str:
    """The configuration a prop is in, from the sentence that names it.

    Read from the staging text, not the raw copy: a line of dialogue is somebody
    talking, not the state of the room, and taking cues from it had a character
    DENYING they fixed something register as the something being fixed.
    """
    text = _staging_text(slide)
    clauses: list[str] = []
    for alias in prop.names():
        for clause in _clauses_for(alias, text):
            if clause not in clauses:
                clauses.append(clause)
    # Every sentence that names it, not just the last: the copy often states
    # the change ("BREAKS THE LAT PULLDOWN AGAIN") while the image brief only
    # describes the shot, and taking the last one alone lost the change.
    for clause in clauses:
        for token, pattern in _PROP_STATES:
            match = pattern.search(clause)
            if not match:
                continue
            if _NEGATED.search(clause[:match.start()][-40:]):
                continue
            return token
    return ""


def _camera_for(present: list[str], props: list[str]) -> str:
    """One dramatic moment per panel, framed for what it actually holds."""
    if not present:
        return ("wide, eye level — the room and the object, with no character "
                "in the frame")
    if len(present) == 1:
        return ("close, eye level — one character filling the frame, the "
                "background simplified so the text has clear space")
    if len(present) == 2:
        return ("medium two-shot, eye level — both characters fully in frame "
                "and facing each other")
    return ("wide, slightly low — all of the characters in one frame, clearly "
            "separated so none of them overlaps another")


def _blocking_for(slide: dict, present: list[str], props, prop_ids: list[str]) -> str:
    """Who is where, doing what, facing whom — stated rather than left implied."""
    from .roster import get_character

    text = _staging_text(slide)
    # One sentence often places two characters at once ("Tracy at the back,
    # Molly-Mae centre") — say it once and name both, rather than repeating it.
    grouped: list[tuple[list[str], str]] = []
    for key in present:
        char = get_character(key)
        if char is None:
            continue
        clause = (_clause_for(char.name, text)
                  or _clause_for(char.name.split()[-1], text)
                  or "in frame, reacting to the beat")
        for names, existing in grouped:
            if existing == clause:
                names.append(char.name)
                break
        else:
            grouped.append(([char.name], clause))
    bits = [f"{', '.join(names)}: {clause}" for names, clause in grouped]
    said = {clause for _names, clause in grouped}
    for key in prop_ids:
        prop = props.get(key)
        if prop is None:
            continue
        clause = _clause_for(prop.name, text)
        # A prop described by the same sentence as a character is already
        # placed; repeating it verbatim just pads the prompt.
        if clause and clause in said:
            continue
        bits.append(f"{prop.name}: {clause or 'in frame'}")
    if not bits:
        return ""
    out = "; ".join(bits)
    if _DIRECTION.search(text):
        out += (". Honour the directions exactly as stated — who points at "
                "whom, who looks at whom, and where each object sits relative "
                "to which character")
    quantities = _QUANTITY.findall(text)
    if quantities:
        counted = [(n, u) for n, u in quantities if not _is_mass(u)]
        weights = [(n, u) for n, u in quantities if _is_mass(u)]
        if counted:
            out += (". COUNTABLE IN FRAME: "
                    + ", ".join(f"{n} {u}" for n, u in counted)
                    + " — draw exactly that many, arranged so a reader could "
                      "count them")
        if weights:
            out += (". WEIGHT SHOWN: "
                    + ", ".join(f"{n}{u}" for n, u in weights)
                    + " — the plates in frame must read as exactly that, and "
                      "any number written on them must match")
    return out


def _is_mass(unit: str) -> bool:
    return unit.lower().rstrip("s") in ("kg", "kilo")


def _state_for(present: list[str], prop_states: dict[str, str], camera: str) -> str:
    """The panel's physical configuration, as one comparable string."""
    who = "+".join(present) or "empty"
    things = ",".join(f"{k}={v}" for k, v in sorted(prop_states.items()) if v)
    shot = camera.split(",")[0].strip()
    return f"{who}|{things}|{shot}"


def build_manifest(post: dict, cast_keys: list[str], *,
                   title_card: str = "", title_note: str = "",
                   props: dict | None = None, crowd_base: int = 0,
                   layout_family: str = "panel") -> Manifest:
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

    sheet = dict(props or {})
    in_room: set[str] = set()
    #: The last known configuration of each prop, carried forward — this is what
    #: makes "the object is back in its before state" visible without an image.
    prop_states: dict[str, str] = {}
    crowd = crowd_base

    for i, slide in enumerate(slides):
        named, _arriving, departing = scans[i]
        # Anyone whose entrance is this panel or earlier, and who has been
        # named by now, is in the room.
        in_room |= {k for k in named if entrance.get(k, i) <= i}

        copy = _slide_copy(slide)
        text = f"{copy} {_slide_art(slide)}"
        # An explicitly person-free beat (an object, a sign, a wide of the room)
        # empties the frame without emptying the continuity state — they didn't
        # leave, the camera looked elsewhere. Copy naming somebody outranks it:
        # a caption about Tracy Beaker on a panel she isn't in is the other half
        # of the same bug.
        person_free = slide.get("feature_character") is False and not (
            _scan(copy, cast)[0]
        )
        here = set() if person_free else set(in_room)
        present = [k for k in cast if k in here]

        elements = _text_for(slide, cast, index=i, title_card=title_card,
                             title_note=title_note)

        # Props: the ones this beat names, plus any broadcast source. Their
        # configurations carry forward, so a prop that is fixed on panel 3 stays
        # fixed until something breaks it again.
        prop_ids = _props_in(text, sheet)
        changed: list[str] = []
        for key in prop_ids:
            found = _prop_state(slide, sheet[key])
            if found and found != prop_states.get(key):
                changed.append(key)
            if found:
                prop_states[key] = found
        sources = [e.anchor for e in elements if e.role == "broadcast" and e.anchor]
        for key in sources:
            if key not in prop_ids:
                prop_ids.append(key)
        # Writing physically on a prop is still writing on the artwork, so it
        # goes INSIDE the closed set rather than being forbidden as incidental
        # signage — otherwise the BACK SOON sign becomes uncaptionable.
        for key in prop_ids:
            prop = sheet.get(key)
            if prop is None or not prop.label.strip():
                continue
            if any(e.content.strip().lower() == prop.label.strip().lower()
                   for e in elements):
                continue
            elements.append(TextElement(
                id=f"t{len(elements) + 1}", role="prop_label",
                content=prop.label.strip(), anchor=key,
                placement=f"the words physically on {prop.name}",
                style="hand-lettered on the object itself, as it would really "
                      "be written",
            ))

        # Crowd density: fixed, and only ever ramped by a beat that turns the
        # room. It was jumping arbitrarily because nothing ever stated it.
        if crowd_base and _CROWD_TURN.search(copy):
            crowd = min(crowd + 3, crowd_base + 9)

        camera = _camera_for(present, prop_ids)
        words = sum(len(e.content.split()) for e in elements if e.role in _BUDGETED)
        manifest.panels.append(Panel(
            index=i,
            total=total,
            beat_text=copy,
            cast_present=present,
            cast_forbidden=[k for k in cast if k not in here],
            text_elements=elements,
            props_present=sorted(set(prop_ids)),
            blocking=_blocking_for(slide, present, sheet, sorted(set(prop_ids))),
            camera=camera,
            crowd_count=crowd if crowd_base else None,
            state=_state_for(present, {k: prop_states.get(k, "") for k in prop_ids},
                             camera),
            progress=_progress_for(i, total),
            state_changes=sorted(changed),
            layout=_layout_for(words, elements, layout_family,
                               role=str(slide.get("role") or "")),
        ))
        in_room -= departing
    return manifest


#: Words a reader will actually take in at thumb size. Panel 1 is tighter
#: because it has half a second to earn the swipe, and a text block eating the
#: canvas is what the carousels were stalling on. The cap does double duty: it
#: is the swipe lever AND the main control on glyph garbling, since the model
#: garbles in proportion to how much text there is and how small it renders.
MAX_WORDS_PANEL_ONE = 12
MAX_WORDS_PANEL = 25

#: Roles that spend the writer's word budget. The masthead and the words
#: physically on a prop are neither the writer's to shorten nor the reason a
#: panel reads as dense, so they are outside the count — the per-panel
#: character cap still covers every glyph on the artwork.
_BUDGETED = ("caption", "dialogue", "broadcast")


def word_count(panel: Panel) -> int:
    return sum(len(e.content.split()) for e in panel.text_elements
               if e.role in _BUDGETED)


def _layout_for(panel_words: int, elements: list[TextElement], family: str,
                *, role: str = "") -> str:
    """One template, one deterministic variant. Never improvised per panel."""
    if role == CLOSING_ROLE:
        variant = "question-card"
    elif any(e.role in ("dialogue", "broadcast") for e in elements):
        variant = "with-dialogue"
    elif panel_words <= 8:
        variant = "short"
    else:
        variant = "medium"
    return f"{family}/{variant}"


#: What each variant actually means on the page. Stated explicitly, because
#: "the shared layout grid" as a free-text design-system string was being
#: reinterpreted every frame.
_LAYOUT_NOTE = {
    "short": (
        "LAYOUT — SHORT: one narration box in the UPPER THIRD, inset from the "
        "top so there is clear empty space above it, no more than a fifth of "
        "the height, with the artwork holding the rest of the frame."
    ),
    "medium": (
        "LAYOUT — MEDIUM: a narration box in the UPPER THIRD, inset from the "
        "top, and a second in the LOWER MIDDLE — not at the bottom — only if a "
        "second narration string is listed below. Each is no more than a fifth "
        "of the height, and the lower one keeps well clear of the bottom of the "
        "image, where TikTok's own caption will sit over the picture."
    ),
    "question-card": (
        "LAYOUT — QUESTION CARD: this is the closing panel and it is the only "
        "one of its kind in the set. The question is the whole frame — set it "
        "large and centred, filling the middle third and nothing outside it, "
        "on a plain field of the "
        "show's flattest colour with the artwork reduced to a simple graphic "
        "backdrop. No characters, no scene, no detail competing with the words. "
        "It reads as the card at the end of an issue asking the reader "
        "something."
    ),
    "with-dialogue": (
        "LAYOUT — WITH DIALOGUE: a narration box in the UPPER THIRD, inset "
        "from the top, no more than a fifth of the height, and the speech or "
        "sound sitting in the middle third beside its speaker, clear of the "
        "narration and clear of the character's face."
    ),
}


def layout_note(panel: Panel) -> str:
    """The panel's layout instruction — the same template every frame."""
    if not panel.layout:
        return ""
    _family, _, variant = panel.layout.partition("/")
    note = _LAYOUT_NOTE.get(variant, "")
    if not note:
        return ""
    return (
        note + " Every panel in this set uses the same template, the same "
        "margins and the same type treatment; only the density of the text "
        "changes. Do not invent a different arrangement for this frame."
    )


def _progress_for(index: int, total: int) -> str:
    """The escalating motif's exact configuration at this index.

    Improvised, it came back near-full on panel 1 and identical on three panels
    running, because the intended state was never computed or stated — the
    design system said "the motif fills" on every panel with no value attached.
    """
    filled, empty = index + 1, total - (index + 1)
    return (
        f"This is step {filled} of {total} of the escalation. Any progress or "
        f"filling motif in the design must be drawn at EXACTLY this value: "
        f"{filled} segment(s) filled and {empty} segment(s) empty, in a single "
        "row reading left to right, in the same position and at the same size "
        "as every other panel. Do not estimate it and do not round it — "
        f"{filled} filled, {empty} empty."
    )


# --- the ending -------------------------------------------------------------

#: The last slide already asks something, so a question card would be a second
#: one. Cheap to detect and cheaper than a duplicate ending.
_ASKS = re.compile(r"\?\s*$")

#: Copy on the question card, kept deliberately plain — this panel is the
#: comment prompt and the completion signal, not another joke.
CLOSING_ROLE = "cta"


def ensure_closing_card(post: dict, *, unresolved: str = "",
                        comment_trigger: str = "", max_slides: int = 10) -> bool:
    """Give the episode its ending back. Returns True if a card was added.

    Stories that end on an open question had that ending dropped: `unresolved`
    was written, gated on and stored, but only ever reached the build as a
    "do not resolve it" instruction, and `comment_trigger` lived on the post
    without ever becoming a slide. So the carousel finished on a beat with no
    engagement prompt and no completion signal.

    Deliberately conservative — it does nothing when the set already ends on a
    question, when there is nothing to ask, or when adding one would push past
    the platform's slide ceiling.
    """
    slides = post.get("slides") or []
    if not slides or len(slides) >= max_slides:
        return False
    last = slides[-1]
    tail = " ".join(str(last.get(k) or "") for k in ("headline", "supporting", "body"))
    if _ASKS.search(tail.strip()):
        return False

    question = (comment_trigger or "").strip()
    if not question:
        seed = (unresolved or "").strip().rstrip(".")
        if not seed:
            return False
        # The unresolved line is written as a statement ("somebody saw her");
        # as the card it has to be the question the reader answers.
        question = f"{seed[0].upper()}{seed[1:]}?"
    if not question.endswith("?"):
        question = question.rstrip(".") + "?"

    slides.append({
        "headline": question,
        "supporting": "",
        "body": "",
        "image_prompt": "",
        "visual_intent": "the question card",
        "role": CLOSING_ROLE,
        "swipe_trigger": "",
        # No character: this is a card, and forcing the cast onto it would put
        # them in a panel the story has already finished with.
        "feature_character": False,
    })
    post["slides"] = slides
    return True


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
    return build_manifest({"slides": slides}, cast, **title_for(idea))


def title_for(idea) -> dict:
    """Everything the manifest needs from the show: the masthead (which is words
    on the artwork and therefore has to be IN the closed set — granting it in
    one place and forbidding it in another is how the model ends up inventing
    more text), the locked prop sheet, and the fixed crowd density."""
    from .shows import show_for_idea

    show = show_for_idea(idea)
    if show is None:
        return {}
    out: dict = {
        "props": show.props,
        "crowd_base": show.crowd_base,
        "layout_family": show.key or "panel",
    }
    if show.look.title_card.strip():
        out["title_card"] = show.look.title_card.strip()
        out["title_note"] = show.look.title_note.strip()
    return out


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

        reasons += _text_problems(panel, manifest.cast, present, where, label)
    reasons += _sequence_problems(manifest)
    return reasons


def _sequence_problems(manifest: Manifest) -> list[str]:
    """Rules 5, 6 and 8 — the shape of the set, checked in one pass.

    On rule 6, be straight about the limit. Full causal validation is not
    something a string comparison can do, and the literal rule — "flag a prop
    that returns to an earlier configuration" — is wrong anyway: a machine that
    is fixed, broken and fixed again is the plot, not a defect. What IS
    well-founded, and catches the failure the brief actually names, is an
    UNCAUSED change: the object is in a different configuration and nothing in
    that panel moved it. A beat that is merely out of narrative order with no
    prop or cast change behind it will pass, and the review stop is where that
    is seen.
    """
    reasons: list[str] = []
    panels = sorted(manifest.panels, key=lambda p: p.index)

    # Rule 5 — two consecutive panels resolving to the same physical state are
    # the same moment drawn twice.
    for prev, panel in zip(panels, panels[1:]):
        if prev.state and panel.state and prev.state == panel.state:
            reasons.append(
                f"panels {prev.index + 1} and {panel.index + 1} are the same "
                f"physical state ({panel.state}) — the same moment twice. Each "
                "panel is one dramatic moment and something has to change."
            )

    # Rule 6 — a prop's configuration only moves when this beat moves it.
    last: dict[str, str] = {}
    for panel in panels:
        for key, value in _states_in(panel.state).items():
            was = last.get(key)
            if was is not None and value != was and key not in panel.state_changes:
                reasons.append(
                    f"panel {panel.index + 1}: {key} is drawn '{value}' when it "
                    f"was last '{was}', and nothing in this panel changes it — "
                    "an object in a configuration the story has not put it in"
                )
            last[key] = value

    # Rule 8 — the escalation's value increments once per panel, 1/N to N/N.
    for panel in [p for p in panels if p.progress]:
        want = f"step {panel.index + 1} of {panel.total}"
        if want not in panel.progress:
            reasons.append(
                f"panel {panel.index + 1}: the progress element says something "
                f"other than '{want}' — it must be computed from the index, "
                "never estimated"
            )

    # Rule 12 — one template for the set. Density variants are fine; a
    # different arrangement per frame is the drift.
    families = {p.layout.partition("/")[0] for p in panels if p.layout}
    if len(families) > 1:
        reasons.append(
            "the set uses more than one layout template ("
            + ", ".join(sorted(families))
            + ") — one template, with density variants, or the carousel reads "
            "as separate posters"
        )

    # Rule 13 — an episode that leaves something open has to ask it. Checked,
    # not assumed: `ensure_closing_card` is conservative and declines when the
    # set already ends on a question, so this confirms one of the two happened.
    if panels:
        tail = panels[-1]
        asks = _ASKS.search(tail.beat_text.strip() or "") or any(
            e.content.strip().endswith("?") for e in tail.text_elements
        )
        if not asks:
            reasons.append(
                "the last panel asks nothing — an episode that leaves a thread "
                "open needs a closing question card, or it finishes on a beat "
                "with no comment prompt and no completion signal"
            )
    return reasons


def _states_in(state: str) -> dict[str, str]:
    """The prop configurations out of a state string."""
    parts = (state or "").split("|")
    if len(parts) < 2 or not parts[1]:
        return {}
    out = {}
    for chunk in parts[1].split(","):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            if value:
                out[key] = value
    return out


def prompt_consistency(prompts: list[str]) -> list[str]:
    """Rule 7 — locked descriptions are byte-identical across the whole set.

    Run on the composed prompt strings themselves, which is the only place the
    guarantee actually matters and is free to check: a locked line that appears
    in two panels must appear identically in both.
    """
    reasons: list[str] = []
    seen: dict[str, str] = {}
    for i, prompt in enumerate(prompts, 1):
        for line in (prompt or "").splitlines():
            match = re.match(r"^(.{1,60}?) \(locked[^)]*\): (.+)$", line.strip())
            if not match:
                continue
            subject, description = match.group(1), match.group(2)
            if subject in seen and seen[subject] != description:
                reasons.append(
                    f"panel {i}: the locked description of {subject} differs "
                    "from the one used earlier in the set — it must be the same "
                    "string every time, never re-summarised"
                )
            seen.setdefault(subject, description)
    return reasons


def _text_problems(panel: Panel, cast: list[str], present: set[str],
                   where: str, label) -> list[str]:
    """Rules 3, 4, 10 and 11 — the closed set, the glyph budget and attribution."""
    reasons: list[str] = []
    if not panel.text_elements:
        return reasons

    # Rule 3 — every declared string is unique within its panel. A repeated
    # string is a licence to render it twice, which is half of the wall-art bug.
    seen: dict[str, int] = {}
    for element in panel.text_elements:
        key = element.content.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    for text, count in seen.items():
        if count > 1 and text:
            reasons.append(
                f"{where}: \"{text[:40]}\" is declared {count} times — each "
                "string may appear exactly once, or it gets drawn twice"
            )

    # Rule 9 — the word budget. Panel 1 is tighter because it has half a second
    # to earn the swipe, and a text block eating the canvas is what these
    # carousels were stalling on.
    words = word_count(panel)
    cap = MAX_WORDS_PANEL_ONE if panel.index == 0 else MAX_WORDS_PANEL
    if words > cap:
        reasons.append(
            f"{where}: {words} words of copy (cap {cap}"
            + (" — slide 1 has half a second to earn the swipe" if panel.index == 0
               else "")
            + "). Cut it: short copy is both the swipe lever and the main "
            "defence against garbled glyphs."
        )

    total = 0
    for element in panel.text_elements:
        content = element.content.strip()
        total += len(content)

        # Rule 4 — the glyph budget.
        if len(content) > MAX_ELEMENT_CHARS:
            reasons.append(
                f"{where}: the {element.role} runs to {len(content)} characters "
                f"(cap {MAX_ELEMENT_CHARS}) — long text renders small and "
                f"garbles: \"{content[:50]}…\""
            )
        for word in _long_words(content, cast):
            reasons.append(
                f"{where}: \"{word}\" is {len(word)} characters — long, unusual "
                "words garble disproportionately. Rewrite the line with shorter, "
                "commoner words."
            )

        # Rule 10 — no attribution prefixes and no label furniture. An
        # unrecognised speaker label survives derivation intact; this is where
        # it stops, because printing "DAVE:" on the artwork is the failure.
        leftover = _SPEAKER_PREFIX.match(content)
        if leftover and element.role == "caption":
            reasons.append(
                f"{where}: \"{content[:40]}\" still carries a speaker label. "
                "Attribution is the bubble's tail, never a prefix — either name "
                "a character from the cast, or write it as narration."
            )

        # Rule 11 — a tail has to point at something that is in the panel.
        if element.role in ("dialogue", "broadcast"):
            if not element.anchor:
                reasons.append(
                    f"{where}: the {element.role} \"{content[:40]}\" has no "
                    "anchor — a bubble with nothing to point at is a floating "
                    "label"
                )
            elif element.role == "dialogue" and element.anchor not in present:
                reasons.append(
                    f"{where}: the dialogue \"{content[:40]}\" is anchored to "
                    f"{label(element.anchor)}, who is not in the panel"
                )
            elif element.role == "broadcast" and element.anchor not in panel.props_present:
                reasons.append(
                    f"{where}: the broadcast \"{content[:40]}\" issues from "
                    f"{element.anchor}, which is not in the panel's props — the "
                    "emitting object has to be visibly in frame"
                )

    if total > MAX_PANEL_CHARS:
        reasons.append(
            f"{where}: {total} characters of text on one panel (cap "
            f"{MAX_PANEL_CHARS}) — cut it. Shorter copy is both the swipe lever "
            "and the main defence against garbled glyphs."
        )
    return reasons


def _long_words(text: str, cast: list[str]) -> list[str]:
    """Words over the cap, excluding anything that is part of a cast name."""
    from .roster import get_character

    exempt = set()
    for key in cast:
        char = get_character(key)
        if char:
            exempt |= {w.lower().strip(_QUOTES) for w in char.name.split()}
    out = []
    for word in re.findall(r"[A-Za-z'’\-]+", text):
        bare = word.lower().strip("-")
        if len(bare) > MAX_WORD_CHARS and bare not in exempt:
            out.append(word)
    return out


# --- what the image prompt is told ------------------------------------------


def cast_block(panel: Panel, show=None) -> str:
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
    props = _prop_sheet(panel, show)
    if props:
        parts.append(props)
    return "\n\n".join(parts)


def _prop_sheet(panel: Panel, show=None) -> str:
    """The locked descriptions for this panel's objects, verbatim.

    Byte-identical wherever a prop appears, which is guaranteed rather than
    hoped for: `Prop.visual_lock()` is a pure function of the prop, so there is
    no route by which one panel gets a re-summarised version of it.
    """
    sheet = getattr(show, "props", None) or {}
    lines = [sheet[k].visual_lock() for k in panel.props_present if k in sheet]
    unknown = [k for k in panel.props_present if k not in sheet]
    out: list[str] = []
    if lines:
        out.append(
            "OBJECTS IN THIS PANEL (locked — hold each identical in every panel "
            "it appears in):\n" + "\n".join(lines)
        )
    if unknown:
        out.append(
            "ALSO REQUIRED IN FRAME — "
            + ", ".join(sorted(unknown))
            + ". Text issues from this object, so it must be visibly in the "
            "panel with clear space around it."
        )
    return "\n\n".join(out)


def staging(panel: Panel) -> str:
    """Blocking, camera, crowd density and the escalation's exact value.

    All four were previously left for the generator to invent per frame, which
    is why consecutive panels came out as the same moment, why a caption could
    contradict its own artwork, and why the background crowd changed size for
    no reason.
    """
    parts: list[str] = []
    if panel.blocking:
        parts.append(
            "BLOCKING — the exact configuration of this moment, and it is not "
            "open to interpretation: " + panel.blocking + "."
        )
    if panel.camera:
        parts.append(f"CAMERA: {panel.camera}.")
    if panel.crowd_count is not None:
        parts.append(
            f"BACKGROUND: exactly {panel.crowd_count} other figures in the gym "
            "behind the action, generic and unrecognisable, none of them a cast "
            "member. This number is fixed for the panel — do not add more to "
            "fill the frame and do not thin them out."
        )
    if panel.progress:
        parts.append(panel.progress)
    note = layout_note(panel)
    if note:
        parts.append(note)
    return "\n\n".join(parts)


#: Placement wording per role, for the labels in the spec below.
_ROLE_LABEL = {
    "title": "SHOW MASTHEAD",
    "caption": "NARRATION BOX",
    "dialogue": "SPEECH BUBBLE",
    "broadcast": "MEDIATED SOUND",
    "prop_label": "TEXT ON A PROP",
}


def text_spec(panel: Panel) -> str:
    """The panel's lettering, declared as a closed set rather than implied.

    The old prompt ended with `TEXT TO PLACE ON IMAGE:` and two bare strings,
    while the beat's narrative text sat higher up in the same prompt — so the
    model treated the words as things that exist in the world and rendered them
    as the caption AND as a poster AND on the wall. Three rules fix that, and
    all three have to be stated: verbatim, closed set, one instance each.
    """
    if not panel.text_elements:
        return (
            "TEXT ON THIS PANEL: NONE. This panel carries no words at all — no "
            "narration, no bubbles, no signage, no lettering of any kind "
            "anywhere in the frame."
        )
    lines = [_SAFE_AREA, ""]
    lines.append(
        "TEXT ON THIS PANEL — this is the COMPLETE and CLOSED list of every "
        "word that appears anywhere in the image:"
    )
    for i, element in enumerate(panel.text_elements, 1):
        role = _ROLE_LABEL.get(element.role, element.role.upper())
        lines.append(
            f"{i}. {role} — {element.placement}. Set in {element.style}.\n"
            f'   Render exactly, character for character: "{element.content}"'
        )
    lines.append(
        "HOW THE TEXT IS RENDERED — three absolute rules:\n"
        "- VERBATIM. Each string above is drawn exactly as written, spelled "
        "exactly as given. Do not paraphrase it, do not shorten it, do not "
        "correct it, do not add a word to it.\n"
        "- CLOSED SET. These are the ONLY words anywhere in this panel. No "
        "other signage, no posters, no wall art, no whiteboards, no banners, "
        "no labels on equipment, no logos, no watermarks, no page numbers, no "
        "background lettering, no incidental text of any kind. If a surface in "
        "the scene would realistically carry writing, draw it blank.\n"
        "- ONE INSTANCE EACH. Every string above appears exactly ONCE in the "
        "panel. Never repeat a line as background decoration.\n"
        "- NO LABELS. Never letter a speaker's name, a role, a caption tag or "
        "a badge next to any text. A bubble's tail pointing at the speaker IS "
        "the attribution; a narration box needs no label because its position "
        "says what it is."
    )
    lines.append(
        "GIVE EVERY TEXT ELEMENT GENEROUS CLEAR SPACE — a calm, uncluttered "
        "area of the artwork behind it. Text over busy detail degrades badly. "
        "Set it in a heavy display face, all-caps, high contrast — large, but "
        "never larger than the safe area can hold."
    )
    return "\n\n".join(lines)


#: Stated FIRST, before the strings themselves. It used to be one sentence at
#: the end of the last paragraph, under "set it large", and the model resolved
#: that ordering the way anyone would: it set the text large and let it run off
#: the frame.
_SAFE_AREA = (
    "THE SAFE AREA — read this before you place a single word, because it "
    "outranks every other instruction about size and position.\n"
    "- The image is a vertical 2:3 phone graphic, and it is published to "
    "TikTok, which lays ITS OWN furniture over the picture: the account name, "
    "the caption and the music row sit across the bottom of the image, and the "
    "like/share/bookmark rail runs down the right-hand edge. Anything drawn "
    f"there is buried.\n"
    f"- EVERY letter of EVERY word must sit inside a box that starts "
    f"{SAFE_TOP}% of the height down from the top, ends {SAFE_BOTTOM}% of the "
    f"height up from the bottom, and is inset {SAFE_SIDE}% from the left and "
    f"{SAFE_RIGHT}% from the right. Nothing may touch or cross those bounds — "
    "no box edge, no outline, no descender, no part of a letter.\n"
    "- NEVER let a line bleed off any edge and NEVER slice a word. If a string "
    "does not fit, REDUCE THE TYPE SIZE until the whole thing fits inside the "
    "safe area. Smaller and complete always beats large and cropped: a "
    "half-visible line is worse than no line, because the reader cannot finish "
    "the sentence.\n"
    "- NO MORE THAN TWO LINES per narration box or bubble. If a string will "
    "not break into two comfortable lines at the size you have chosen, make "
    "the type smaller — do not add a third line and do not let the box grow "
    "past the safe area."
)
