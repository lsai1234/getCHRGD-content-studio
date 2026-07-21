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


def test_short_slide_gets_embedded_typography():
    from chrgd.images import compose_design_prompt

    brand = load_brand()
    slide = Slide(headline="how many scoops?", supporting="confess below")
    prompt = compose_design_prompt(slide, brand, None)
    # Words become part of the scene, not a caption bar…
    assert "TANGIBLE PART OF THE SCENE" in prompt
    assert "TYPOGRAPHY TREATMENT:" in prompt
    # …and the clean caption-placement rules are NOT used.
    assert "Place the headline in the upper third" not in prompt
    # Legibility + exact-copy guarantees still hold.
    assert "Use EXACTLY the text provided above" in prompt
    assert "margin" in prompt.lower() and "off any edge" in prompt.lower()


def test_reader_slide_stays_clean():
    from chrgd.images import compose_design_prompt

    brand = load_brand()
    slide = Slide(
        headline="the real cost",
        supporting="what you're paying for",
        body="A £4.50 energy drink is mostly water, sugar and caffeine. "
        "The caffeine costs pennies. You're paying for the can and the brand.",
    )
    prompt = compose_design_prompt(slide, brand, None)
    assert "This is a READER slide" in prompt
    assert "Place the headline in the upper third" in prompt  # clean rules
    assert "TANGIBLE PART OF THE SCENE" not in prompt


def test_long_copy_stays_clean():
    from chrgd.images import _typography_mode

    brand = load_brand()
    short = Slide(headline="one rep left", supporting="you said that 4 sets ago")
    long = Slide(
        headline="the five lads every single gym has and you are definitely one",
        supporting="be honest with yourself before you tag the other four below",
    )
    assert _typography_mode(short, brand) == "embedded"
    assert _typography_mode(long, brand) == "clean"


def test_embedded_typography_can_be_disabled(monkeypatch):
    from chrgd.images import _typography_mode

    brand = load_brand()
    slide = Slide(headline="short", supporting="copy")
    assert _typography_mode(slide, brand) == "embedded"
    monkeypatch.setattr(brand.typography, "embed_when_appropriate", False)
    assert _typography_mode(slide, brand) == "clean"


def test_character_portrait_is_identity_not_a_pose():
    from chrgd.images import compose_design_prompt

    brand = load_brand()
    slide = Slide(headline="which one are you?", supporting="tag the other four")
    prompt = compose_design_prompt(slide, brand, None, character_ref=True)
    low = prompt.lower()
    # Same person (identity locked)…
    assert "same person" in low
    assert "identity reference" in low
    # …but the pose/angle is the scene's call, not the portrait pasted back in.
    assert "not a pose to copy" in low
    assert "never the same head pasted in again" in low


def test_slide_features_character_honours_explicit_flag_then_heuristic():
    from chrgd.images import slide_features_character

    # Explicit engine call always wins, even against the cue scan.
    assert slide_features_character(
        Slide(headline="h", image_prompt="an empty barbell", feature_character=True)
    ) is True
    assert slide_features_character(
        Slide(headline="h", image_prompt="a lifter mid-rep", feature_character=False)
    ) is False
    # Fallback scan when the engine didn't decide: person cue vs object-only.
    assert slide_features_character(
        Slide(headline="h", image_prompt="a gym bloke reacting to the scale")
    ) is True
    assert slide_features_character(
        Slide(headline="h", image_prompt="a chalk-dusted barbell on the floor",
              visual_intent="object, prop")
    ) is False


def test_person_free_slide_told_not_to_add_character():
    from chrgd.images import compose_design_prompt

    brand = load_brand()
    # Slide 1 (no look anchor) of an account that HAS a recurring character,
    # but this beat is a pure object shot: the model must be told to leave the
    # person out rather than pasting them in from habit.
    prompt = compose_design_prompt(
        Slide(headline="the receipt", image_prompt="a crumpled gym membership bill"),
        brand, None, has_character=True, feature_character=False,
    )
    low = prompt.lower()
    assert "does not call for a person" in low
    assert "do not place them or any human figure" in low


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
    # Slide 1 is the scroll-stopper and gets the best quality; slides 2+ stay
    # low to keep spend down.
    assert brand.generation.quality_for(0) == brand.generation.quality_first
    assert brand.generation.quality_for(0) == "high"
    assert brand.generation.quality_for(1) == "low"
    assert brand.generation.quality_for(4) == "low"


def test_non_dry_run_cost_first_medium_rest_low(settings, monkeypatch):
    from chrgd import images

    settings.openai_api_key = "sk-test"
    monkeypatch.setattr(
        images, "_generate_background", lambda *a, **k: Image.new("RGB", (100, 150))
    )
    result = images.render_carousel(make_idea(), settings, dry_run=False)
    gen = load_brand().generation
    n_first = gen.variants_first
    expected = (
        n_first * images._IMAGE_COST[gen.quality_first]
        + 4 * images._IMAGE_COST[gen.quality_rest]
    )
    assert result.generated == 4 + n_first
    assert result.spend_usd == pytest.approx(expected)
    if n_first > 1:
        assert len(result.variants.get(0, [])) == n_first
    else:
        assert result.variants == {}  # one image per slide, no variant files


def test_webp_format(tmp_path):
    settings = Settings(CHRGD_OUTPUT_DIR=tmp_path / "out")
    # Copy — load_brand() is cached and mutating it would poison other tests.
    brand = load_brand().model_copy(deep=True)
    brand.canvas.format = "webp"
    result = render_carousel(make_idea(), settings, brand=brand, dry_run=True)
    assert result.paths[0].endswith(".webp")
    assert Image.open(result.paths[0]).format == "WEBP"
