"""THE MULTIVERSE's cast — the approved roster and the world they collide in.

The show is a comic universe of instantly recognisable characters: meme
characters carrying it, caricatured public figures guest-starring. That premise
is funny and it also carries the only real outside risk in this whole plan, so
two things live here rather than in a prompt:

* **the roster is a closed list.** "The engine casts from your allow-list; it
  never free-picks a person" is a likeness rule, and `config/roster.toml` is
  where it's enforced — `chrgd/likeness.py` checks every name a post uses
  against these keys.
* **every character carries a safety class.** `meme_character` has no rights
  holder and only taste applies; `public_figure` gets the full likeness gate;
  `fictional_ip` is parodied in our own drawing, never a copy of theirs.

The visual lock is the same trick `character.py` uses on Amp: a fixed
description prepended to the slide's own brief, which the brief can add a scene
to but never override. That's what keeps a cast recognisable across episodes,
and — for the public figures — what keeps them caricatures.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

ROSTER_FILE = Path(__file__).resolve().parent.parent / "config" / "roster.toml"
#: The worked episode the engine writes against. An abstract spine tells a model
#: what SHAPE to make; a good example tells it what good feels like, and that is
#: the difference between six slides about a theme and a story people swipe.
EXAMPLE_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "multiverse_example.md"
)
#: How to find the joke before writing. The step both failed episodes skipped.
PROCESS_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "story_process.md"
)

#: Route key the episode's cast rides on.
ROUTE_KEY = "cast"

#: Classes that the likeness gate applies to in full.
PROTECTED = ("public_figure", "fictional_ip")


class World(BaseModel):
    location: str = ""
    detail: str = ""
    engine: str = ""
    voice: str = ""
    #: More than four and a carousel can't hold them — this is the single most
    #: common way a comic serial goes soggy.
    max_cast: int = 4

    def as_block(self) -> str:
        bits = []
        if self.location:
            bits.append(f"THE WORLD: {self.location}." +
                        (f" {self.detail}" if self.detail else ""))
        if self.engine:
            bits.append(f"THE ENGINE OF THE SERIES: {self.engine}")
        if self.voice:
            bits.append(f"VOICE: {self.voice}")
        return "\n".join(bits)


class Character(BaseModel):
    key: str
    name: str
    cls: str = Field(default="meme_character", alias="class")
    what: str = ""
    trait: str = ""
    role: str = ""
    visual: str = ""
    #: The seed jokes. A character defined only by a trait sentence makes the
    #: engine invent a personality every episode; a catchphrase and two or
    #: three concrete running gags give it something to play and escalate from
    #: episode one. What the show actually lands gets added per character in
    #: the canon (chrgd/series.py) — the roster is where they begin.
    catchphrase: str = ""
    bits: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def protected(self) -> bool:
        return self.cls in PROTECTED

    def brief_line(self, *, extra_bits: list[str] | None = None) -> str:
        line = f"- {self.name} ({self.what}) — {self.trait}"
        if self.role:
            line += f" In the gym: {self.role}"
        if self.catchphrase:
            line += f' Catchphrase: "{self.catchphrase}"'
        gags = list(self.bits) + [b for b in (extra_bits or []) if b not in self.bits]
        if gags:
            line += (
                "\n    Established running gags — play or ESCALATE these, never "
                "re-explain them: " + "; ".join(gags)
            )
        if self.protected:
            line += (
                "  [CARICATURE ONLY — never photoreal, never using or "
                "endorsing a product, never a fabricated quote.]"
            )
        return line

    def visual_lock(self) -> str:
        """The locked prefix for a slide featuring this character."""
        lock = f"{self.name} (locked design — this outranks any scene detail): {self.visual}"
        if self.protected:
            lock += (
                " This is a stylised comic CARICATURE, not a likeness: bold ink "
                "and halftone, obviously a drawing. Never photorealistic."
            )
        return lock


@lru_cache(maxsize=1)
def _load(path: Path = ROSTER_FILE) -> tuple[World, dict[str, Character]]:
    if not path.exists():
        return World(), {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    world = World(**(data.get("world") or {}))
    chars = {
        key: Character(key=key, **spec)
        for key, spec in (data.get("characters") or {}).items()
    }
    return world, chars


def load_roster() -> dict[str, Character]:
    return _load()[1]


def load_world() -> World:
    return _load()[0]


def get_character(key: str) -> Character | None:
    if not key:
        return None
    return load_roster().get(key)


def resolve(keys: list[str] | None) -> list[Character]:
    """Roster entries for these keys, in the order given, unknowns dropped."""
    out: list[Character] = []
    for key in keys or []:
        char = get_character(str(key))
        if char is not None and char not in out:
            out.append(char)
    return out


def name_index() -> dict[str, Character]:
    """Lower-cased name → character, for scanning finished copy."""
    return {c.name.lower(): c for c in load_roster().values()}


def cast_block(
    cast: list[Character], *, gags: dict[str, list[str]] | None = None
) -> str:
    """The cast, as the instruction an episode is written from.

    `gags` carries the bits this show has ALREADY landed for each character
    (from the canon), on top of the seed bits in the roster. That accumulation
    is what makes a returning character feel like a returning character rather
    than the same premise re-stated — a running gag is only running if the
    engine knows it ran.
    """
    if not cast:
        return ""
    lines = [
        "THIS EPISODE'S CAST — use these characters and no others. Play each "
        "one's established trait and their running gags; those ARE the joke "
        "engine. A gag lands harder the third time if it escalates, so build "
        "on what's established rather than restating it:",
    ]
    lines += [c.brief_line(extra_bits=(gags or {}).get(c.key)) for c in cast]
    return "\n".join(lines)


def load_example() -> str:
    """The gold-standard episode, as a block for the brief. '' if missing."""
    if not EXAMPLE_FILE.exists():
        return ""
    body = EXAMPLE_FILE.read_text(encoding="utf-8").strip()
    return (
        "THE STANDARD TO WRITE TO — a worked episode of this show. Match how "
        "PLAIN it is, never its plot. Study the sentence length, the total "
        "absence of similes, the fact that nothing is mentioned before it has "
        "been shown, and that two characters carry it while the third gets one "
        "line. Easy to follow is the standard; clever writing that costs "
        "comprehension is the way this show fails. Your episode must be a "
        "completely different story.\n\n" + body
    )


def load_process() -> str:
    """The written process for finding a comic idea. '' if missing."""
    if not PROCESS_FILE.exists():
        return ""
    return (
        "THE PROCESS FOR FINDING THE JOKE — follow it, in order. Both previous "
        "failures skipped straight to writing.\n\n"
        + PROCESS_FILE.read_text(encoding="utf-8").strip()
    )


def visual_block(cast: list[Character]) -> str:
    """The locked designs, prepended to every slide's image prompt."""
    if not cast:
        return ""
    lines = ["CHARACTER DESIGNS (locked — hold these identical on every slide):"]
    lines += [c.visual_lock() for c in cast]
    return "\n".join(lines)
