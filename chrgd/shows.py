"""Shows — the named creation paths the studio actually makes content through.

The create journey used to have six doors that differed only in *where the seed
came from*. After the seed, every door hit the same write call, the same look
and the same quality rubric — so a supplement explainer, a comic parody and a
workout card all came out of one dark, thumb-stopping-hot-take machine.

A **Show** is the missing layer: a reusable creation path that owns

  * its **spine** — the slide roles, in order, that give the format its shape;
  * its **voice** — the brief injected into the write call, and the moves it
    is banned from making;
  * its **look** — art direction, palette, type, motif, and (D10) an on-image
    show name, applied above the account's house style;
  * its **gate profile** — what the slide-1 concept gate judges it on, because
    "is this a thumb-stopping hot take" is the wrong question to ask of a
    claims explainer;
  * its **cast** — nobody, Amp, or the Multiverse roster.

Definitions are declarative TOML in `config/shows/*.toml`, loaded exactly the
way `chrgd/mechanics.py` loads its gallery: cached, permissive, and returning
None for an unknown key rather than raising.

**Everything here is additive.** An idea with no `show` on its route resolves to
None, every consumer falls back to the path it took before, and the output is
byte-identical to the pre-show engine. That property is covered by tests in
`tests/test_shows.py` and is the thing to protect when editing this module.

A note on furniture, because it surprises people: on a real render the engine
runs in `ai_design` mode, where the image model designs the WHOLE slide and no
brand frame is drawn in code at all (see `images.render_slide`). So the
`[look.furniture]` toggles below only affect the dry-run preview and the legacy
overlay path. What actually makes a show look like itself on a paid render is
`style_preset` + `house_style` + the look block — which is why those carry the
weight here.
"""

from __future__ import annotations

import json
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

SHOWS_DIR = Path(__file__).resolve().parent.parent / "config" / "shows"
GATE_PROFILES_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "gate_profiles.toml"
)

#: Route key the chosen show rides on, alongside `style` / `mechanic_lock`.
ROUTE_KEY = "show"


class Spine(BaseModel):
    """The slide roles that give a show its recognisable shape.

    A floor, not a cage: `flex` is how many slides the engine may add or drop
    when an idea genuinely wants a different length. Set `flex = 0` to pin the
    count exactly.
    """

    roles: list[str] = Field(default_factory=list)
    briefs: list[str] = Field(default_factory=list)
    flex: int = 1

    def as_lines(self) -> list[str]:
        """The spine as slide-by-slide instructions for the write call."""
        out: list[str] = []
        for i, role in enumerate(self.roles):
            brief = self.briefs[i] if i < len(self.briefs) else ""
            out.append(f"  slide {i + 1} ({role}): {brief}" if brief
                       else f"  slide {i + 1}: {role}")
        return out


class Voice(BaseModel):
    block: str = ""
    banned: list[str] = Field(default_factory=list)


class Furniture(BaseModel):
    """Per-show overrides of `brand.toml [identity]`.

    `None` means "inherit the brand default" — that's what keeps four of the
    five shows on shared furniture while the Multiverse runs handle-only (D3).
    Only affects the overlay/dry-run path; see the module docstring.
    """

    wordmark: bool | None = None
    scrim: bool | None = None
    counter: bool | None = None
    handle: bool | None = None
    footer_bar: bool | None = None


class Look(BaseModel):
    style_preset: str = ""
    #: Replaces the account's house style for this show. Empty = inherit.
    house_style: str = ""
    palette: str = ""
    type_style: str = ""
    motif: str = ""
    #: The show's name, rendered on-image (D10). Empty = no title treatment.
    title_card: str = ""
    title_note: str = ""
    furniture: Furniture = Field(default_factory=Furniture)

    def as_block(self) -> str:
        """The look, as art direction appended to every slide's image prompt."""
        bits: list[str] = []
        if self.house_style.strip():
            bits.append(self.house_style.strip())
        if self.palette.strip():
            bits.append(f"Palette: {self.palette.strip()}")
        if self.type_style.strip():
            bits.append(f"Typography: {self.type_style.strip()}")
        if self.motif.strip():
            bits.append(f"Recurring motif: {self.motif.strip()}")
        return " ".join(bits)


class Show(BaseModel):
    key: str
    label: str
    tagline: str = ""
    blurb: str = ""
    icon: str = ""
    weekday: str = ""
    kpi: str = ""
    slides_min: int = 3
    slides_max: int = 8
    #: Which tailored second screen the create journey opens (Phase 0 ships the
    #: generic seed screen for every show; later phases add the bespoke ones).
    front_screen: str = "seed"
    #: none | amp | roster
    cast: str = "none"
    #: Key into config/gate_profiles.toml. Empty = today's default rubric.
    gate_profile: str = ""
    #: save | share | comment — the off-screen action the show is built to earn.
    engagement_play: str = ""
    spine: Spine = Field(default_factory=Spine)
    voice: Voice = Field(default_factory=Voice)
    look: Look = Field(default_factory=Look)

    # --- what the write call is told ----------------------------------------

    def brief_block(self, *, include_length: bool = True) -> str:
        """The show's brief, injected into the build's user message.

        This is what turns one general-purpose engine into five formats. It is
        deliberately a *brief* rather than a separate system prompt: the JSON
        contract, the QA self-scoring and the `Post` model are identical across
        all five shows, and five prompt files would drift apart within a month.
        """
        lines = [
            f"THIS POST IS AN EPISODE OF **{self.label}** — one of the "
            "account's recurring shows. It must read as that show, not as a "
            "generic post from this account.",
        ]
        if self.tagline:
            lines.append(f"- what the show is: {self.tagline}")
        if self.kpi:
            lines.append(f"- what it is built to earn: {self.kpi}")
        if self.engagement_play:
            lines.append(
                f"- the deliberate off-screen action: {self.engagement_play} "
                "— execute it explicitly in the copy, not just in the route."
            )
        if self.voice.block.strip():
            lines.append("")
            lines.append(self.voice.block.strip())
        if self.voice.banned:
            lines.append("")
            lines.append(
                "NEVER, in this show: " + "; ".join(self.voice.banned) + "."
            )
        if self.spine.roles:
            lines.append("")
            lines.append(
                f"THE SPINE — {self.label} always runs this shape, and the "
                "audience learns it. Write each slide to its job:"
            )
            lines.extend(self.spine.as_lines())
            if self.spine.flex > 0:
                lines.append(
                    f"You may add or drop up to {self.spine.flex} slide(s) if "
                    "the idea genuinely needs it — but the shape above is the "
                    "default and the reason the show is recognisable."
                )
            else:
                lines.append(
                    "Use exactly these slides — this show's shape is fixed."
                )
        if include_length:
            lines.append("")
            lines.append(
                f"LENGTH: aim for {self.slides_min}-{self.slides_max} slides."
            )
        return "\n".join(lines)


class GateProfile(BaseModel):
    """What the slide-1 concept gate judges a show ON.

    A profile does not replace the gate's rubric — it appends a focus block to
    each stage's system prompt. The empty profile (the default, and what an
    unknown key resolves to) appends nothing, so the gate behaves exactly as it
    did before shows existed.
    """

    key: str = ""
    label: str = ""
    judge_focus: str = ""
    rival_focus: str = ""
    tourney_focus: str = ""
    glance_focus: str = ""


#: The no-op profile: every focus block empty, so nothing is appended anywhere.
DEFAULT_GATE_PROFILE = GateProfile()


@lru_cache(maxsize=1)
def load_gate_profiles(path: Path = GATE_PROFILES_FILE) -> dict[str, GateProfile]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, GateProfile] = {}
    for key, spec in (data.get("profiles") or {}).items():
        out[key] = GateProfile(key=key, **spec)
    return out


def get_gate_profile(key: str) -> GateProfile:
    """The named profile, or the no-op default for '' / unknown keys."""
    if not key:
        return DEFAULT_GATE_PROFILE
    return load_gate_profiles().get(key, DEFAULT_GATE_PROFILE)


def gate_profile_for_idea(idea) -> GateProfile:
    """The gate profile this idea should be judged under.

    Off-format ideas (no show) get the no-op profile, which is what keeps the
    gate byte-identical to its pre-show behaviour for every existing post.
    """
    show = show_for_idea(idea)
    return get_gate_profile(show.gate_profile if show else "")


# --- loading ----------------------------------------------------------------


@lru_cache(maxsize=1)
def load_shows(directory: Path = SHOWS_DIR) -> dict[str, Show]:
    """Every show defined in `config/shows/*.toml`, keyed by its key.

    Permissive by design, matching `mechanics.load_mechanics`: a missing
    directory yields an empty registry and the studio behaves exactly as it did
    before shows existed.
    """
    if not directory.exists():
        return {}
    out: dict[str, Show] = {}
    for path in sorted(directory.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        spec = dict(data.get("show") or {})
        if not spec:
            continue
        spec.setdefault("key", path.stem)
        for section in ("spine", "voice", "look"):
            if section in data:
                spec[section] = data[section]
        show = Show(**spec)
        out[show.key] = show
    return out


def get_show(key: str) -> Show | None:
    if not key:
        return None
    return load_shows().get(key)


def ordered_shows() -> list[Show]:
    """Shows in the order they run through the week — what /create lists."""
    order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    def sort_key(show: Show) -> tuple[int, str]:
        day = show.weekday.strip().lower()[:3]
        return (order.index(day) if day in order else len(order), show.label)

    return sorted(load_shows().values(), key=sort_key)


# --- reading the choice off an idea -----------------------------------------


def show_key_for_route(route: dict | None) -> str:
    return str((route or {}).get(ROUTE_KEY) or "")


def show_for_idea(idea) -> Show | None:
    """The show this idea is an episode of, or None for an off-format post.

    Mirrors `images.style_for_idea`, so no other module has to parse route_json
    by hand. None is the pass-through: every consumer falls back to the
    pre-show path.
    """
    raw = getattr(idea, "route_json", None)
    if not raw:
        return None
    try:
        route = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return get_show(show_key_for_route(route))


def job_show_filter(show: str) -> tuple[str, tuple]:
    """SQL fragment + params matching concept jobs belonging to `show`.

    Concept jobs created before the show layer existed have no `show` key in
    their params at all. Those are off-format sets, so an empty `show` must
    match them as well as rows explicitly stamped `"show": ""` — otherwise a
    live database's entire concept history goes invisible the moment this ships
    and every reopened /create pays for a fresh run.
    """
    if show:
        return ("AND COALESCE(params_json,'{}') LIKE ? ", (f'%"show": "{show}"%',))
    return (
        "AND (COALESCE(params_json,'{}') LIKE '%\"show\": \"\"%' "
        "OR COALESCE(params_json,'{}') NOT LIKE '%\"show\":%') ",
        (),
    )


def brand_for_show(brand, show: Show | None):
    """A copy of `brand` with the show's furniture overrides applied.

    Returns `brand` untouched when there's no show or nothing to override, so
    the common path allocates nothing. Only the overlay/dry-run path renders
    this furniture — see the module docstring.
    """
    if show is None:
        return brand
    f = show.look.furniture
    changes: dict = {}
    if f.wordmark is False:
        changes["wordmark"] = ""
        changes["logo_path"] = ""
    if f.handle is False:
        changes["handle"] = ""
    if f.scrim is not None:
        changes["scrim"] = f.scrim
    if f.counter is not None:
        changes["show_counter"] = f.counter
    if f.footer_bar is not None:
        changes["footer_bar"] = f.footer_bar
    if not changes:
        return brand
    out = brand.model_copy(deep=True)
    out.identity = out.identity.model_copy(update=changes)
    return out
