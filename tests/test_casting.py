"""Tests for the casting lever (Bet 1) — profile block + guardrails + validator."""

from __future__ import annotations

from chrgd.profile import (
    BrandProfile,
    casting_block,
    profile_style_block,
    validate_supporting_cast,
)


def test_casting_off_by_default_is_silent():
    p = BrandProfile()
    assert p.casting_intensity == "off"
    assert casting_block(p) == ""
    assert profile_style_block(p) == ""       # a blank profile stays inert
    assert p.is_empty()                        # casting default doesn't count


def test_casting_natural_carries_direction_and_guardrails():
    p = BrandProfile(
        casting_intensity="natural",
        supporting_cast="aspirational UK gym women in their 20s",
    )
    block = casting_block(p)
    assert "CASTING DIRECTION" in block
    assert "aspirational UK gym women" in block           # the operator's words
    assert "attractive and aspirational" in block         # natural tone
    # Guardrails are present.
    assert "ADULT" in block
    assert "gymwear" in block and "glamour" in block.lower()


def test_casting_elevated_pushes_harder_but_keeps_rails():
    block = casting_block(BrandProfile(casting_intensity="elevated"))
    assert "head-turning" in block            # elevated tone
    assert "ADULT" in block and "sexualised" in block  # rails still on


def test_casting_rides_into_style_block_without_other_locks():
    # Casting alone (no palette/character) still reaches the image prompt.
    assert "CASTING DIRECTION" in profile_style_block(
        BrandProfile(casting_intensity="natural")
    )


def test_guardrails_survive_a_pushy_direction():
    # Whatever the operator types, the hard limits are always appended.
    p = BrandProfile(casting_intensity="elevated", supporting_cast="make them really hot")
    block = casting_block(p)
    assert "make them really hot" in block                 # their words kept…
    assert "ADULT" in block and "sexualised" in block       # …rails not stripped


def test_validator_blocks_unsafe_directions():
    assert validate_supporting_cast("attractive adult gym women, fitted kit") is None
    assert validate_supporting_cast("teen girls") == "teen"
    assert validate_supporting_cast("in lingerie") == "lingerie"
    assert validate_supporting_cast("NSFW / explicit models") is not None
    assert validate_supporting_cast("schoolgirl aesthetic") is not None
