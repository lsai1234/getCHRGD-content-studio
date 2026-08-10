"""STRAIGHT UP's backlog — the ingredient library.

Without a spine the show gets invented weekly and drifts; with one, every post
starts from a real question someone asks, the evidence as it actually stands,
and the marketing lie worth naming. Loaded the way every other config here is.

D7: there are no getCHRGD products yet, so every entry carries an **empty
`our_product` field on purpose**. When there's a range, wiring it in is filling
that field — `brief_block` already knows what to do with it — rather than
re-architecting the show.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

INGREDIENTS_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "ingredients.toml"
)

#: Route key the chosen ingredient rides on.
ROUTE_KEY = "ingredient"


class Ingredient(BaseModel):
    key: str
    label: str
    question: str = ""
    evidence: str = ""
    myth: str = ""
    industry: str = ""
    #: D7 — empty until there's a product. The hook, designed in and unused.
    our_product: str = ""

    def brief_block(self) -> str:
        """The ingredient, as the brief STRAIGHT UP's build is written from."""
        lines = [f"THE SUBJECT: {self.label}."]
        if self.question:
            lines.append(
                f"THE QUESTION people actually ask, and the one to open on: "
                f"{self.question}"
            )
        if self.evidence:
            lines.append(
                f"WHAT THE EVIDENCE SUPPORTS (report this honestly, including "
                f"where it's thin — do not upgrade a hedge into a claim): "
                f"{self.evidence}"
            )
        if self.myth:
            lines.append(f"THE MYTH to name and correct: {self.myth}")
        if self.industry:
            lines.append(
                f"WHERE THE SHOW IS ALLOWED TO BE SHARP — this is about the "
                f"INDUSTRY, not physiology, so take a real side: {self.industry}"
            )
        if self.our_product.strip():
            lines.append(
                f"OUR PRODUCT, mentioned once and only where it is honestly "
                f"the answer: {self.our_product.strip()}"
            )
        else:
            lines.append(
                "THIS BRAND HAS NO PRODUCT IN THIS CATEGORY: keep the post "
                "category education. Never imply a product recommendation that "
                "does not exist."
            )
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load_ingredients(path: Path = INGREDIENTS_FILE) -> dict[str, Ingredient]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        key: Ingredient(key=key, **spec)
        for key, spec in (data.get("ingredients") or {}).items()
    }


def get_ingredient(key: str) -> Ingredient | None:
    if not key:
        return None
    return load_ingredients().get(key)


def covered(store) -> set[str]:
    """Ingredient keys already used by a post — a nudge, not an exclusion."""
    import json

    rows = store.recent_routes(f'"{ROUTE_KEY}"')
    out: set[str] = set()
    for row in rows:
        try:
            route = json.loads(row["route_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        key = str(route.get(ROUTE_KEY) or "")
        if key:
            out.add(key)
    return out
