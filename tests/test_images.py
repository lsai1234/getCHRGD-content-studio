"""Tests for milestone 3: the carousel image builder (offline / dry-run)."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from chrgd.brand import load_brand
from chrgd.config import Settings
from chrgd.images import ImageError, compose_slide, render_carousel
from chrgd.models import Idea, Slide


def make_idea(idea_id="G-0001"):
    slides = [
        {
            "headline": f"Slide {i} headline that is deliberately quite long to wrap",
            "supporting": "A supporting line underneath the headline.",
            "image_prompt": "a moody UK gym scene",
            "visual_intent": "intent",
        }
        for i in range(5)
    ]
    return Idea(idea_id=idea_id, concept_note="x", slides_json=json.dumps(slides))


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_OUTPUT_DIR=tmp_path / "out", CHRGD_DB_PATH=tmp_path / "t.db")


def test_render_dry_run_writes_five_jpegs(settings):
    idea = make_idea()
    result = render_carousel(idea, settings, dry_run=True)
    assert len(result.paths) == 5
    assert result.generated == 0
    assert result.spend_usd == 0.0
    for p in result.paths:
        assert p.endswith(".jpg")
        img = Image.open(p)
        assert img.format == "JPEG"


def test_rendered_slides_match_canvas_size(settings):
    brand = load_brand()
    result = render_carousel(make_idea(), settings, dry_run=True)
    img = Image.open(result.paths[0])
    assert img.size == (brand.canvas.width, brand.canvas.height)


def test_compose_slide_returns_rgb_canvas():
    brand = load_brand()
    bg = Image.new("RGB", (400, 400), (10, 10, 10))
    slide = Slide(headline="Hook line", supporting="under text")
    out = compose_slide(bg, slide, brand, 0)
    assert out.mode == "RGB"
    assert out.size == (brand.canvas.width, brand.canvas.height)


def test_render_without_slides_errors(settings):
    idea = Idea(idea_id="G-0002", concept_note="x")  # no slides_json
    with pytest.raises(ImageError):
        render_carousel(idea, settings, dry_run=True)


def test_non_dry_run_without_key_errors(settings):
    settings.openai_api_key = None
    settings.image_api_key = None
    with pytest.raises(ImageError):
        render_carousel(make_idea(), settings, dry_run=False)


def test_per_slide_quality_selection():
    brand = load_brand()
    assert brand.generation.quality_for(0) == "high"   # slide 1
    assert brand.generation.quality_for(1) == "medium"  # slides 2-5
    assert brand.generation.quality_for(4) == "medium"


def test_non_dry_run_cost_uses_high_then_medium(settings, monkeypatch):
    from chrgd import images

    settings.openai_api_key = "sk-test"
    monkeypatch.setattr(
        images, "_generate_background", lambda *a, **k: Image.new("RGB", (100, 150))
    )
    result = images.render_carousel(make_idea(), settings, dry_run=False)
    expected = images._IMAGE_COST["high"] + 4 * images._IMAGE_COST["medium"]
    assert result.generated == 5
    assert result.spend_usd == pytest.approx(expected)


def test_webp_format(tmp_path):
    settings = Settings(CHRGD_OUTPUT_DIR=tmp_path / "out")
    brand = load_brand()
    brand.canvas.format = "webp"
    result = render_carousel(make_idea(), settings, brand=brand, dry_run=True)
    assert result.paths[0].endswith(".webp")
    assert Image.open(result.paths[0]).format == "WEBP"
