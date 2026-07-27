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

#: The three states Amp moves through, keyed by the band they cover.
CHARGE_STATES: dict[str, dict[str, str]] = {
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


def character_block(charge: int, *, include_world: bool = False) -> str:
    """The LOCKED character prefix for one slide's image prompt.

    Assembly order matters: who he is, then what state he's in, then the style
    floor. The slide's own brief is appended after this by the caller and may
    add a scene — but everything here outranks it, so a topic can never restyle
    the character.
    """
    parts = [
        f"CHARACTER (locked — this description outranks any scene detail below): "
        f"{CANONICAL_PROMPT}",
        charge_block(charge),
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


def is_amp_route(route: dict | None) -> bool:
    """True when an idea's route marks it as an Amp charge-cycle post.

    Checks the stored mechanic lock (how the blank-canvas gallery records a
    chosen format) as well as an explicit `amp` flag, so the journey can also be
    switched on directly without going through the gallery.
    """
    if not isinstance(route, dict):
        return False
    if route.get("amp") is True:
        return True
    names = {MECHANIC_KEY, MECHANIC_LABEL}
    lock = route.get("mechanic_lock")
    if isinstance(lock, dict):
        return lock.get("key") in names or lock.get("name") in names
    return route.get("mechanic") in names


def charges_for_route(route: dict | None, slide_count: int) -> list[int] | None:
    """The per-slide charge arc for an Amp idea, or None when Amp isn't active.

    A stored arc (written when the idea was created) wins so an editor's tweak
    survives; otherwise the arc is derived from the slide count. Returning None
    for non-Amp ideas is what keeps this a pass-through for every other post.
    """
    if not is_amp_route(route):
        return None
    stored = (route or {}).get("charge_arc")
    if isinstance(stored, list) and stored:
        arc = [int(c) for c in stored[:slide_count]]
        # A short stored arc (slides added later) is topped up to full length.
        if len(arc) < slide_count:
            arc += charge_arc(slide_count)[len(arc):]
        return arc
    return charge_arc(slide_count)
