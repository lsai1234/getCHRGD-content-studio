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

from pydantic import BaseModel

from .db import Store

SETTINGS_KEY = "brand_profile"


class BrandProfile(BaseModel):
    # --- brand & voice (feeds the engine) ---
    brand_name: str = ""
    one_liner: str = ""        # what the brand is, one line
    voice: str = ""            # tone/personality
    audience: str = ""         # who it's for
    dos: str = ""              # always do / lean into (free text, newline list ok)
    donts: str = ""            # never do / avoid
    handle: str = ""           # @handle for captions
    default_hashtags: str = ""  # space/comma separated, always-include tags

    # --- visual style (feeds image generation) ---
    house_style: str = ""      # art-direction line applied to every slide
    palette: str = ""          # brand colours + how they're used
    motif: str = ""            # a recurring visual anchor/character/object

    def is_empty(self) -> bool:
        return not any(v.strip() for v in self.model_dump().values())


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


def profile_style_block(profile: BrandProfile) -> str:
    """House art-direction for the image prompts. '' when nothing visual set."""
    rows: list[str] = []
    if profile.house_style.strip():
        rows.append(profile.house_style.strip())
    if profile.palette.strip():
        rows.append(f"Brand palette: {profile.palette.strip()}")
    if profile.motif.strip():
        rows.append(f"Recurring brand motif: {profile.motif.strip()}")
    if not rows:
        return ""
    return (
        "HOUSE BRAND STYLE (apply to EVERY slide so the whole account looks "
        "like one brand): " + " · ".join(rows)
    )


def brand_profile_notes(store: Store) -> str:
    """Convenience: the engine block for the currently-saved profile."""
    return profile_engine_block(load_profile(store))


def brand_style_note(store: Store) -> str:
    """Convenience: the image-style block for the currently-saved profile."""
    return profile_style_block(load_profile(store))
