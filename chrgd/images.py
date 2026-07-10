"""Carousel image builder (milestone 3).

Beats the old system by never asking the image model to render text:

  1. Generate a *background* per slide from the slide's image prompt via the
     image API (OpenAI `gpt-image-1` by default).
  2. Overlay the approved headline + supporting text in code with Pillow,
     inside the TikTok-safe zones, with a contrast panel on busy backgrounds.

Output is JPEG/WebP (never PNG — TikTok rejects it), vertical, <=1080p, all
images per carousel, saved to `output/<idea_id>/slide_1..5.<ext>`.

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
from .models import MAX_SLIDES, Idea, Slide

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
    # slide index (0-based) -> composed variant paths (slide 1 options).
    variants: dict[int, list[str]] = field(default_factory=dict)


@dataclass
class SlideRenderResult:
    idea_id: str
    slide_index: int
    path: str = ""  # canonical composed slide
    variant_paths: list[str] = field(default_factory=list)
    spend_usd: float = 0.0
    generated: int = 0
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

    # Explicit base_url: see Settings.get_openai_base_url.
    client = OpenAI(api_key=key, base_url=settings.get_openai_base_url())
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
        from .pipeline import _describe_llm_error

        raise ImageError(_describe_llm_error(exc)) from exc
    return Image.open(BytesIO(base64.b64decode(b64)))


# --- prompt composition -------------------------------------------------------

# Text-placement rules for AI-designed slides. Ported from the proven n8n
# pipeline's image call: the model designs the whole slide, typography
# included, but must use exactly the approved copy.
_DESIGN_TEXT_RULES = (
    "\n\nText & layout rules (follow EXACTLY):"
    "\n- The image is a vertical 2:3 phone graphic. Treat the outer 12% on"
    " every side as an untouchable margin: NO text may touch, overlap or run"
    " off any edge. Every letter of every word must sit fully inside the frame"
    " with clear breathing room around it."
    "\n- Place the headline in the upper third but BELOW the top margin — leave"
    " clear empty space above the first line. Never let the title bleed off the"
    " top. If the headline is long, reduce the font size so the whole thing"
    " fits on 2-3 lines well inside the margins rather than cropping it."
    "\n- Place the supporting text below the headline in smaller but readable"
    " type, also inside the margins."
    "\n- Use EXACTLY the text provided above, spelled correctly, complete —"
    " never truncate, abbreviate or cut off a word."
    "\n- Do not add any extra words, labels, logos, captions, watermarks,"
    " numeric page numbers or random text. The ONLY non-copy graphic allowed"
    " is the small progress-dot indicator described above (dots, not numbers)."
    "\n- Make the text large, high-contrast and readable at a glance on a phone."
)


def compose_image_prompt(slide_prompt: str, brand: Brand, style: str | None) -> str:
    """Background-only prompt (branded/overlay mode): a strong photo with room
    for the CHRGD frame. Text + brand furniture are drawn in code, so the model
    must never paint its own text."""
    parts = []
    style_block = brand.style_prompt(style)
    if style_block:
        parts.append(style_block)
    if slide_prompt.strip():
        parts.append(slide_prompt.strip())
    # Composition: keep the subject clear of the centre band and leave the top
    # and bottom edges cleaner/darker so the overlaid headline + wordmark read.
    parts.append(
        "Composition: one bold, unexpected subject with strong depth; place it "
        "off-centre (lower or to one side) leaving generous clean, darker "
        "negative space across the upper-middle and bottom for text overlay; "
        "cinematic contrast, not a flat busy scene."
    )
    if brand.generation.consistency_clause:
        parts.append(brand.generation.consistency_clause)
    if brand.generation.negative_clause:
        parts.append(brand.generation.negative_clause)
    return " ".join(parts)


_DESIGN_SYSTEM_KEYS = (
    ("palette", "Colour palette"),
    ("type_style", "Typography"),
    ("motif", "Recurring motif / character"),
    ("layout", "Layout grid"),
)


def _design_system_block(design_system: dict | None) -> str:
    """The shared visual language, restated on every slide's prompt.

    gpt-image-2 generates each slide blind to the others, so the only way the
    set reads as one designed sequence (rather than five unrelated posters) is
    to describe the shared world in words on every single frame.
    """
    ds = design_system or {}
    lines = [f"- {label}: {ds[key]}" for key, label in _DESIGN_SYSTEM_KEYS if ds.get(key)]
    if not lines:
        return ""
    return (
        "SHARED CAROUSEL DESIGN SYSTEM — IDENTICAL on every slide in this set so "
        "the whole carousel reads as ONE cohesive piece, not separate posts. "
        "Reproduce this exact visual language here:\n" + "\n".join(lines)
    )


def _sequence_block(
    slide: Slide,
    slides: list[Slide] | None,
    index: int,
    design_system: dict | None,
) -> str:
    """Where this frame sits in the swipe journey + how it continues/evolves."""
    if not slides or len(slides) <= 1:
        return ""
    total = len(slides)
    ds = design_system or {}
    out = [f"SEQUENCE & CONTINUITY: this is frame {index + 1} of {total}."]
    if slide.role:
        out.append(f"Its role in the story is: {slide.role}.")
    if index == 0:
        out.append(
            "This is the OPENING frame — it establishes the world, palette, "
            "type treatment and recurring motif that every following slide will "
            "continue exactly."
        )
    else:
        prev = slides[index - 1]
        prev_ref = (prev.visual_intent or prev.headline or "the previous frame").strip()
        out.append(
            f"The previous frame showed: {prev_ref}. Continue the SAME world, the "
            "SAME recurring character/motif, the SAME palette and type treatment — "
            "this must look like the very next frame of that sequence, never a new "
            "design."
        )
    if ds.get("evolution"):
        out.append(
            "Show visible motion from the last frame — how the design escalates as "
            f"the story builds: {ds['evolution']}"
        )
    if index + 1 < total:
        out.append(
            "Leave clear visual momentum pulling the viewer to swipe to the next "
            "frame; do not resolve everything here."
        )
    out.append(
        f"Include a small, consistent progress indicator in the same corner on "
        f"every slide — a discreet row of {total} dots with dot {index + 1} "
        "highlighted — so the viewer feels their place in the journey. Keep it "
        "tiny and tasteful; it is the only extra graphic element allowed."
    )
    return " ".join(out)


def compose_design_prompt(
    slide: Slide,
    brand: Brand,
    style: str | None,
    *,
    slides: list[Slide] | None = None,
    index: int = 0,
    design_system: dict | None = None,
) -> str:
    """Full-slide design prompt (ai_design mode): the model designs the whole
    piece — concept, layout, and the approved copy rendered as typography.

    When `slides` + `design_system` are supplied the prompt also carries the
    carousel's shared visual language and this frame's place in the swipe
    journey, so the set reads as one continuous story rather than isolated
    slides (each image is generated blind to its siblings)."""
    parts = []
    if slide.image_prompt.strip():
        parts.append(slide.image_prompt.strip())
    style_block = brand.style_prompt(style)
    if style_block:
        parts.append(f"Art direction: {style_block}")
    ds_block = _design_system_block(design_system)
    if ds_block:
        parts.append(ds_block)
    seq_block = _sequence_block(slide, slides, index, design_system)
    if seq_block:
        parts.append(seq_block)
    # Fall back to the generic clause only when there's no real design system
    # to carry continuity (e.g. legacy posts built before the spine existed).
    if not ds_block and brand.generation.consistency_clause:
        parts.append(brand.generation.consistency_clause)
    prompt = "\n\n".join(parts)
    prompt += "\n\nTEXT TO PLACE ON IMAGE:"
    prompt += f"\nHeadline text: {slide.headline}"
    if slide.supporting:
        prompt += f"\nSupporting text: {slide.supporting}"
    prompt += _DESIGN_TEXT_RULES
    return prompt


def _route_of(idea: Idea) -> dict:
    if not idea.route_json:
        return {}
    try:
        return json.loads(idea.route_json)
    except json.JSONDecodeError:
        return {}


def style_for_idea(idea: Idea) -> str | None:
    """The style preset chosen for this idea (stored in route_json)."""
    return _route_of(idea).get("style")


# Modes that draw text (and the brand frame) in code over a photo background.
def render_mode_for_idea(idea: Idea, brand: Brand) -> str:
    """Always 'ai_design'.

    Every slide — including all its text — is generated by the image API and
    baked into the artwork. Code-overlaid text ('branded'/'overlay') is never
    used for a real render; the brand requires API-generated typography on
    every image. Kept as a function so callers/tests need no change.
    """
    return "ai_design"


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


def _draw_scrim(base: Image.Image, brand: Brand) -> Image.Image:
    """Darken the top and bottom edges so the brand furniture + text always
    read cleanly over any photo. Returns a new RGB image."""
    w, h = base.size
    scrim = Image.new("L", (w, h), 0)
    px = scrim.load()
    top_end = int(h * 0.16)
    bot_start = int(h * 0.80)
    for y in range(h):
        a = 0
        if y < top_end:
            a = int(150 * (1 - y / top_end))
        elif y > bot_start:
            a = int(190 * (y - bot_start) / (h - bot_start))
        if a:
            for x in range(0, w, 3):
                px[x, y] = a
                if x + 1 < w:
                    px[x + 1, y] = a
                if x + 2 < w:
                    px[x + 2, y] = a
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(black, base.convert("RGB"), scrim)


def _draw_brand_furniture(
    draw: ImageDraw.ImageDraw, brand: Brand, canvas: Image.Image,
    slide_index: int, total: int | None,
) -> int:
    """Draw the fixed CHRGD frame (wordmark, counter, handle, footer bar).

    Returns the y offset below the wordmark where slide text should start.
    """
    ident = brand.identity
    x0, y0, x1, y1 = brand.safe_box()
    accent = _hex(brand.colors.accent)
    head_color = _hex(brand.colors.headline)
    muted = _hex(brand.colors.supporting)

    word_size = int(brand.fonts.supporting_size * 1.4)
    word_font = _load_font(brand.resolve_font("headline"), word_size)
    small_font = _load_font(brand.resolve_font("supporting"), int(brand.fonts.supporting_size * 0.85))

    top_after = y0
    # Logo image wins over the text wordmark when present.
    logo = ident.logo_path
    if logo and Path(logo).exists():
        try:
            lg = Image.open(logo).convert("RGBA")
            target_h = word_size + 6
            lg = lg.resize((max(1, round(lg.width * target_h / lg.height)), target_h))
            canvas.paste(lg, (x0, y0), lg)
            top_after = y0 + target_h
        except OSError:
            logo = ""
    if not (logo and Path(logo).exists()) and ident.wordmark:
        # Two-tone wordmark: first `split` chars in accent, rest in headline.
        mark = ident.wordmark
        split = max(0, min(ident.wordmark_split, len(mark)))
        x = x0
        for i, ch in enumerate(mark):
            col = accent if i < split else head_color
            draw.text((x, y0), ch, font=word_font, fill=(*col, 255))
            x += int(draw.textlength(ch, font=word_font))
        top_after = y0 + word_size + 4

    # Slide counter, top-right.
    if ident.show_counter and total:
        label = f"{slide_index + 1}/{total}"
        tw = draw.textlength(label, font=small_font)
        draw.text((x1 - tw, y0 + 6), label, font=small_font, fill=(*muted, 235))

    # Handle + footer accent bar, bottom-left of the safe zone.
    a2, d2 = small_font.getmetrics()
    handle_h = a2 + d2
    if ident.footer_bar:
        by = y1 - handle_h - 14
        draw.rounded_rectangle([x0, by, x0 + int((x1 - x0) * 0.16), by + 5], radius=2,
                               fill=(*accent, 255))
    if ident.handle:
        draw.text((x0, y1 - handle_h), ident.handle, font=small_font, fill=(*muted, 235))

    return top_after


def compose_slide(
    background: Image.Image,
    slide: Slide,
    brand: Brand,
    slide_index: int,
    total: int | None = None,
) -> Image.Image:
    """Compose the branded slide: photo + fixed CHRGD frame + approved text."""
    w, h = brand.canvas.width, brand.canvas.height
    canvas = _fit_to_canvas(background, w, h)
    if brand.identity.scrim:
        canvas = _draw_scrim(canvas, brand)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    x0, y0, x1, y1 = brand.safe_box()
    max_width = x1 - x0

    # Brand furniture first — it also tells us where the text may start.
    brand_top = _draw_brand_furniture(draw, brand, canvas, slide_index, total)

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

    # Headline sits below the wordmark, in the upper-middle of the safe zone.
    safe_h = y1 - y0
    text_top = brand_top + int(safe_h * 0.06)
    block_top = min(text_top, y1 - block_h)  # never spill past the safe bottom
    block_top = max(block_top, brand_top + 8)

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

_VARIANT_KEYS = "abcdefgh"


def _slides_from_idea(idea: Idea) -> list[Slide]:
    if not idea.slides_json:
        raise ImageError(
            f"{idea.idea_id} has no built slides — run `chrgd build` first"
        )
    return [Slide.model_validate(s) for s in json.loads(idea.slides_json)]


def _ext(brand: Brand) -> tuple[str, str]:
    ext = "webp" if brand.canvas.format.lower() == "webp" else "jpg"
    return ext, ("WEBP" if ext == "webp" else "JPEG")


def _out_dir(settings: Settings, idea_id: str) -> Path:
    out = Path(settings.output_dir) / idea_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _bg_path(out_dir: Path, index: int, ext: str, variant: str | None = None) -> Path:
    suffix = f"_{variant}" if variant else ""
    return out_dir / f"bg_{index + 1}{suffix}.{ext}"


def _slide_path(out_dir: Path, index: int, ext: str, variant: str | None = None) -> Path:
    suffix = f"_{variant}" if variant else ""
    return out_dir / f"slide_{index + 1}{suffix}.{ext}"


def _save(img: Image.Image, path: Path, pil_format: str, brand: Brand) -> None:
    img.save(path, pil_format, quality=brand.canvas.save_quality)


def render_slide(
    idea: Idea,
    slide_index: int,
    settings: Settings,
    *,
    brand: Brand | None = None,
    dry_run: bool = False,
    variants: int | None = None,
    spent_so_far: float = 0.0,
    notify=None,
) -> SlideRenderResult:
    """Render one slide: N background variants + composed text overlay.

    Two modes (per-post via route_json.render_mode, default from brand.toml):

    * ``ai_design`` — the model designs the WHOLE slide, typography included
      (the proven n8n approach). The design is saved as both `slide_N` and
      `bg_N` so variant-picking works identically; there is no free text
      re-lay in this mode.
    * ``overlay`` — the model paints a background; the approved copy is
      overlaid in code. The raw background is kept on disk so copy edits can
      re-overlay text for free.

    Variant files get an `_a`/`_b` suffix; the first variant also becomes the
    canonical `slide_N` / `bg_N` until a different one is picked.
    """
    brand = brand or load_brand()
    slides = _slides_from_idea(idea)
    if not 0 <= slide_index < len(slides):
        raise ImageError(f"slide {slide_index + 1} out of range for {idea.idea_id}")
    slide = slides[slide_index]
    out_dir = _out_dir(settings, idea.idea_id)
    ext, pil_format = _ext(brand)
    style = style_for_idea(idea)
    mode = render_mode_for_idea(idea, brand)

    n = variants if variants is not None else brand.generation.variants_for(slide_index)
    n = max(1, min(n, len(_VARIANT_KEYS)))
    quality = brand.generation.quality_for(slide_index)
    per_image = _IMAGE_COST.get(quality, 0.042)

    def _note(msg: str) -> None:
        if notify:
            notify(f"slide {slide_index + 1}: {msg}")

    result = SlideRenderResult(
        idea_id=idea.idea_id, slide_index=slide_index, dry_run=dry_run
    )
    for v in range(n):
        if dry_run:
            _note("test mode — painting a placeholder")
            background = _placeholder_background(brand, slide_index + v)
        else:
            if spent_so_far + result.spend_usd + per_image > settings.max_spend_per_run:
                raise ImageError(
                    f"spend cap £{settings.max_spend_per_run:g} would be exceeded "
                    f"at slide {slide_index + 1} — aborting render"
                )
            if mode == "ai_design":
                prompt = compose_design_prompt(
                    slide,
                    brand,
                    style,
                    slides=slides,
                    index=slide_index,
                    design_system=_route_of(idea).get("design_system"),
                )
            else:
                prompt = compose_image_prompt(slide.image_prompt, brand, style)
            _note(
                f"request sent to {settings.image_model} ({quality} quality) — "
                "waiting for the image, typically 20-60s"
            )
            background = _generate_background(prompt, settings, brand, quality)
            _note("image received — fitting to canvas and saving")
            result.spend_usd += per_image
            result.generated += 1

        fitted = _fit_to_canvas(background, brand.canvas.width, brand.canvas.height)
        if mode == "ai_design" and not dry_run:
            # The model output IS the finished slide — no code overlay.
            composed = fitted
        else:
            # branded/overlay always; ai_design dry-run gets an approximate
            # overlay so test mode still previews the copy + frame on the slide.
            composed = compose_slide(fitted, slide, brand, slide_index, total=len(slides))
        key = _VARIANT_KEYS[v] if n > 1 else None
        _save(fitted, _bg_path(out_dir, slide_index, ext, key), pil_format, brand)
        _save(composed, _slide_path(out_dir, slide_index, ext, key), pil_format, brand)
        if key:
            result.variant_paths.append(str(_slide_path(out_dir, slide_index, ext, key)))
        if v == 0:
            # First variant doubles as the canonical files until a pick.
            if key:
                _save(fitted, _bg_path(out_dir, slide_index, ext), pil_format, brand)
                _save(composed, _slide_path(out_dir, slide_index, ext), pil_format, brand)
            result.path = str(_slide_path(out_dir, slide_index, ext))

    return result


def recompose_slide(
    idea: Idea,
    slide_index: int,
    settings: Settings,
    *,
    brand: Brand | None = None,
) -> SlideRenderResult:
    """Re-overlay the current copy onto the saved background — free, instant.

    Overlay mode only: in ai_design mode the text is part of the artwork, so
    copy changes need a regeneration. Refreshes the canonical slide and any
    variant previews that still have their background on disk.
    """
    brand = brand or load_brand()
    if render_mode_for_idea(idea, brand) == "ai_design":
        raise ImageError(
            "this post uses AI-designed slides — the text is part of the "
            "artwork, so regenerate the slide to apply copy changes"
        )
    slides = _slides_from_idea(idea)
    if not 0 <= slide_index < len(slides):
        raise ImageError(f"slide {slide_index + 1} out of range for {idea.idea_id}")
    slide = slides[slide_index]
    out_dir = _out_dir(settings, idea.idea_id)
    ext, pil_format = _ext(brand)

    canonical_bg = _bg_path(out_dir, slide_index, ext)
    if not canonical_bg.exists():
        raise ImageError(
            f"no saved background for slide {slide_index + 1} — generate it first"
        )

    result = SlideRenderResult(idea_id=idea.idea_id, slide_index=slide_index)
    targets: list[tuple[Path, Path, str | None]] = [(canonical_bg, _slide_path(out_dir, slide_index, ext), None)]
    for key in _VARIANT_KEYS:
        bg = _bg_path(out_dir, slide_index, ext, key)
        if bg.exists():
            targets.append((bg, _slide_path(out_dir, slide_index, ext, key), key))

    for bg, dest, key in targets:
        composed = compose_slide(
            Image.open(bg), slide, brand, slide_index, total=len(slides)
        )
        _save(composed, dest, pil_format, brand)
        if key:
            result.variant_paths.append(str(dest))
        else:
            result.path = str(dest)
    return result


def pick_variant(
    idea: Idea,
    slide_index: int,
    variant: str,
    settings: Settings,
    *,
    brand: Brand | None = None,
) -> str:
    """Promote a background variant to the canonical slide. Returns its path."""
    brand = brand or load_brand()
    out_dir = _out_dir(settings, idea.idea_id)
    ext, _ = _ext(brand)
    if variant not in _VARIANT_KEYS:
        raise ImageError(f"unknown variant '{variant}'")
    bg = _bg_path(out_dir, slide_index, ext, variant)
    composed = _slide_path(out_dir, slide_index, ext, variant)
    if not bg.exists() or not composed.exists():
        raise ImageError(f"variant '{variant}' for slide {slide_index + 1} not found")
    import shutil

    shutil.copyfile(bg, _bg_path(out_dir, slide_index, ext))
    shutil.copyfile(composed, _slide_path(out_dir, slide_index, ext))
    return str(_slide_path(out_dir, slide_index, ext))


def list_variants(idea: Idea, settings: Settings, *, brand: Brand | None = None) -> dict[int, list[str]]:
    """Composed variant filenames on disk, keyed by 0-based slide index."""
    brand = brand or load_brand()
    out_dir = Path(settings.output_dir) / idea.idea_id
    ext, _ = _ext(brand)
    found: dict[int, list[str]] = {}
    if not out_dir.exists():
        return found
    for i in range(MAX_SLIDES):
        names = [
            _slide_path(out_dir, i, ext, key).name
            for key in _VARIANT_KEYS
            if _slide_path(out_dir, i, ext, key).exists()
        ]
        if names:
            found[i] = names
    return found


def render_carousel(
    idea: Idea,
    settings: Settings,
    *,
    brand: Brand | None = None,
    dry_run: bool = False,
    on_slide=None,
    notify=None,
) -> RenderResult:
    """Render every slide for one idea to disk. Returns paths + spend.

    `on_slide(done, total)` fires after each slide for job progress;
    `notify(msg)` streams fine-grained step updates (request sent, waiting,
    image received) for the queue page.
    """
    brand = brand or load_brand()
    slides = _slides_from_idea(idea)
    result = RenderResult(idea_id=idea.idea_id, dry_run=dry_run)

    for i in range(len(slides)):
        slide_result = render_slide(
            idea,
            i,
            settings,
            brand=brand,
            dry_run=dry_run,
            spent_so_far=result.spend_usd,
            notify=notify,
        )
        result.spend_usd += slide_result.spend_usd
        result.generated += slide_result.generated
        result.paths.append(slide_result.path)
        if slide_result.variant_paths:
            result.variants[i] = slide_result.variant_paths
        if on_slide:
            on_slide(i + 1, len(slides))

    return result
