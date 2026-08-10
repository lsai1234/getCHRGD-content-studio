"""Amp — the getCHRGD mascot, and the charge-cycle arc that drives his posts.

This module is the single source of truth for how Amp looks and how his charge
level maps onto a carousel, so every image prompt describes the same character
no matter what the slide is about.

The core idea: **Amp's charge level IS the narrative arc.** Slide 1 opens on him
drained by a relatable "drain event", the middle slides charge him back up, and
the final slide lands on a fully-charged Amp plus the CTA. One post = one charge
cycle.

How it plugs into the engine (deliberately additive — nothing here replaces an
existing path):

  * `config/mechanics.toml` carries the `amp_charge_cycle` mechanic, so Amp is
    **selectable** from the blank-canvas gallery like any other format. He is
    not the default; promote him only once test carousels look right.
  * `charge_arc()` assigns each slide a rising charge %, stored on the idea's
    `route_json` alongside the other creation prefs.
  * `character_block()` is prepended to that slide's image prompt as a LOCKED
    prefix: the slide's own brief can add a scene, but can never override who
    Amp is or what he looks like. This is the fix for slide copy hijacking the
    character's art direction.

Note on the visual anchor: text alone drifts across generations. The engine
already supports a locked character portrait (`BrandProfile.character_image`,
attached at render as an identity reference), so the strongest setup is to
generate and approve one canonical Amp render, then save it as the brand
character image. This module supplies the words; that portrait supplies the
pixels.
"""

from __future__ import annotations

import json
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# The mechanic key in config/mechanics.toml. An idea whose route carries this
# (as `mechanic_lock`) is an Amp post and gets the locked character prefix.
MECHANIC_KEY = "amp_charge_cycle"
# The human label the same mechanic is stored under. The create journey records
# a mechanic as {key, name, skeleton}, but older ideas (and any path that only
# had the label to hand) carry the label alone — match either so an Amp post is
# never silently treated as a normal carousel.
MECHANIC_LABEL = "Amp's charge cycle"

BRAND = "getCHRGD"
NAME = "Amp"
SIGN_OFF = "Stay amped."

#: Who Amp is, in the words every prompt starts from. Never overridden by a
#: slide's own image brief — it is prepended as an immutable prefix.
CANONICAL_PROMPT = (
    "Amp, the getCHRGD mascot: a friendly cartoon character shaped like a bold, "
    "rounded lightning bolt. Electric cyan-blue body (#29C2F2) with a clean "
    "thick black outline. Two simple round white eyes with small black pupils, "
    "a small expressive mouth, short stubby arms and legs. Chunky sticker-style "
    "flat vector look, minimal shading, energetic. Head-heavy proportions, about "
    "2.5 heads tall."
)

#: The art-direction floor. Held identical on every Amp slide.
STYLE_LOCK = (
    "flat vector, sticker-friendly, bold outlines, consistent proportions, "
    "no photoreal rendering"
)

#: Amp's home — used when a slide wants an interior rather than a real-world scene.
WORLD = (
    "The Cell (the inside of a battery): a vertical interior with glowing "
    "horizontal charge-bar floor lines and a charging dock at the base, the "
    "lighting warming from dim to electric as charge rises."
)

#: The three states the CHARGE ARC moves through, keyed by the band they cover.
#: A subset of AMP_STATES (below) — the charge cycle is now one of Amp's
#: spines rather than the only one he has.
_LEGACY_CHARGE_STATES: dict[str, dict[str, str]] = {
    "drained": {
        "body": "desaturated grey-blue, no glow, visibly dim",
        "posture": "slouched and heavy, droopy eyes, a small red low-battery "
                   "tint at his feet",
        "mood": "flat and defeated, but relatable rather than sad",
    },
    "charging": {
        "body": "normal electric cyan (#29C2F2) with a mild glow and a few "
                "small sparks",
        "posture": "upright, straightening out, coming back to life",
        "mood": "getting there — hopeful, on the way up",
    },
    "charged": {
        "body": "vivid electric cyan with a bright glow and aura, small "
                "lightning arcs and sparks coming off him",
        "posture": "confident hero pose, big grin",
        "mood": "unstoppable",
    },
}

#: Kept as the public name — every existing caller reads it, and the three
#: charge bands themselves are unchanged.
CHARGE_STATES = _LEGACY_CHARGE_STATES


#: Amp's emotional range, decoupled from the charge cycle (D5).
#:
#: The charge arc forces him flat → charged on every post, which is a strong
#: container and a fast route to sameness. His state is now free: a post can
#: open him smug and humble him, or run him beaming the whole way. Each state
#: carries the body colour, posture and mood that the image prompt describes —
#: his COLOUR is how the emotion reads at thumb size, so this is the lever that
#: makes an Amp post feel different week to week.
AMP_STATES: dict[str, dict[str, str]] = {
    "drained": {
        "label": "Drained",
        "body": "desaturated grey-blue, no glow, visibly dim",
        "posture": "slouched and heavy, droopy eyes, a small red low-battery "
                   "tint at his feet",
        "mood": "flat and defeated, but relatable rather than sad",
    },
    "flat": {
        "label": "Flat",
        "body": "muted cyan, dull, barely any glow",
        "posture": "upright but listless, shoulders down, unimpressed face",
        "mood": "can't be bothered — the state of a wet Tuesday",
    },
    "wired": {
        "label": "Wired",
        "body": "over-bright cyan with erratic sparks flying off him",
        "posture": "jittery, wide eyes, vibrating slightly, too much energy",
        "mood": "massively over-caffeinated and about to regret it",
    },
    "charging": {
        "body": "normal electric cyan (#29C2F2) with a mild glow and a few "
                "small sparks",
        "label": "Charging",
        "posture": "upright, straightening out, coming back to life",
        "mood": "getting there — hopeful, on the way up",
    },
    "beaming": {
        "label": "Beaming",
        "body": "vivid saturated cyan with a warm bright glow and a colourful "
                "halo of light around him",
        "posture": "open, arms out, huge genuine grin",
        "mood": "delighted with himself and with everything",
    },
    "charged": {
        "label": "Charged",
        "body": "vivid electric cyan with a bright glow and aura, small "
                "lightning arcs and sparks coming off him",
        "posture": "confident hero pose, big grin",
        "mood": "unstoppable",
    },
    "smug": {
        "label": "Smug",
        "body": "bright cyan, steady even glow, no sparks",
        "posture": "arms folded, eyebrow raised, leaning on something",
        "mood": "insufferably pleased with himself — and usually about to be "
                "humbled",
    },
    "knackered_happy": {
        "label": "Knackered but happy",
        "body": "warm cyan with a soft fading glow, slightly washed out",
        "posture": "sat down, sprawled, head back, contented grin",
        "mood": "wrecked in the good way — the post-session slump",
    },
}

#: The states offered in the create journey, in the order they're shown.
STATE_ORDER = (
    "drained", "flat", "wired", "charging",
    "beaming", "charged", "smug", "knackered_happy",
)


def state_block(state: str) -> str:
    """The per-slide description of Amp in a named emotional state.

    The free-state counterpart to `charge_block`: no meter, no percentage —
    just who he is right now. Unknown states fall back to `charging` so a typo
    in a stored route can never break a render.
    """
    spec = AMP_STATES.get(state) or AMP_STATES["charging"]
    return (
        f"Amp is {spec['label'].lower()}. "
        f"His body is {spec['body']}. "
        f"He is {spec['posture']}. "
        f"He reads as {spec['mood']}."
    )


def state_for_charge(charge: int) -> str:
    """The charge-state name for a charge percentage.

    Bands follow the concept: drained at the bottom, charged at the top, and
    everything in between is the climb.
    """
    if charge <= 30:
        return "drained"
    if charge >= 80:
        return "charged"
    return "charging"


def charge_arc(slide_count: int) -> list[int]:
    """A monotonically rising charge % per slide, always ending fully charged.

    Slide 1 opens drained (10%) and the final slide lands at 100%, with the
    middle slides spread evenly between — so the swipe itself reads as Amp
    charging up. A single-slide post is simply the fully-charged payoff.
    """
    n = max(1, slide_count)
    if n == 1:
        return [100]
    start = 10
    step = (100 - start) / (n - 1)
    return [round(start + step * i) for i in range(n)]


def charge_block(charge: int) -> str:
    """The per-slide description of Amp at this charge level."""
    name = state_for_charge(charge)
    state = CHARGE_STATES[name]
    return (
        f"Amp is at {charge}% charge ({name}). "
        f"His body is {state['body']}. "
        f"He is {state['posture']}. "
        f"He reads as {state['mood']}. "
        "Show a small charge meter in a consistent corner of the frame, filled "
        f"to roughly {charge}%."
    )


def character_prefix(
    *, charge: int | None = None, state: str = "", include_world: bool = False
) -> str:
    """The LOCKED character prefix for one slide's image prompt.

    Assembly order matters: who he is, then what state he's in, then the style
    floor. The slide's own brief is appended after this by the caller and may
    add a scene — but everything here outranks it, so a topic can never restyle
    the character.

    Two ways to say what state he's in, and exactly one applies per post:
      * `charge` — the charge cycle, with its meter and rising percentage;
      * `state`  — the free expression system (D5), no meter, no percentage.
    The locked description and the style floor are identical either way, which
    is what keeps him on-model whichever spine the post runs.
    """
    parts = [
        f"CHARACTER (locked — this description outranks any scene detail below): "
        f"{CANONICAL_PROMPT}",
        state_block(state) if state else charge_block(charge or 0),
    ]
    if include_world:
        parts.append(f"Setting: {WORLD}")
    parts.append(f"Art direction (locked): {STYLE_LOCK}.")
    parts.append(
        "Keep Amp's design identical to the description above on every slide — "
        "same shape, same proportions, same colours. Only his charge state, pose "
        "and the scene around him change."
    )
    return "\n".join(parts)


def character_block(charge: int, *, include_world: bool = False) -> str:
    """The charge-cycle prefix. Kept as the name every existing caller uses."""
    return character_prefix(charge=charge, include_world=include_world)


#: Who Amp is as a personality — the voice the copy is written in. Deliberately
#: not a superhero: he gets wrecked by leg day too, which is what makes the
#: drain half of the cycle land.
PERSONA = (
    "Amp is upbeat but never annoying — a lovable try-hard on the journey WITH "
    "the audience, not a superhero who has it sorted. He gets wrecked by leg "
    "day, dodgy sleep and the 3pm slump like everyone else. Cheeky, dry British "
    "humour. Never corporate, never a brand voice doing 'fun'."
)


def build_brief(slide_count: int) -> str:
    """The copy brief injected into the build prompt for an Amp post.

    Without this the engine writes a normal carousel that merely *looks* like
    Amp once the images render. This makes the charge cycle the story: the words
    have to travel drained → charging → charged too.
    """
    arc = charge_arc(slide_count)
    beats = ", ".join(f"slide {i + 1} at {c}%" for i, c in enumerate(arc))
    return "\n".join([
        f"AMP POST: this is an '{MECHANIC_LABEL}' carousel starring {NAME}, "
        f"the {BRAND} mascot — an electric-blue lightning bolt with a face, "
        "stubby arms and legs.",
        f"VOICE: {PERSONA}",
        "THE CYCLE IS THE STORY: the post is ONE charge cycle. Open on a real "
        "drain event that flattens Amp (write the drain, don't explain it), "
        "let the middle slides genuinely turn it around, and land the final "
        "slide on the payoff with Amp fully charged.",
        f"CHARGE ARC (the images follow this — write copy that matches the "
        f"energy at each step): {beats}.",
        f"SIGN-OFF: end the final slide with '{SIGN_OFF}'",
        "Write Amp as a mate, in first person where it helps. The product/"
        "hydration/recovery payoff should feel like the thing that recharged "
        "him, never an ad read.",
    ])


# --- the situation bank -----------------------------------------------------

SITUATIONS_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "amp_situations.toml"
)


class Situation(BaseModel):
    key: str
    label: str
    setup: str = ""
    states: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)

    def default_state(self) -> str:
        for st in self.states:
            if st in AMP_STATES:
                return st
        return "flat"


@lru_cache(maxsize=1)
def load_situations(path: Path = SITUATIONS_FILE) -> dict[str, Situation]:
    """Amp's situation bank, keyed by key. Missing file = empty bank, and the
    journey falls back to a free-text situation."""
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        key: Situation(key=key, **spec)
        for key, spec in (data.get("situations") or {}).items()
    }


def get_situation(key: str) -> Situation | None:
    if not key:
        return None
    return load_situations().get(key)


def used_situations(store, *, limit: int = 8) -> set[str]:
    """Situation keys used by recent Amp posts.

    Feeds the create screen's "recently used" marks. A NUDGE, never a rule
    (D12): the editor can pick a used situation and nothing stops them.
    """
    rows = store.recent_routes('"amp_situation"', limit=limit)
    out: set[str] = set()
    for row in rows:
        try:
            route = json.loads(row["route_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        key = str(route.get("amp_situation") or "")
        if key:
            out.add(key)
    return out


def situation_brief(route: dict | None) -> str:
    """The copy brief for an AMP-show post: the situation, his state, the tip.

    The charge-cycle brief (`build_brief`) tells the engine to travel drained →
    charged. This one doesn't: under D5 the show is tip-led and Amp's state is
    whatever the story needs, so the brief pins the SITUATION and the TAKEAWAY
    instead of an energy curve.
    """
    route = route or {}
    lines = [
        f"AMP POST: this stars {NAME}, the {BRAND} mascot — an electric-blue "
        "lightning bolt with a face, stubby arms and legs.",
        f"VOICE: {PERSONA}",
    ]
    sit = get_situation(str(route.get("amp_situation") or ""))
    custom = str(route.get("amp_situation_text") or "").strip()
    if sit:
        lines.append(
            f"THE SITUATION — build the post on this exact one, and make it "
            f"specific: {sit.label}. {sit.setup}"
        )
    elif custom:
        lines.append(
            f"THE SITUATION — build the post on this exact one, and make it "
            f"specific: {custom}"
        )
    state = state_for_route(route)
    if state:
        spec = AMP_STATES[state]
        lines.append(
            f"AMP'S STATE: he is {spec['label'].lower()} — {spec['mood']}. The "
            "images carry this, so write copy with the same energy. He does "
            "NOT have to end the post fully charged; land wherever the story "
            "actually lands."
        )
    tip = str(route.get("amp_tip") or "").strip()
    if tip:
        lines.append(f"THE TIP the post must deliver: {tip}")
    lines.append(
        "TIP-LED: the post must leave the viewer with ONE thing they could "
        "actually do. State it plainly on the turn — do not bury it in the "
        "joke. Keep it to behaviour and habit (going anyway, packing the bag, "
        "the 10-minute rule); send dosing and physiology to another post."
    )
    lines.append(f"SIGN-OFF: end the final slide with '{SIGN_OFF}'")
    lines.append(
        "Write Amp as a mate, first person where it helps. Specificity is the "
        "joke — the exact situation, never a general one."
    )
    return "\n".join(lines)


def amp_brief_for(route: dict | None, slide_count: int) -> str:
    """The right Amp brief for however this post was started.

    A post running the free expression system (the AMP show) gets the
    situation brief; the charge cycle — every pre-show Amp post, and the
    mechanic-gallery path — keeps the arc brief it has always had.
    """
    if state_for_route(route):
        return situation_brief(route)
    return build_brief(slide_count)


def is_amp_route(route: dict | None) -> bool:
    """True when an idea's route marks it as an Amp charge-cycle post.

    Checks the AMP show (the route the create journey now uses), the stored
    mechanic lock (how the blank-canvas gallery records a chosen format) and an
    explicit `amp` flag — so a post is recognised as Amp's however it was
    started, including every idea created before the show layer existed.
    """
    if not isinstance(route, dict):
        return False
    if route.get("show") == "amp":
        return True
    if route.get("amp") is True:
        return True
    names = {MECHANIC_KEY, MECHANIC_LABEL}
    lock = route.get("mechanic_lock")
    if isinstance(lock, dict):
        return lock.get("key") in names or lock.get("name") in names
    return route.get("mechanic") in names


def state_for_route(route: dict | None) -> str:
    """The free emotional state this Amp idea runs on, or '' for the arc.

    '' means "not using the expression system" — either it isn't an Amp post at
    all, or it's a charge-cycle one. Callers treat '' as "fall back to charge".
    """
    if not is_amp_route(route):
        return ""
    state = str((route or {}).get("amp_state") or "")
    return state if state in AMP_STATES else ""


def charges_for_route(route: dict | None, slide_count: int) -> list[int] | None:
    """The per-slide charge arc for an Amp idea, or None when Amp isn't active.

    A stored arc (written when the idea was created) wins so an editor's tweak
    survives; otherwise the arc is derived from the slide count. Returning None
    for non-Amp ideas is what keeps this a pass-through for every other post.

    D5 demoted the charge cycle from Amp's only shape to one of them: an idea
    carrying a free `amp_state` is running the expression system instead, and
    gets no arc. Everything that predates the AMP show has no `amp_state`, so
    it keeps the rising arc exactly as before.

    The state is validated via `state_for_route`, not read raw: a state that
    isn't in AMP_STATES has to fall back to the arc, because returning None on
    a value the renderer will also reject would leave the slide with neither —
    and an Amp slide with no locked prefix is an off-model mascot on a paid
    image.
    """
    if not is_amp_route(route):
        return None
    if state_for_route(route):
        return None
    stored = (route or {}).get("charge_arc")
    if isinstance(stored, list) and stored:
        arc = [int(c) for c in stored[:slide_count]]
        # A short stored arc (slides added later) is topped up to full length.
        if len(arc) < slide_count:
            arc += charge_arc(slide_count)[len(arc):]
        return arc
    return charge_arc(slide_count)
