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
    # 2:3 to match the generation size (see Generation.size) so AI-designed
    # slides never get cropped onto the canvas.
    height: int = 1620
    format: str = "jpeg"
    save_quality: int = 90


class Generation(BaseModel):
    size: str = "1024x1536"
    # Slide 1 is the whole scroll-stop; give it the best quality. Rest stay low.
    quality_first: str = "high"
    quality_rest: str = "low"
    # Options generated for slide 1 (paid each). One image per slide — the
    # concept gate validates the slide-1 concept before this single image is
    # generated, rather than rolling several and picking.
    variants_first: int = 1
    # How slides are produced. Only "ai_design" is real:
    #   "ai_design" — gpt-image-2 designs the WHOLE slide, every word of text
    #                 baked into the artwork by the image API, never overlaid
    #                 in code. All image text MUST be API-generated.
    #   "branded"/"overlay" — legacy code-overlay modes; accepted as a stored
    #                 hint but ignored (see images.render_mode_for_idea).
    render_mode: str = "ai_design"
    # Fallback continuity clause for legacy posts with no design_system
    # (see images.compose_design_prompt). Count-agnostic on purpose.
    consistency_clause: str = (
        "Part of one carousel set: keep the same location, lighting, colour "
        "grade and photographic style across every image in the set."
    )
    negative_clause: str = (
        "Absolutely no text, no words, no letters, no numbers, no captions, "
        "no watermarks anywhere in the image."
    )
    # Generate slides 2..N with slide 1 attached as a VISUAL reference (image
    # edit), so the character/palette/style carry across as real pixels rather
    # than just words. The single biggest lever on the swipe feeling like one
    # connected piece. Costs a touch more per image; set false to disable.
    reference_continuity: bool = True
    # A small progress-dot row on every slide. OFF by default: to a cold viewer
    # it reads as carousel-ad furniture, and TikTok's own UI already shows the
    # viewer's position in the set. Turn on only for a deliberately "designed"
    # brand look.
    progress_dots: bool = False

    def quality_for(self, slide_index: int) -> str:
        return self.quality_first if slide_index == 0 else self.quality_rest

    def variants_for(self, slide_index: int) -> int:
        return max(1, self.variants_first) if slide_index == 0 else 1


class StylePreset(BaseModel):
    """A named art-direction block appended to every slide prompt."""

    label: str = ""
    prompt: str = ""


class Typography(BaseModel):
    """Controls when the model renders text as part of the scene vs a caption.

    Embedded typography (words spelled in chalk, formed from plates, on a sign
    the character holds) is the scroll-stopping treatment — but only where the
    copy is short enough to stay legible. Longer or reader-heavy slides fall
    back to clean, high-contrast type.
    """

    embed_when_appropriate: bool = True
    embed_max_chars: int = 90
    embed_prompt: str = (
        "Render the words as a tangible part of the scene rather than a flat "
        "caption — a real object in the shot, physically lit and placed, never "
        "a floating overlay."
    )


class SafeZones(BaseModel):
    top: float = 0.10
    right: float = 0.18
    bottom: float = 0.24
    left: float = 0.08


class Colors(BaseModel):
    background: str = "#0B0B0D"
    headline: str = "#FFFFFF"
    supporting: str = "#E6E6E6"
    accent: str = "#4FC3F7"       # CHRGD light blue
    accent_alt: str = "#8FD9F7"
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


class Identity(BaseModel):
    """The fixed CHRGD furniture drawn on every branded slide.

    This is what makes every post unmistakably CHRGD without making them
    identical: a consistent wordmark/logo, handle, slide counter and accent.
    """

    wordmark: str = "CHRGD"          # two-tone text wordmark (see wordmark_split)
    wordmark_split: int = 3          # first N chars use the accent colour
    logo_path: str = ""              # optional PNG; overrides the text wordmark
    handle: str = "@getchrgd"        # bottom-corner handle
    show_counter: bool = True        # "1/5" slide counter
    scrim: bool = True               # top+bottom legibility gradient
    footer_bar: bool = True          # thin accent line along the bottom edge


class Brand(BaseModel):
    canvas: Canvas = Field(default_factory=Canvas)
    generation: Generation = Field(default_factory=Generation)
    safe_zones: SafeZones = Field(default_factory=SafeZones)
    colors: Colors = Field(default_factory=Colors)
    fonts: Fonts = Field(default_factory=Fonts)
    text: TextOpts = Field(default_factory=TextOpts)
    typography: Typography = Field(default_factory=Typography)
    identity: Identity = Field(default_factory=Identity)
    styles: dict[str, StylePreset] = Field(default_factory=dict)

    def style_prompt(self, name: str | None) -> str:
        """The art-direction block for a named preset ('' if unknown/unset)."""
        if name and name in self.styles:
            return self.styles[name].prompt
        return ""

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
