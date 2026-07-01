"""Brand styling config, loaded from `brand.toml`.

Keeps the entire carousel look — canvas size, safe zones, colours, fonts —
in one validated place so the image builder never hardcodes styling.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

BRAND_FILE = Path(__file__).resolve().parent.parent / "brand.toml"

# Reliable fallbacks if a configured font path is missing on the machine.
_FONT_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class Canvas(BaseModel):
    width: int = 1080
    height: int = 1350
    format: str = "jpeg"
    save_quality: int = 90


class Generation(BaseModel):
    size: str = "1024x1536"
    # Slide 1 leads the carousel, so it renders at a stronger quality tier.
    quality_first: str = "high"
    quality_rest: str = "medium"

    def quality_for(self, slide_index: int) -> str:
        return self.quality_first if slide_index == 0 else self.quality_rest


class SafeZones(BaseModel):
    top: float = 0.10
    right: float = 0.18
    bottom: float = 0.24
    left: float = 0.08


class Colors(BaseModel):
    background: str = "#0B0B0D"
    headline: str = "#FFFFFF"
    supporting: str = "#E6E6E6"
    accent: str = "#FF6A00"
    accent_alt: str = "#FFC400"
    panel: str = "#000000"
    panel_opacity: float = 0.55


class Fonts(BaseModel):
    headline: str = _FONT_FALLBACKS[0]
    supporting: str = _FONT_FALLBACKS[1]
    headline_size: int = 74
    supporting_size: int = 38
    line_spacing: float = 1.15


class TextOpts(BaseModel):
    headline_max_lines: int = 4
    accent_bar: bool = True


class Brand(BaseModel):
    canvas: Canvas = Field(default_factory=Canvas)
    generation: Generation = Field(default_factory=Generation)
    safe_zones: SafeZones = Field(default_factory=SafeZones)
    colors: Colors = Field(default_factory=Colors)
    fonts: Fonts = Field(default_factory=Fonts)
    text: TextOpts = Field(default_factory=TextOpts)

    def safe_box(self) -> tuple[int, int, int, int]:
        """Pixel box (x0, y0, x1, y1) that text must stay inside."""
        w, h = self.canvas.width, self.canvas.height
        return (
            int(self.safe_zones.left * w),
            int(self.safe_zones.top * h),
            int((1 - self.safe_zones.right) * w),
            int((1 - self.safe_zones.bottom) * h),
        )

    def resolve_font(self, which: str) -> str:
        """Return an existing font path for 'headline'/'supporting'."""
        configured = getattr(self.fonts, which)
        if configured and Path(configured).exists():
            return configured
        for fallback in _FONT_FALLBACKS:
            if Path(fallback).exists():
                return fallback
        return configured  # let Pillow raise a clear error if truly missing


@lru_cache(maxsize=1)
def load_brand() -> Brand:
    if BRAND_FILE.exists():
        data = tomllib.loads(BRAND_FILE.read_text(encoding="utf-8"))
        return Brand.model_validate(data)
    return Brand()
