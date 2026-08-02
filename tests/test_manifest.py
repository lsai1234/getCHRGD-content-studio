"""The panel manifest, and the cast leakage it exists to stop.

The failure: every character named anywhere in the episode appeared in every
panel, because `_cast_lock_for` returned the whole cast for every slide with
each design flagged as outranking the scene. Any story whose payoff is an
arrival, a reveal or an exit was destroyed — the character the reader was meant
to be surprised by had been standing in frame since panel 1.
"""

from __future__ import annotations

import json

import pytest

from chrgd.images import _cast_lock_for
from chrgd.manifest import (
    ROUTE_KEY,
    Manifest,
    Panel,
    build_manifest,
    cast_block,
    fingerprint,
    manifest_for,
    validate,
)
from chrgd.models import Idea

CAST = ["tracy_beaker", "molly_mae", "clarkson"]


def slide(headline, art="", *, supporting="", feature=True):
    return {
        "headline": headline, "supporting": supporting, "body": "",
        "image_prompt": art, "visual_intent": "",
        "feature_character": feature,
    }


def post(*slides):
    return {"slides": list(slides)}


def idea_with(slides, cast=CAST, *, stored=None):
    route = {"show": "multiverse", "cast": cast}
    if stored is not None:
        route[ROUTE_KEY] = stored
    return Idea(idea_id="G1", concept_note="",
                route_json=json.dumps(route), slides_json=json.dumps(slides))


# --- presence ---------------------------------------------------------------


def test_a_character_is_absent_until_they_are_first_named():
    m = build_manifest(post(
        slide("Tracy Beaker has grease on both hands"),
        slide("Molly-Mae finds the tripod"),
    ), CAST)
    assert m.panels[0].cast_present == ["tracy_beaker"]
    assert set(m.panels[0].cast_forbidden) == {"molly_mae", "clarkson"}
    assert m.panels[1].cast_present == ["tracy_beaker", "molly_mae"]


def test_presence_carries_forward_until_a_departure():
    """Two people in a conversation don't evaporate on the beat their name
    happens not to appear."""
    m = build_manifest(post(
        slide("Tracy Beaker denies it"),
        slide("Molly-Mae takes the credit"),
        slide("Nobody argues"),
        slide("Molly-Mae walks out"),
        slide("The gym is quiet"),
    ), CAST)
    assert m.panels[2].cast_present == ["tracy_beaker", "molly_mae"]
    # present through the beat that says she goes, absent after it
    assert "molly_mae" in m.panels[3].cast_present
    assert "molly_mae" in m.panels[4].cast_forbidden
    assert m.panels[4].cast_present == ["tracy_beaker"]


def test_an_arrival_later_in_the_story_keeps_them_out_of_the_earlier_panels():
    """The reveal-killer. 'Clarkson gets here at six' on panel 1 is the story
    talking ABOUT him; drawing him there spends the surprise before it lands."""
    m = build_manifest(post(
        slide("Tracy Beaker says Jeremy Clarkson will be here at six"),
        slide("Nobody believes her"),
        slide("Jeremy Clarkson walks in with a coffee"),
    ), CAST)
    assert "clarkson" in m.panels[0].cast_forbidden
    assert "clarkson" in m.panels[1].cast_forbidden
    assert "clarkson" in m.panels[2].cast_present


def test_a_character_named_only_in_the_artwork_still_counts():
    m = build_manifest(post(
        slide("The machine works", art="Molly-Mae kneeling by the frame"),
    ), CAST)
    assert m.panels[0].cast_present == ["molly_mae"]


def test_a_person_free_beat_empties_the_frame_without_losing_the_room():
    """The camera looked elsewhere; nobody left."""
    m = build_manifest(post(
        slide("Tracy Beaker denies it"),
        slide("The BACK SOON sign, close up", art="the sign", feature=False),
        slide("She goes back to the machine"),
    ), CAST)
    assert m.panels[1].cast_present == []
    assert set(m.panels[1].cast_forbidden) == set(CAST)
    assert m.panels[2].cast_present == ["tracy_beaker"]


def test_copy_naming_someone_outranks_a_person_free_flag():
    """The other half of the same bug: a caption about Tracy on a panel she
    isn't drawn in."""
    m = build_manifest(post(
        slide("Tracy Beaker denies it", feature=False),
    ), CAST)
    assert m.panels[0].cast_present == ["tracy_beaker"]


def test_a_surname_counts_the_same_as_a_full_name():
    m = build_manifest(post(slide("Clarkson refuses to move")), CAST)
    assert m.panels[0].cast_present == ["clarkson"]


def test_every_character_lands_on_exactly_one_list_for_every_panel():
    m = build_manifest(post(slide("Tracy Beaker denies it"), slide("Molly-Mae films it")), CAST)
    for panel in m.panels:
        assert set(panel.cast_present) | set(panel.cast_forbidden) == set(CAST)
        assert not set(panel.cast_present) & set(panel.cast_forbidden)
        assert panel.total == 2


# --- the prompt: an allowlist alone is not enough ---------------------------


def test_the_panel_prompt_names_every_absent_character():
    """An empty or partial cast list loses to the story context the model
    already has — each absent character has to be refused by name."""
    panel = Panel(index=0, total=2, cast_present=["tracy_beaker"],
                  cast_forbidden=["molly_mae", "clarkson"])
    block = cast_block(panel)
    assert "Tracy Beaker (locked design" in block
    assert "NOT IN THIS PANEL — Molly-Mae, Jeremy Clarkson" in block
    assert "not in a mirror" in block
    # and the ones who aren't here get no design at all
    assert "Molly-Mae (locked design" not in block


def test_an_empty_panel_says_so_rather_than_saying_nothing():
    block = cast_block(Panel(index=0, total=1, cast_forbidden=CAST))
    assert "NOBODY FROM THE CAST IS IN THIS PANEL" in block
    assert "Tracy Beaker" in block  # still refused by name


def test_the_render_asks_the_manifest_who_is_in_this_panel():
    slides = [slide("Tracy Beaker denies it"), slide("Jeremy Clarkson walks in")]
    idea = idea_with(slides)
    first = _cast_lock_for(idea, 0)
    second = _cast_lock_for(idea, 1)
    assert "Jeremy Clarkson" in first          # named, but only to be refused
    assert "Jeremy Clarkson (locked design" not in first
    assert "NOT IN THIS PANEL — Molly-Mae, Jeremy Clarkson" in first
    assert "Jeremy Clarkson (locked design" in second


def test_the_locked_description_is_byte_identical_between_panels():
    """Defect 4's requirement, and it is free to hold: the description is a
    pure function of the character."""
    slides = [slide("Tracy Beaker denies it"), slide("Tracy Beaker walks off")]
    idea = idea_with(slides)
    line = "Tracy Beaker (locked design"
    a = _cast_lock_for(idea, 0)
    b = _cast_lock_for(idea, 1)
    assert a[a.index(line):a.index(line) + 200] == b[b.index(line):b.index(line) + 200]


# --- backwards compatibility ------------------------------------------------


def test_a_post_with_no_cast_is_untouched():
    idea = Idea(idea_id="G1", concept_note="",
                route_json=json.dumps({"show": "amp"}),
                slides_json=json.dumps([slide("x")]))
    assert _cast_lock_for(idea, 0) == ""
    assert manifest_for(idea) is None


def test_an_older_post_with_no_manifest_falls_back_to_the_whole_cast():
    """Nothing already in the queue changes shape."""
    idea = Idea(idea_id="G1", concept_note="",
                route_json=json.dumps({"show": "multiverse", "cast": CAST}))
    block = _cast_lock_for(idea, 0)          # no slides_json at all
    for name in ("Tracy Beaker", "Molly-Mae", "Jeremy Clarkson"):
        assert f"{name} (locked design" in block
    assert "NOT IN THIS PANEL" not in block


def test_calling_without_an_index_keeps_the_old_behaviour():
    idea = idea_with([slide("Tracy Beaker denies it")])
    assert "Jeremy Clarkson (locked design" in _cast_lock_for(idea)


# --- staleness --------------------------------------------------------------


def test_an_edited_slide_gets_a_freshly_computed_manifest():
    """A stale allowlist is worse than none — it forbids the wrong people."""
    original = [slide("Tracy Beaker denies it"), slide("Jeremy Clarkson walks in")]
    stored = build_manifest(post(*original), CAST).model_dump()
    edited = [slide("Molly-Mae denies it"), slide("Jeremy Clarkson walks in")]

    idea = idea_with(edited, stored=stored)
    fresh = manifest_for(idea)
    assert fresh.panels[0].cast_present == ["molly_mae"]
    assert "tracy_beaker" in fresh.panels[0].cast_forbidden


def test_an_unedited_post_reuses_the_stored_manifest():
    slides = [slide("Tracy Beaker denies it")]
    stored = build_manifest(post(*slides), CAST)
    stored.panels[0].blocking = "hand-authored"          # proves it wasn't rebuilt
    idea = idea_with(slides, stored=stored.model_dump())
    assert manifest_for(idea).panels[0].blocking == "hand-authored"


def test_the_fingerprint_moves_with_the_copy_and_the_brief():
    a = [slide("Tracy Beaker denies it", "art")]
    assert fingerprint(a) == fingerprint([slide("Tracy Beaker denies it", "art")])
    assert fingerprint(a) != fingerprint([slide("Tracy Beaker admits it", "art")])
    assert fingerprint(a) != fingerprint([slide("Tracy Beaker denies it", "other")])


# --- validation: rules 1 and 2 ----------------------------------------------


def test_a_clean_manifest_passes():
    assert validate(build_manifest(post(
        slide("Tracy Beaker denies it"), slide("Molly-Mae films it")), CAST)) == []


@pytest.mark.parametrize("panel,fragment", [
    (Panel(index=0, cast_present=["tracy_beaker"],
           cast_forbidden=["tracy_beaker", "molly_mae", "clarkson"]),
     "both present and forbidden"),
    (Panel(index=0, cast_present=["tracy_beaker"], cast_forbidden=["molly_mae"]),
     "on neither list"),
    (Panel(index=0, cast_present=["tracy_beaker", "molly_mae", "clarkson", "haaland"],
           cast_forbidden=[]),
     "not in this episode's cast"),
])
def test_rule_2_every_character_on_exactly_one_list(panel, fragment):
    reasons = validate(Manifest(cast=CAST, panels=[panel]))
    assert any(fragment in r for r in reasons), reasons


def test_rule_1_the_copy_cannot_point_at_somebody_the_episode_never_draws():
    m = Manifest(cast=CAST, panels=[Panel(
        index=0, beat_text="Tracy Beaker points at Molly-Mae",
        cast_present=["tracy_beaker"], cast_forbidden=["molly_mae", "clarkson"],
    )])
    reasons = validate(m)
    assert any("Molly-Mae" in r and "never drawn" in r for r in reasons)


def test_rule_1_strict_form_is_ready_for_the_text_stage():
    """A bubble's tail is the attribution, so its anchor must be in the panel.
    Inert until text elements are populated — this proves it isn't dead."""
    from chrgd.manifest import TextElement

    m = Manifest(cast=CAST, panels=[Panel(
        index=0, cast_present=["tracy_beaker"],
        cast_forbidden=["molly_mae", "clarkson"],
        text_elements=[TextElement(
            role="dialogue", content="It's not what it looks like",
            placement="bubble anchored to Molly-Mae's mouth")],
    )])
    reasons = validate(m)
    assert any("anchored to Molly-Mae" in r for r in reasons)


def test_a_character_talked_about_while_absent_is_not_a_failure():
    """Legitimate: they haven't arrived yet. Derivation already knows this, so
    the check must not fire on its own output."""
    m = build_manifest(post(
        slide("Tracy Beaker says Jeremy Clarkson will be here at six"),
        slide("Jeremy Clarkson walks in"),
    ), CAST)
    assert validate(m) == []
