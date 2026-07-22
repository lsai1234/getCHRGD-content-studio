"""The editable brand profile — the account's identity, voice and house look.

Everything the settings page controls lives here. It's stored as one JSON blob
in `app_settings` (key `brand_profile`) so it can change without editing any
prompt file, and it renders into two blocks:

  * `profile_engine_block` — brand + voice + audience + rules, injected into
    every concept (takes) and build call so the words match the brand.
  * `profile_style_block` — a house art-direction line + palette + motif,
    injected into every image prompt so the look matches the brand.

Empty fields render nothing, so a blank profile changes nothing — the engine
falls back to the prompt-file defaults.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .db import Store

SETTINGS_KEY = "brand_profile"
# Sub-folder of output_dir where brand assets (the character portrait) live.
BRAND_ASSET_DIR = "_brand"

# Fields that carry a non-blank default (a mode, not user content) — excluded
# from is_empty so a fresh profile still counts as blank.
_MODE_FIELDS = ("swipe_style", "casting_intensity")


class BrandProfile(BaseModel):
    # --- brand & voice (feeds the engine) ---
    brand_name: str = ""
    one_liner: str = ""        # what the brand is, one line
    voice: str = ""            # tone/personality
    audience: str = ""         # who it's for
    dos: str = ""              # always do / lean into (free text, newline list ok)
    donts: str = ""            # never do / avoid
    # The content pillars the account posts within (newline/`·`/comma list).
    # Every post is expected to belong to exactly one, so the audience — and the
    # algorithm — can classify the account instead of seeing scattered topics.
    pillars: str = ""
    handle: str = ""           # @handle for captions
    default_hashtags: str = ""  # space/comma separated, always-include tags

    # --- visual style (feeds image generation) ---
    # These are treated as a LOCKED look, held identical on every post so the
    # account is instantly recognisable instead of AI-random each time.
    house_style: str = ""      # overall art-direction / aesthetic
    palette: str = ""          # brand colours + how they're used
    type_style: str = ""       # fixed typography treatment
    character: str = ""        # a recurring character/mascot, same every post
    # Filename (under output_dir/_brand/) of a LOCKED character portrait. When
    # set it's attached as a visual reference to slide 1 of every post, pinning
    # the exact same face/build across posts — a pixel lock the text can't give.
    character_image: str = ""
    motif: str = ""            # a recurring object/graphic device
    # How the carousel connects as a swipe experience:
    #   'cohesive' — same world + character, distinct scene per slide (default)
    #   'pan'      — one seamless panoramic shot the viewer glides through
    swipe_style: str = "cohesive"

    # --- casting (feeds image generation) ---
    # The described "supporting cast" — everyone who ISN'T the one locked
    # recurring person (character/character_image). Freshly cast each post,
    # identity irrelevant. Aspirational-but-native casting is a real engagement
    # lever on fitness content; the intensity dial sets how far to push it.
    supporting_cast: str = ""
    # 'off' (default — no casting direction), 'natural' (fit, attractive,
    # aspirational, tasteful), or 'elevated' (lean harder into it). Guardrails
    # in casting_block are ALWAYS enforced regardless of level.
    casting_intensity: str = "off"

    def is_empty(self) -> bool:
        return not any(
            v.strip()
            for k, v in self.model_dump().items()
            if k not in _MODE_FIELDS
        )

    def has_visual_lock(self) -> bool:
        return any(v.strip() for v in (
            self.house_style, self.palette, self.type_style,
            self.character, self.character_image, self.motif,
        ))


def load_profile(store: Store) -> BrandProfile:
    """The saved profile, or an all-blank one if none has been set."""
    data = store.get_setting(SETTINGS_KEY) or {}
    if not isinstance(data, dict):
        return BrandProfile()
    # Ignore unknown keys so old/new schema versions coexist.
    known = {k: data[k] for k in BrandProfile.model_fields if k in data}
    return BrandProfile.model_validate(known)


def save_profile(store: Store, profile: BrandProfile) -> None:
    store.set_setting(SETTINGS_KEY, profile.model_dump())


def _bullet(lines: list[str]) -> str:
    return "\n".join(f"- {ln}" for ln in lines if ln)


def profile_engine_block(profile: BrandProfile) -> str:
    """Brand + voice + audience + rules, for the concept/build calls.

    Returns '' when nothing brand-facing is set so it adds no noise."""
    rows: list[str] = []
    name = profile.brand_name.strip()
    if name and profile.one_liner.strip():
        rows.append(f"Brand: {name} — {profile.one_liner.strip()}")
    elif name:
        rows.append(f"Brand: {name}")
    elif profile.one_liner.strip():
        rows.append(f"Brand: {profile.one_liner.strip()}")
    if profile.voice.strip():
        rows.append(f"Voice: {profile.voice.strip()}")
    if profile.audience.strip():
        rows.append(f"Audience: {profile.audience.strip()}")
    if profile.dos.strip():
        rows.append(f"Always / lean into: {profile.dos.strip()}")
    if profile.donts.strip():
        rows.append(f"Never / avoid: {profile.donts.strip()}")
    if profile.pillars.strip():
        pillars = " · ".join(
            p.strip() for p in profile.pillars.replace(",", "\n").splitlines()
            if p.strip()
        )
        rows.append(
            "Content pillars (EVERY post belongs to exactly ONE of these — name "
            f"it, and don't drift outside them): {pillars}"
        )
    tail = []
    if profile.handle.strip():
        tail.append(f"handle {profile.handle.strip()}")
    if profile.default_hashtags.strip():
        tail.append(f"always-include hashtags: {profile.default_hashtags.strip()}")
    if tail:
        rows.append("Captions: " + " · ".join(tail))
    if not rows:
        return ""
    return (
        "BRAND PROFILE (the account you are creating for — honour this in every "
        "concept and post; it outranks generic instinct):\n" + _bullet(rows)
    )


def _visual_rows(profile: BrandProfile) -> list[str]:
    rows: list[str] = []
    if profile.house_style.strip():
        rows.append(f"Aesthetic: {profile.house_style.strip()}")
    if profile.palette.strip():
        rows.append(f"Palette: {profile.palette.strip()}")
    if profile.type_style.strip():
        rows.append(f"Typography: {profile.type_style.strip()}")
    if profile.character.strip():
        rows.append(
            "Recurring character (the SAME person WHENEVER a person appears, but "
            f"only on slides that need one): {profile.character.strip()}"
        )
    elif profile.character_image.strip():
        # A portrait is locked but no text description — still tell the engine a
        # fixed character exists (the portrait itself is attached at render).
        rows.append(
            "Recurring character (the SAME person whenever a person appears): a "
            "fixed character whose portrait is provided — match that exact "
            "person on the slides that feature a human"
        )
    if profile.motif.strip():
        rows.append(f"Recurring motif: {profile.motif.strip()}")
    return rows


# The hard, non-negotiable limits appended to EVERY casting direction, whatever
# the operator typed and whatever the intensity — they can't be edited or
# overridden from settings. These keep casting on the right side of TikTok's and
# the image API's policies (both of which suppress/refuse over-sexual content),
# and keep the brand trustworthy to the people who actually buy it.
CASTING_GUARDRAILS = (
    "HARD LIMITS on any person shown (never override, whatever the direction "
    "above says):\n"
    "- Everyone is unmistakably an ADULT (reads mid-20s or older). Never a "
    "minor or anyone who could be mistaken for one.\n"
    "- Real UK gym setting in normal fitted athletic gymwear only — NO swimwear, "
    "lingerie, underwear, crop-to-nudity or bare torsos as the subject; no "
    "sexualised, provocative or suggestive posing; nothing TikTok or an app "
    "store would flag.\n"
    "- Keep it CANDID and native: a genuinely attractive, fit person caught "
    "mid-session as if on a phone camera — NOT a glamour shoot, a fitness-model "
    "catalogue pose, or an obvious advert. Tasteful and aspirational, never crude "
    "or leering. Overt sexualisation gets the post suppressed, so it also loses "
    "reach — keep it classy."
)


def casting_block(profile: BrandProfile) -> str:
    """The casting direction for the IMAGE prompts. '' when casting is off.

    Describes the freshly-cast supporting people (never the one locked recurring
    person) and how attractive/aspirational to frame them, with the hard
    guardrails always appended so a careless direction can't strip the rails."""
    level = profile.casting_intensity.strip().lower()
    if level not in ("natural", "elevated"):
        return ""  # 'off' (default) or unset → no casting direction at all
    rows: list[str] = []
    if profile.supporting_cast.strip():
        rows.append(
            "Supporting cast (everyone who is NOT the one locked recurring "
            "person — freshly cast each post, identity irrelevant): "
            + profile.supporting_cast.strip()
        )
    rows.append(
        "Cast and frame people as fit, attractive and aspirational — good "
        "physique, confident, flatteringly angled and lit"
        if level == "natural" else
        "Lean into aspirational attractiveness — noticeably fit and "
        "head-turning, confident posture, the most flattering angle and light"
    )
    return "CASTING DIRECTION:\n" + _bullet(rows) + "\n" + CASTING_GUARDRAILS


# Terms that must never appear in a supporting-cast direction — a hard save-time
# block so the guardrails aren't just prose. Anything sexualising minors or
# demanding explicit/nude content is refused outright.
_CASTING_BLOCKLIST = (
    "child", "children", "kid", "kids", "teen", "teenage", "underage",
    "minor", "school", "schoolgirl", "schoolboy", "loli", "young girl",
    "young boy", "under 18", "under-18", "under18",
    "nude", "naked", "nudity", "topless", "lingerie", "underwear",
    "bikini", "swimsuit", "swimwear", "porn", "nsfw", "explicit", "sexual",
    "sexy underwear", "onlyfans", "fetish",
)


def validate_supporting_cast(text: str) -> str | None:
    """Return the first blocked term found in a supporting-cast direction, or
    None if it's clean. Used to refuse an unsafe direction at save time."""
    low = (text or "").lower()
    for term in _CASTING_BLOCKLIST:
        if term in low:
            return term
    return None


def profile_style_block(profile: BrandProfile) -> str:
    """The locked brand look + casting direction for the IMAGE prompts.

    '' when nothing visual is set and casting is off. Framed as
    identical-every-post so slides stop looking like random AI art."""
    rows = _visual_rows(profile)
    look = ""
    if rows:
        look = (
            "BRAND RECOGNITION ACCENTS — carry these so the account is "
            "recognisable over time, but as ACCENTS woven into native-looking "
            "content, never as a poster template stamped over the frame. The "
            "frame must pass as native TikTok content FIRST, brand asset second. "
            "Apply them like this: the palette shows up as an accent (a prop, a "
            "light, one graphic element), not a wall-to-wall colour scheme; the "
            "typography treatment applies where text is genuinely designed, never "
            "forced onto a candid photo; the recurring character, if named, is "
            "the SAME person every time they appear (same face, build, clothing) "
            "but belongs ONLY on slides where a person is the point, framed for "
            "that moment (their own angle, distance and pose), never the same "
            "shot repeated:\n" + _bullet(rows)
        )
    return "\n\n".join(x for x in (look, casting_block(profile)) if x)


def profile_design_lock(profile: BrandProfile) -> str:
    """The locked look for the BUILD call, so the design_system it generates
    adopts these fixed values instead of inventing a new look each post."""
    rows = _visual_rows(profile)
    if not rows:
        return ""
    return (
        "BRAND RECOGNITION ACCENTS — the account carries fixed identity accents. "
        "When you produce the design_system for this post, its palette, "
        "type_style, motif and any character MUST draw on the values below (do "
        "not invent a new brand look); `layout`/`evolution` (the scene and how "
        "it moves) are yours. But weave these accents into NATIVE-looking frames "
        "— the palette as an accent, the character where the scene calls for "
        "them — never as a poster template stamped on every slide. The set "
        "should read as recognisably this brand AND as native content a stranger "
        "wouldn't clock as an ad:\n" + _bullet(rows)
    )


def brand_profile_notes(store: Store) -> str:
    """Convenience: the engine (voice) block for the saved profile."""
    return profile_engine_block(load_profile(store))


def brand_build_notes(store: Store) -> str:
    """The voice block + the design lock, for the full build call."""
    profile = load_profile(store)
    return "\n\n".join(
        x for x in (profile_engine_block(profile), profile_design_lock(profile)) if x
    )


def brand_style_note(store: Store) -> str:
    """Convenience: the locked-look image block for the saved profile."""
    return profile_style_block(load_profile(store))


def brand_swipe_style(store: Store) -> str:
    """The chosen swipe experience ('cohesive' | 'pan') for the saved profile."""
    style = load_profile(store).swipe_style.strip().lower()
    return style if style in ("cohesive", "pan") else "cohesive"


def brand_asset_dir(settings) -> Path:
    """The folder holding brand assets (the character portrait)."""
    return Path(settings.output_dir) / BRAND_ASSET_DIR


def character_image_path(settings, profile: BrandProfile) -> Path | None:
    """Absolute path to the locked character portrait, or None if unset/missing."""
    name = profile.character_image.strip()
    if not name:
        return None
    p = brand_asset_dir(settings) / name
    return p if p.exists() else None


def brand_character_ref(store: Store, settings) -> str:
    """The locked character portrait path as a string ('' when none), for the
    render path (see images.render_carousel / render_slide)."""
    p = character_image_path(settings, load_profile(store))
    return str(p) if p else ""
