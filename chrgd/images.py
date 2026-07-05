"""Carousel image builder (milestone 3).

Beats the old system by never asking the image model to render text:

  1. Generate a *background* per slide from the slide's image prompt via the
     image API (OpenAI `gpt-image-1` by default).
  2. Overlay the approved headline + supporting text in code with Pillow,
     inside the TikTok-safe zones, with a contrast panel on busy backgrounds.

Output is JPEG/WebP (never PNG — TikTok rejects it), vertical, <=1080p, one
image per slide (5 for Sketch posts, up to 10 for Playbooks), saved to
`output/<idea_id>/slide_1..N.<ext>`.

`--dry-run` skips the paid image API and paints a branded placeholder
background instead, so layout/typography can be tested offline for free.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .brand import Brand, load_brand
from .config import Settings
from .models import Idea, Slide

# Rough USD cost per generated image, by gpt-image-1 quality. Estimate only,
# for the spend log / cost guard.
_IMAGE_COST = {"low": 0.011, "medium": 0.042, "high": 0.167, "auto": 0.042}


class ImageError(RuntimeError):
    pass


@dataclass
class RenderResult:
    idea_id: str
    paths: list[str] = field(default_factory=list)
    spend_usd: float = 0.0
    generated: int = 0  # backgrounds actually generated via the paid API
    dry_run: bool = False


# --- helpers ----------------------------------------------------------------


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:  # pragma: no cover - guarded by brand.resolve_font
        raise ImageError(f"could not load font {path}: {exc}") from exc


def _fit_to_canvas(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize + centre-crop `img` to exactly width×height (cover)."""
    img = img.convert("RGB")
    scale = max(width / img.width, height / img.height)
    resized = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _placeholder_background(brand: Brand, seed: int) -> Image.Image:
    """A branded dark gradient with a subtle accent glow (dry-run / fallback)."""
    w, h = brand.canvas.width, brand.canvas.height
    base = _hex(brand.colors.background)
    accent = _hex(brand.colors.accent if seed % 2 == 0 else brand.colors.accent_alt)
    img = Image.new("RGB", (w, h), base)
    px = img.load()
    # Vertical gradient darkening toward the bottom, with a faint accent tint
    # drifting across slides so they aren't identical.
    for y in range(h):
        t = y / h
        for x in range(0, w, 4):  # step for speed; fill 4px blocks
            gx = x / w
            r = int(base[0] + (accent[0] - base[0]) * 0.10 * (1 - t) * gx)
            g = int(base[1] + (accent[1] - base[1]) * 0.10 * (1 - t) * gx)
            b = int(base[2] + (accent[2] - base[2]) * 0.10 * (1 - t) * gx)
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = (r, g, b)
    return img


def _generate_background(
    prompt: str, settings: Settings, brand: Brand, quality: str
) -> Image.Image:
    """Call the image API for one background. Raises ImageError on failure."""
    key = settings.get_image_key()
    if not key:
        raise ImageError(
            "no image API key — set CHRGD_IMAGE_API_KEY or OPENAI_API_KEY"
        )
    if settings.image_provider != "openai":
        raise ImageError(
            f"image provider '{settings.image_provider}' not supported yet; "
            "use openai or run with --dry-run"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise ImageError("openai not installed. Run: pip install -e '.[llm]'") from exc

    kwargs = {"api_key": key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    client = OpenAI(**kwargs)
    try:
        resp = client.images.generate(
            model=settings.image_model,
            prompt=prompt,
            size=brand.generation.size,
            quality=quality,
            n=1,
        )
        b64 = resp.data[0].b64_json
    except Exception as exc:  # noqa: BLE001
        raise ImageError(str(exc)) from exc
    return Image.open(BytesIO(base64.b64decode(b64)))


# --- text overlay -----------------------------------------------------------


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "…"
    return lines


def _text_block_height(
    lines: list[str], font: ImageFont.FreeTypeFont, line_spacing: float
) -> int:
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * line_spacing)
    return line_h * len(lines)


def compose_slide(
    background: Image.Image,
    slide: Slide,
    brand: Brand,
    slide_index: int,
) -> Image.Image:
    """Overlay approved headline + supporting text onto a background."""
    w, h = brand.canvas.width, brand.canvas.height
    canvas = _fit_to_canvas(background, w, h)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    x0, y0, x1, y1 = brand.safe_box()
    max_width = x1 - x0

    # Slide 1 leads with the strongest visual/text — bump the headline size.
    head_size = brand.fonts.headline_size
    if slide_index == 0:
        head_size = int(head_size * 1.12)
    head_font = _load_font(brand.resolve_font("headline"), head_size)
    supp_font = _load_font(brand.resolve_font("supporting"), brand.fonts.supporting_size)

    head_lines = _wrap(
        draw, slide.headline, head_font, max_width, brand.text.headline_max_lines
    )
    supp_lines = _wrap(draw, slide.supporting, supp_font, max_width) if slide.supporting else []

    ls = brand.fonts.line_spacing
    head_h = _text_block_height(head_lines, head_font, ls)
    gap = int(brand.fonts.supporting_size * 0.8) if supp_lines else 0
    supp_h = _text_block_height(supp_lines, supp_font, ls)
    bar_h = 8 if brand.text.accent_bar else 0
    bar_gap = 18 if brand.text.accent_bar else 0
    block_h = head_h + bar_gap + bar_h + gap + supp_h

    # Headline sits in the upper-middle of the safe zone.
    safe_h = y1 - y0
    block_top = y0 + int(safe_h * 0.10)
    block_top = min(block_top, y1 - block_h)  # never spill past the safe bottom
    block_top = max(block_top, y0)

    # Contrast panel behind the whole text block.
    if brand.colors.panel_opacity > 0:
        pad = 28
        panel_rgb = _hex(brand.colors.panel)
        alpha = int(255 * brand.colors.panel_opacity)
        draw.rounded_rectangle(
            [x0 - pad, block_top - pad, x0 + max_width + pad, block_top + block_h + pad],
            radius=28,
            fill=(*panel_rgb, alpha),
        )

    y = block_top
    head_color = _hex(brand.colors.headline)
    ascent, descent = head_font.getmetrics()
    head_line_h = int((ascent + descent) * ls)
    for line in head_lines:
        draw.text((x0, y), line, font=head_font, fill=(*head_color, 255))
        y += head_line_h

    if brand.text.accent_bar:
        y += bar_gap
        draw.rounded_rectangle(
            [x0, y, x0 + int(max_width * 0.28), y + bar_h],
            radius=bar_h // 2,
            fill=(*_hex(brand.colors.accent), 255),
        )
        y += bar_h

    if supp_lines:
        y += gap
        supp_color = _hex(brand.colors.supporting)
        a2, d2 = supp_font.getmetrics()
        supp_line_h = int((a2 + d2) * ls)
        for line in supp_lines:
            draw.text((x0, y), line, font=supp_font, fill=(*supp_color, 255))
            y += supp_line_h

    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


# --- public API -------------------------------------------------------------


def _slides_from_idea(idea: Idea) -> list[Slide]:
    if not idea.slides_json:
        raise ImageError(
            f"{idea.idea_id} has no built slides — run `chrgd build` first"
        )
    return [Slide.model_validate(s) for s in json.loads(idea.slides_json)]


def render_carousel(
    idea: Idea,
    settings: Settings,
    *,
    brand: Brand | None = None,
    dry_run: bool = False,
) -> RenderResult:
    """Render every slide for one idea to disk. Returns paths + spend."""
    brand = brand or load_brand()
    slides = _slides_from_idea(idea)
    out_dir = Path(settings.output_dir) / idea.idea_id
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = "webp" if brand.canvas.format.lower() == "webp" else "jpg"
    pil_format = "WEBP" if ext == "webp" else "JPEG"
    result = RenderResult(idea_id=idea.idea_id, dry_run=dry_run)

    for i, slide in enumerate(slides):
        if dry_run:
            background = _placeholder_background(brand, i)
        else:
            quality = brand.generation.quality_for(i)  # slide 1 high, rest medium
            per_image = _IMAGE_COST.get(quality, 0.042)
            if result.spend_usd + per_image > settings.max_spend_per_run:
                raise ImageError(
                    f"spend cap £{settings.max_spend_per_run:g} would be exceeded "
                    f"at slide {i + 1} — aborting render"
                )
            background = _generate_background(
                slide.image_prompt, settings, brand, quality
            )
            result.spend_usd += per_image
            result.generated += 1

        composed = compose_slide(background, slide, brand, i)
        path = out_dir / f"slide_{i + 1}.{ext}"
        save_kwargs = {"quality": brand.canvas.save_quality}
        composed.save(path, pil_format, **save_kwargs)
        result.paths.append(str(path))

    return result
