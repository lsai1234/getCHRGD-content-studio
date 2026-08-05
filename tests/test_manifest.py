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
    layout_note,
    manifest_for,
    validate,
    word_count,
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
        slide("Tracy Beaker denies it"), slide("Molly-Mae films it"),
        slide("Who was in here at three in the morning?", feature=False),
    ), CAST)) == []


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


def test_rule_11_a_bubble_cannot_point_at_somebody_outside_the_panel():
    """The tail is the attribution, so its anchor has to be in frame."""
    from chrgd.manifest import TextElement

    m = Manifest(cast=CAST, panels=[Panel(
        index=0, cast_present=["tracy_beaker"],
        cast_forbidden=["molly_mae", "clarkson"],
        text_elements=[TextElement(
            role="dialogue", content="It's not what it looks like",
            anchor="molly_mae",
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
        slide("Who told him?", feature=False),
    ), CAST)
    assert validate(m) == []


# === STAGE 3: text declared, not implied ====================================
#
# The words were being concatenated into the prompt as narrative context, so
# the model rendered them wherever it liked — as the caption, and again as a
# poster on the wall. And the only channel the writer had for marking a speaker
# was a name prefix inside the copy, which then got painted onto the artwork.


def test_narration_becomes_a_box_with_no_label():
    m = build_manifest(post(slide("THE SIGN HAS SAID BACK SOON SINCE 2019")), CAST)
    el = m.panels[0].text_elements[0]
    assert el.role == "caption"
    assert el.content == "THE SIGN HAS SAID BACK SOON SINCE 2019"
    assert "narration box" in el.placement
    assert el.anchor == ""


def test_a_name_prefix_becomes_a_bubble_tail_and_leaves_the_artwork():
    """Defect 10. The prefix is the writer's machine-readable signal; it must
    never be lettered."""
    m = build_manifest(post(
        slide("Tracy Beaker is caught", supporting="Orangina: it's not what it looks like"),
    ), ["tracy_beaker", "orangina"])
    dialogue = [e for e in m.panels[0].text_elements if e.role == "dialogue"]
    assert len(dialogue) == 1
    el = dialogue[0]
    assert el.content == "it's not what it looks like"     # the label is gone
    assert not el.content.lower().startswith("orangina")
    assert el.anchor == "orangina"
    assert "tail points to Orangina's mouth" in el.placement


def test_quotes_around_the_dialogue_come_off_too():
    m = build_manifest(post(
        slide('Clarkson: "I have not moved since March"'),
    ), ["clarkson"])
    assert m.panels[0].text_elements[0].content == "I have not moved since March"


def test_a_narration_prefix_is_stripped_without_becoming_dialogue():
    m = build_manifest(post(slide("Narrator: nobody said anything")), CAST)
    el = m.panels[0].text_elements[0]
    assert el.role == "caption" and el.content == "nobody said anything"


def test_a_device_prefix_becomes_mediated_sound_and_requires_the_object():
    """A tannoy line with no tannoy in frame is a floating label."""
    m = build_manifest(post(
        slide("Tracy Beaker stops dead", supporting="Tannoy: the sauna is closed"),
    ), ["tracy_beaker"])
    panel = m.panels[0]
    broadcast = [e for e in panel.text_elements if e.role == "broadcast"][0]
    assert broadcast.content == "the sauna is closed"
    assert broadcast.anchor == "tannoy"
    assert "issuing from the tannoy" in broadcast.placement
    assert "mediated sound" in broadcast.style
    assert panel.props_present == ["tannoy"]
    # and the prompt puts the object in the frame
    assert "REQUIRED IN FRAME — tannoy" in cast_block(panel)


def test_an_unrecognised_speaker_label_blocks_rather_than_being_painted():
    """Printing 'DAVE:' on the artwork is the failure this stage exists to
    stop, so an unattributable line has to block, not shrug."""
    m = build_manifest(post(slide("Dave: get off my rack")), CAST)
    assert any("still carries a speaker label" in r for r in validate(m))


def test_the_show_masthead_is_inside_the_closed_set():
    """Granting it in one place and forbidding it in another is how the model
    ends up inventing more text."""
    m = build_manifest(post(slide("Tracy Beaker denies it"), slide("x")), CAST,
                       title_card="CHRGD MULTIVERSE", title_note="a masthead")
    assert m.panels[0].text_elements[0].role == "title"
    assert m.panels[0].text_elements[0].content == "CHRGD MULTIVERSE"
    # panel 1 only — it is not furniture on every frame
    assert all(e.role != "title" for e in m.panels[1].text_elements)


# --- the prompt -------------------------------------------------------------


def test_the_prompt_declares_a_closed_set_with_the_three_rules():
    from chrgd.manifest import text_spec

    m = build_manifest(post(
        slide("TRACY BEAKER HAS GREASE ON HER HANDS",
              supporting="Tracy Beaker: I have never fixed anything"),
    ), CAST)
    spec = text_spec(m.panels[0])
    assert "COMPLETE and CLOSED list" in spec
    assert "VERBATIM" in spec and "CLOSED SET" in spec and "ONE INSTANCE EACH" in spec
    assert "NO LABELS" in spec
    # the specific thing that was being duplicated
    for banned in ("no posters", "no wall art", "no whiteboards", "no background lettering"):
        assert banned in spec, banned
    assert 'Render exactly, character for character: "I have never fixed anything"' in spec
    assert "SPEECH BUBBLE" in spec and "NARRATION BOX" in spec


def test_a_wordless_panel_says_no_text_rather_than_leaving_it_open():
    from chrgd.manifest import text_spec

    assert "TEXT ON THIS PANEL: NONE" in text_spec(Panel(index=0, total=1))


def test_the_wall_art_instruction_is_gone_from_a_panel_prompt():
    """`_EMBEDDED_TEXT_RULES` told the model to render the copy as gym signage
    — 'sprayed on a wall', 'written on the whiteboard'. That IS the duplicated
    caption. A specification that leaves it in place just gives two orders."""
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    s = Slide(headline="SHORT LINE", supporting="two words")
    m = build_manifest(post(slide("SHORT LINE", supporting="two words")), CAST)

    without = compose_design_prompt(s, load_brand(), None)
    assert "sprayed on a wall" in without          # the old path, unchanged

    with_panel = compose_design_prompt(s, load_brand(), None, panel=m.panels[0])
    assert "sprayed on a wall" not in with_panel
    assert "written on the whiteboard" not in with_panel
    assert "TANGIBLE PART OF THE SCENE" not in with_panel
    assert "TEXT TO PLACE ON IMAGE" not in with_panel
    # whiteboards now appear only in the ban
    assert "no whiteboards" in with_panel
    assert "COMPLETE and CLOSED list" in with_panel


def test_off_format_posts_keep_the_old_text_handling():
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    prompt = compose_design_prompt(
        Slide(headline="H", supporting="S"), load_brand(), None)
    assert "TEXT TO PLACE ON IMAGE" in prompt
    assert "Headline text: H" in prompt


# --- rules 3, 4 and 10 ------------------------------------------------------


def test_rule_3_a_string_declared_twice_is_refused():
    m = build_manifest(post(
        slide("THE SAME LINE", supporting="the same line"),
    ), CAST)
    assert any("declared 2 times" in r for r in validate(m))


def test_rule_4_over_long_copy_is_capped():
    from chrgd.manifest import MAX_ELEMENT_CHARS

    m = build_manifest(post(slide("TRACY " * 30)), CAST)
    reasons = validate(m)
    assert any(f"cap {MAX_ELEMENT_CHARS}" in r for r in reasons)


def test_rule_4_the_panel_total_is_capped_too():
    from chrgd.manifest import MAX_PANEL_CHARS

    m = build_manifest(post(slide("A" * 85, supporting="B" * 85, )), CAST)
    m.panels[0].text_elements.append(
        type(m.panels[0].text_elements[0])(id="t9", content="C" * 60))
    assert any(f"cap {MAX_PANEL_CHARS}" in r for r in validate(m))


def test_rule_4_long_words_are_flagged_but_cast_names_are_exempt():
    """'Chimpanzini Bananini' cannot be rewritten shorter and is the joke."""
    m = build_manifest(post(slide("CHIMPANZINI BANANINI IS INCOMPREHENSIBLE")),
                       ["chimpanzini"])
    reasons = validate(m)
    assert any("INCOMPREHENSIBLE" in r for r in reasons)
    assert not any("CHIMPANZINI" in r for r in reasons)


# === STAGE 4: physical state, blocking, props, crowd, progress ==============


@pytest.fixture()
def show():
    from chrgd.shows import get_show

    return get_show("multiverse")


def built(slides, cast=CAST, *, show=None):
    kw = {}
    if show is not None:
        kw = {"props": show.props, "crowd_base": show.crowd_base,
              "title_card": show.look.title_card, "title_note": show.look.title_note,
              "layout_family": show.key}
    return build_manifest(post(*slides), cast, **kw)


# --- blocking (defect 5) ----------------------------------------------------


def test_blocking_states_what_each_character_is_doing():
    m = built([slide("MOLLY-MAE FINDS THE TRIPOD",
                     art="Molly-Mae kneels by the frame holding a phone tripod up")])
    assert "Molly-Mae: Molly-Mae kneels by the frame holding a phone tripod up" \
        in m.panels[0].blocking


def test_blocking_never_takes_a_line_of_dialogue_as_staging():
    """'Tracy Beaker: I have never fixed anything' is somebody talking, not a
    description of where she is standing."""
    m = built([slide("TRACY BEAKER IS CAUGHT",
                     supporting="Tracy Beaker: I have never fixed anything",
                     art="Tracy Beaker stands at the machine with both palms up")])
    blocking = m.panels[0].blocking
    assert "palms up" in blocking
    assert "never fixed anything" not in blocking


def test_directional_relationships_are_called_out():
    m = built([slide("SHE POINTS AT MOLLY-MAE",
                     art="Tracy Beaker pointing at Molly-Mae across the room")])
    assert "who points at whom" in m.panels[0].blocking


def test_a_countable_quantity_is_stated_rather_than_guessed():
    m = built([slide("THREE PLATES", art="Tracy Beaker loading 3 plates on the bar")])
    assert "COUNTABLE IN FRAME: 3 plates" in m.panels[0].blocking


def test_a_weight_is_stated_as_a_weight_not_a_count():
    m = built([slide("SHE LIFTS IT", art="Tracy Beaker with a 20 kg plate in one hand")])
    blocking = m.panels[0].blocking
    assert "WEIGHT SHOWN: 20kg" in blocking
    assert "draw exactly that many" not in blocking


def test_two_characters_placed_by_one_sentence_are_named_together():
    m = built([slide("THE ROOM TURNS",
                     art="Tracy Beaker at the back, Molly-Mae centre")])
    assert "Tracy Beaker, Molly-Mae:" in m.panels[0].blocking


# --- camera, crowd, progress (defects 3, 4, 6) ------------------------------


def test_the_camera_is_chosen_from_what_the_panel_holds():
    m = built([
        slide("Tracy Beaker alone"),
        slide("Molly-Mae arrives"),
        slide("Jeremy Clarkson walks in"),
    ])
    assert m.panels[0].camera.startswith("close")
    assert m.panels[1].camera.startswith("medium two-shot")
    assert m.panels[2].camera.startswith("wide")


def test_crowd_density_is_fixed_and_only_ever_ramps(show):
    m = built([
        slide("Tracy Beaker denies it"),
        slide("Molly-Mae films it"),
        slide("Everyone turns to look"),
        slide("Tracy Beaker says nothing"),
    ], show=show)
    counts = [p.crowd_count for p in m.panels]
    assert counts[0] == counts[1] == show.crowd_base   # fixed, not re-rolled
    assert counts[2] > counts[1]                       # the room turns
    assert counts == sorted(counts), "crowd density went down"


def test_the_progress_element_is_computed_from_the_index():
    """Improvised, it came out near-full on panel 1 and identical on three
    panels running."""
    m = built([slide("a"), slide("b"), slide("c"), slide("d")])
    assert "step 1 of 4" in m.panels[0].progress
    assert "1 segment(s) filled and 3 segment(s) empty" in m.panels[0].progress
    assert "step 4 of 4" in m.panels[3].progress
    assert "4 segment(s) filled and 0 segment(s) empty" in m.panels[3].progress
    assert len({p.progress for p in m.panels}) == 4    # never identical


def test_the_unbound_evolution_string_is_suppressed_when_a_panel_states_it():
    """`design_system.evolution` was injected identically on every frame with no
    value attached — 'a progress motif fills', on all of them."""
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    ds = {"palette": "p", "evolution": "a progress motif fills"}
    slides = [Slide(headline="a"), Slide(headline="b")]
    m = built([slide("a"), slide("b")])

    old = compose_design_prompt(slides[1], load_brand(), None, slides=slides,
                                index=1, design_system=ds)
    assert "Show visible motion from the last frame" in old

    new = compose_design_prompt(slides[1], load_brand(), None, slides=slides,
                                index=1, design_system=ds, panel=m.panels[1])
    assert "Show visible motion from the last frame" not in new
    assert "step 2 of 2" in new


# --- the locked prop sheet (defect 4) ---------------------------------------


def test_props_named_in_the_beat_get_locked_descriptions(show):
    m = built([slide("THE LAT PULLDOWN WORKED",
                     art="the lat pulldown, cable frayed")], show=show)
    panel = m.panels[0]
    assert "lat_pulldown" in panel.props_present
    block = cast_block(panel, show)
    assert "the lat pulldown (locked — hold this identical in every panel)" in block
    assert "cracked black seat pad" in block


def test_writing_on_a_prop_joins_the_closed_set(show):
    """Otherwise the closed-set rule forbids the BACK SOON sign's own words."""
    m = built([slide("THE MACHINE", art="the lat pulldown")], show=show)
    labels = [e for e in m.panels[0].text_elements if e.role == "prop_label"]
    assert labels and labels[0].content == "BACK SOON"
    assert labels[0].anchor == "lat_pulldown"


def test_a_prop_description_is_byte_identical_across_panels(show):
    m = built([slide("A", art="the lat pulldown"),
               slide("B", art="Tracy Beaker at the lat pulldown")], show=show)
    from chrgd.manifest import prompt_consistency

    prompts = [cast_block(p, show) for p in m.panels]
    assert prompt_consistency(prompts) == []
    line = "the lat pulldown (locked"
    a, b = prompts[0], prompts[1]
    assert a[a.index(line):a.index(line) + 180] == b[b.index(line):b.index(line) + 180]


def test_rule_7_catches_a_re_summarised_lock():
    from chrgd.manifest import prompt_consistency

    reasons = prompt_consistency([
        "the squat rack (locked — hold this identical): One battered yellow rack.",
        "the squat rack (locked — hold this identical): A yellow power rack.",
    ])
    assert any("differs from the one used earlier" in r for r in reasons)


# --- physical state (defect 3) ----------------------------------------------


def test_a_prop_state_change_is_tracked_from_the_copy(show):
    m = built([
        slide("THE SIGN HAS BEEN UP SINCE 2019", art="the lat pulldown, out of order"),
        slide("TODAY THE LAT PULLDOWN WORKED", art="the lat pulldown, cable running"),
        slide("TRACY BEAKER BREAKS THE LAT PULLDOWN AGAIN",
              art="Tracy Beaker snaps the cable"),
    ], show=show)
    assert "lat_pulldown=broken" in m.panels[0].state
    assert "lat_pulldown=working" in m.panels[1].state
    assert "lat_pulldown=broken" in m.panels[2].state
    assert m.panels[2].state_changes == ["lat_pulldown"]


def test_a_denial_does_not_change_the_state_of_anything(show):
    """'I have never fixed anything' was reading as the machine being fixed,
    and then propagating forward for the rest of the episode."""
    m = built([slide("TRACY BEAKER IS CAUGHT",
                     supporting="Tracy Beaker: I have never fixed anything",
                     art="Tracy Beaker at the lat pulldown, palms up")], show=show)
    assert "lat_pulldown=" not in m.panels[0].state


def test_rule_5_two_panels_of_the_same_moment_are_refused():
    m = built([slide("Tracy Beaker stands there"), slide("Tracy Beaker stands there")])
    assert any("the same moment twice" in r for r in validate(m))


def test_rule_6_an_uncaused_state_change_is_refused():
    """Well-founded version: the object moved and nothing in the panel moved
    it. A fixed-broken-fixed cycle is the plot, not a defect."""
    m = Manifest(cast=CAST, panels=[
        Panel(index=0, cast_present=CAST, state="a|rack=free|wide",
              progress="step 1 of 2"),
        Panel(index=1, cast_present=CAST, state="b|rack=occupied|wide",
              progress="step 2 of 2"),
    ])
    assert any("nothing in this panel changes it" in r for r in validate(m))

    caused = m.model_copy(deep=True)
    caused.panels[1].state_changes = ["rack"]
    assert not any("nothing in this panel changes it" in r for r in validate(caused))


def test_a_legitimate_re_break_is_not_a_regression(show):
    m = built([
        slide("THE LAT PULLDOWN IS OUT OF ORDER", art="the lat pulldown, dead"),
        slide("TODAY THE LAT PULLDOWN WORKED", art="the lat pulldown, running"),
        slide("TRACY BEAKER BREAKS THE LAT PULLDOWN", art="Tracy Beaker snaps the cable"),
    ], show=show)
    assert not any("configuration the story has not put it in" in r
                   for r in validate(m))


def test_rule_8_the_progress_value_must_match_the_index():
    m = Manifest(cast=CAST, panels=[
        Panel(index=0, total=2, cast_present=CAST, state="a", progress="step 1 of 2"),
        Panel(index=1, total=2, cast_present=CAST, state="b", progress="step 1 of 2"),
    ])
    assert any("other than 'step 2 of 2'" in r for r in validate(m))


def test_the_staging_block_reaches_the_prompt(show):
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    m = built([slide("TRACY BEAKER SNAPS THE CABLE",
                     art="Tracy Beaker at the lat pulldown, pointing at the squat rack")],
              show=show)
    prompt = compose_design_prompt(
        Slide(headline="TRACY BEAKER SNAPS THE CABLE"), load_brand(), None,
        panel=m.panels[0])
    assert "BLOCKING —" in prompt
    assert "CAMERA:" in prompt
    assert "BACKGROUND: exactly 4 other figures" in prompt
    assert "step 1 of 1" in prompt


def test_off_format_posts_get_no_staging():
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    prompt = compose_design_prompt(Slide(headline="H"), load_brand(), None)
    for absent in ("BLOCKING —", "CAMERA:", "BACKGROUND: exactly", "step 1 of"):
        assert absent not in prompt, absent


# === STAGE 5: the hook, the caps, the ending, one template ==================


# --- panel 1 is a hook (defect 7) -------------------------------------------


def test_panel_one_is_capped_harder_than_the_rest():
    """It has half a second to earn the swipe, and a text block eating the
    canvas is what these carousels were stalling on."""
    from chrgd.manifest import MAX_WORDS_PANEL, MAX_WORDS_PANEL_ONE

    long_line = " ".join(["word"] * 20)
    m = built([slide(long_line), slide(long_line)])
    reasons = validate(m)
    assert any(f"panel 1: 20 words of copy (cap {MAX_WORDS_PANEL_ONE}" in r
               for r in reasons)
    assert any("half a second to earn the swipe" in r for r in reasons)
    # 20 words is fine on any other panel
    assert not any(f"panel 2: 20 words" in r for r in reasons)
    assert MAX_WORDS_PANEL == 25


def test_realistic_panel_one_copy_fits_the_cap():
    """A cap nothing can meet is one that gets switched off."""
    m = built([slide("TRACY BEAKER HAS GREASE ON BOTH HANDS",
                     supporting="Tracy Beaker: I have never fixed anything")])
    assert word_count(m.panels[0]) == 12
    assert not any("words of copy" in r for r in validate(m))


def test_the_masthead_is_a_corner_mark_and_not_the_biggest_thing(show):
    m = built([slide("TRACY BEAKER DENIES IT")], show=show)
    title = [e for e in m.panels[0].text_elements if e.role == "title"][0]
    assert "top-left" in title.placement.lower()
    assert "never be the largest element" in title.placement + title.style
    # and it is outside the writer's word budget, since they cannot shorten it
    assert word_count(m.panels[0]) == 4


def test_the_show_brief_tells_the_writer_the_caps(show):
    brief = show.brief_block()
    assert "12 WORDS MAXIMUM" in brief
    assert "25 words maximum" in brief
    assert "SLIDE 1 IS THE HOOK, NOT THE SETUP" in brief
    assert "Never on the location" in brief


def test_a_show_with_no_cast_gets_no_cap_instructions():
    from chrgd.shows import get_show

    assert "12 WORDS MAXIMUM" not in get_show("session").brief_block()


# --- the closing card (defect 8) --------------------------------------------


def test_the_comment_trigger_becomes_the_last_panel():
    """It lived on the post and never became a slide, so the carousel finished
    on a beat with no engagement prompt."""
    from chrgd.manifest import ensure_closing_card

    payload = post(slide("TRACY BEAKER SAYS NOTHING"))
    assert ensure_closing_card(payload, comment_trigger="Who was in here at 3am")
    last = payload["slides"][-1]
    assert last["headline"] == "Who was in here at 3am?"
    assert last["role"] == "cta"
    assert last["feature_character"] is False


def test_an_unresolved_line_becomes_the_question_when_there_is_no_trigger():
    from chrgd.manifest import ensure_closing_card

    payload = post(slide("SHE WENT BACK AND BROKE IT AGAIN"))
    assert ensure_closing_card(
        payload, unresolved="somebody was in here at three in the morning")
    assert payload["slides"][-1]["headline"] == \
        "Somebody was in here at three in the morning?"


def test_a_set_that_already_ends_on_a_question_gets_no_second_card():
    from chrgd.manifest import ensure_closing_card

    payload = post(slide("WHO ELSE WAS IN HERE AT 3AM?"))
    assert not ensure_closing_card(payload, comment_trigger="Who was it")
    assert len(payload["slides"]) == 1


def test_no_card_is_invented_when_there_is_nothing_to_ask():
    from chrgd.manifest import ensure_closing_card

    payload = post(slide("SHE SAID NOTHING"))
    assert not ensure_closing_card(payload)
    assert len(payload["slides"]) == 1


def test_the_slide_ceiling_is_respected():
    from chrgd.manifest import ensure_closing_card

    payload = post(*[slide(f"beat {i}") for i in range(10)])
    assert not ensure_closing_card(payload, comment_trigger="who", max_slides=10)


def test_the_closing_card_is_a_question_card_with_nobody_on_it(show):
    from chrgd.manifest import ensure_closing_card, layout_note

    payload = post(slide("TRACY BEAKER SAYS NOTHING"))
    ensure_closing_card(payload, comment_trigger="Who was in here at 3am")
    m = build_manifest(payload, CAST, props=show.props, crowd_base=show.crowd_base,
                       layout_family=show.key)
    last = m.panels[-1]
    assert last.layout == "multiverse/question-card"
    assert last.cast_present == [] and set(last.cast_forbidden) == set(CAST)
    assert "the question is the whole frame" in layout_note(last).lower()


def test_rule_13_an_episode_that_asks_nothing_is_refused():
    m = built([slide("Tracy Beaker denies it"), slide("She says nothing")])
    assert any("the last panel asks nothing" in r for r in validate(m))


# --- one template (defect 9) ------------------------------------------------


def test_the_variant_is_picked_deterministically_by_density(show):
    m = built([
        slide("SHORT LINE"),                                   # 2 words
        slide("A LONGER NARRATION LINE THAT RUNS ON A BIT", supporting="and more"),
        slide("SHE SPEAKS", supporting="Tracy Beaker: get off my rack"),
        slide("Who was it?", feature=False),
    ], show=show)
    assert m.panels[0].layout == "multiverse/short"
    assert m.panels[1].layout == "multiverse/medium"
    assert m.panels[2].layout == "multiverse/with-dialogue"


def test_rule_12_one_template_family_for_the_set():
    m = Manifest(cast=CAST, panels=[
        Panel(index=0, total=2, cast_present=CAST, layout="multiverse/short",
              progress="step 1 of 2", beat_text="a?"),
        Panel(index=1, total=2, cast_present=CAST, layout="explainer/medium",
              progress="step 2 of 2", beat_text="b?"),
    ])
    assert any("more than one layout template" in r for r in validate(m))


def test_every_panel_states_the_same_template(show):
    m = built([slide("a"), slide("b", supporting="Tracy Beaker: hello"),
               slide("Who?", feature=False)], show=show)
    families = {p.layout.partition("/")[0] for p in m.panels}
    assert families == {"multiverse"}
    assert all("Every panel in this set uses the same template" in layout_note(p)
               for p in m.panels)


def test_the_layout_note_reaches_the_prompt(show):
    from chrgd.brand import load_brand
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    m = built([slide("SHORT LINE")], show=show)
    prompt = compose_design_prompt(Slide(headline="SHORT LINE"), load_brand(),
                                   None, panel=m.panels[0])
    assert "LAYOUT — SHORT" in prompt
    assert "Do not invent a different arrangement" in prompt


def test_normal_writing_survives_all_of_it():
    """A validator that fires on good copy is one that gets switched off."""
    m = build_manifest(post(
        slide("THE LAT PULLDOWN WORKED ON TUESDAY"),
        slide("Molly-Mae finds a tripod under the frame",
              supporting="Molly-Mae: I suppose that was me"),
        slide("Tracy Beaker says nothing"),
        slide("Who else was in here at 3am?", feature=False),
    ), CAST)
    assert validate(m) == []
