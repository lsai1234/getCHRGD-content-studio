"""Carousel-mechanic templates, loaded from `config/mechanics.toml`.

The create journey's "blank canvas" door offers these as a gallery; choosing
one locks the engine's Stage 2 (format selection) to the mechanic's 5-slide
skeleton via `route_json.mechanic_lock` (see pipeline.build_user_message).
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

MECHANICS_FILE = Path(__file__).resolve().parent.parent / "config" / "mechanics.toml"


class Mechanic(BaseModel):
    key: str
    label: str
    description: str = ""
    skeleton: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_mechanics(path: Path = MECHANICS_FILE) -> dict[str, Mechanic]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Mechanic] = {}
    for key, spec in (data.get("mechanics") or {}).items():
        out[key] = Mechanic(key=key, **spec)
    return out


def get_mechanic(key: str) -> Mechanic | None:
    return load_mechanics().get(key)
