"""The Show layer.

The single most important property here is the **null case**: an idea with no
show behaves exactly as it did before shows existed. Every consumer (the write
call, the renderer, the concept gate, the concept sketch) has a show-shaped
hook in it now, and each one has to be a genuine pass-through when there's no
show. If these tests go red, existing posts have changed behaviour.
"""

from __future__ import annotations

import json

import pytest

from chrgd.brand import load_brand
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.images import compose_design_prompt, style_for_idea
from chrgd.models import Idea, Slide
from chrgd.pipeline import build_user_message, creation_prefs
from chrgd.shows import (
    DEFAULT_GATE_PROFILE,
    brand_for_show,
    gate_profile_for_idea,
    get_gate_profile,
    get_show,
    job_show_filter,
    load_gate_profiles,
    load_shows,
    ordered_shows,
    show_for_idea,
)

ALL_SHOWS = ("amp", "multiverse", "straight_up", "session", "live_wire",
             "the_stack")


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def idea_on(show: str | None, **route) -> Idea:
    if show:
        route["show"] = show
    return Idea(
        idea_id="G1",
        concept_note="squat rack hogs at 6pm",
        route_json=json.dumps(route) if route else None,
    )


# --- the registry -----------------------------------------------------------


def test_every_show_loads():
    shows = load_shows()
    assert set(shows) == set(ALL_SHOWS)


def test_shows_are_ordered_through_the_week():
    assert [s.key for s in ordered_shows()] == [
        "session", "straight_up", "amp", "multiverse", "live_wire", "the_stack"
    ]


def test_unknown_show_is_none_not_an_error():
    assert get_show("nope") is None
    assert get_show("") is None


@pytest.mark.parametrize("key", ALL_SHOWS)
def test_every_show_is_complete_enough_to_drive_a_build(key):
    """A show missing its spine or voice would silently degrade to the generic
    engine — the exact failure this whole layer exists to prevent."""
    show = get_show(key)
    assert show.label and show.tagline and show.icon and show.weekday
    assert show.spine.roles, f"{key} has no spine"
    assert len(show.spine.briefs) == len(show.spine.roles)
    assert show.voice.block.strip(), f"{key} has no voice"
    assert show.voice.banned, f"{key} bans nothing"
    assert show.look.style_preset and show.look.title_card
    assert show.slides_min <= show.slides_max


@pytest.mark.parametrize("key", ALL_SHOWS)
def test_every_show_style_preset_exists_in_brand_toml(key):
    """A preset naming a style that isn't defined resolves to '' — the show
    would silently lose its art direction."""
    assert load_brand().style_prompt(get_show(key).look.style_preset)


def test_show_for_idea_reads_the_route():
    assert show_for_idea(idea_on("amp")).key == "amp"
    assert show_for_idea(idea_on(None)) is None
    assert show_for_idea(Idea(idea_id="G2", route_json="{not json")) is None


def test_creation_prefs_carries_the_show_through_rebuilds():
    assert creation_prefs(idea_on("session"))["show"] == "session"
    assert "show" not in creation_prefs(idea_on(None))


# --- the write call ---------------------------------------------------------


def test_off_format_build_message_is_unchanged():
    """The null case for the write call."""
    msg = build_user_message(idea_on(None))
    assert "EPISODE OF" not in msg
    assert "THE SPINE" not in msg


@pytest.mark.parametrize("key", ALL_SHOWS)
def test_a_show_brief_reaches_the_build(key):
    show = get_show(key)
    msg = build_user_message(idea_on(key))
    assert f"EPISODE OF **{show.label}**" in msg
    assert show.voice.block.strip().splitlines()[0] in msg
    assert "THE SPINE" in msg
    # every slide of the spine is briefed, not just named
    for brief in show.spine.briefs:
        assert brief in msg


def test_show_length_applies_unless_the_editor_chose_one():
    assert "aim for 4-6 slides" in build_user_message(idea_on("amp"))
    picked = build_user_message(idea_on("amp", length_pref="deep"))
    assert "aim for 4-6 slides" not in picked


def test_banned_moves_reach_the_build():
    msg = build_user_message(idea_on("session"))
    assert "NEVER, in this show:" in msg
    assert "%1RM" in msg


# --- the look ---------------------------------------------------------------


def _slide() -> Slide:
    return Slide(headline="Hi", supporting="there", image_prompt="a gym")


def test_off_format_render_prompt_keeps_the_house_style():
    """The null case for the renderer."""
    prompt = compose_design_prompt(
        _slide(), load_brand(), None, house_style="THE HOUSE LOOK"
    )
    assert "THE HOUSE LOOK" in prompt
    assert "SHOW TITLE" not in prompt


def test_a_show_look_outranks_the_house_style():
    prompt = compose_design_prompt(
        _slide(), load_brand(), None, house_style="THE HOUSE LOOK",
        show=get_show("multiverse"),
    )
    assert "THE HOUSE LOOK" not in prompt
    assert "comic book page" in prompt.lower()
    assert "Palette:" in prompt and "Recurring motif:" in prompt


def test_the_show_is_named_on_slide_one_only():
    """D10. The text rules forbid extra labels outright, so the title has to be
    granted explicitly or the model will refuse to draw it."""
    show = get_show("amp")
    first = compose_design_prompt(_slide(), load_brand(), None, index=0, show=show)
    later = compose_design_prompt(_slide(), load_brand(), None, index=3, show=show)
    assert "SHOW TITLE" in first and "AMP" in first
    assert "SHOW TITLE" not in later


def test_a_shows_preset_beats_a_leftover_style_pick():
    assert style_for_idea(idea_on("multiverse", style="gritty")) == "comic"
    assert style_for_idea(idea_on(None, style="gritty")) == "gritty"
    assert style_for_idea(idea_on(None)) is None


def test_the_multiverse_drops_the_brand_furniture():
    """D3 — this show breaks the house look; the other four keep it."""
    brand = load_brand()
    mv = brand_for_show(brand, get_show("multiverse"))
    assert mv.identity.wordmark == "" and mv.identity.footer_bar is False
    assert mv.identity.handle  # the handle stays
    # the four that share the furniture keep it, and the source brand is intact
    assert brand_for_show(brand, get_show("session")).identity.wordmark == "CHRGD"
    assert brand_for_show(brand, None) is brand
    assert brand.identity.wordmark == "CHRGD"


# --- the gate ---------------------------------------------------------------


def test_gate_profiles_load():
    assert set(load_gate_profiles()) == {
        "comedy_useful", "claims", "useful_legible", "continuity_likeness",
        "diagnostic_pull"
    }


def test_off_format_gets_the_no_op_gate_profile():
    """The null case for the concept gate: empty focus blocks mean every stage's
    system prompt is passed through untouched, so verdicts don't move."""
    profile = gate_profile_for_idea(idea_on(None))
    assert profile is DEFAULT_GATE_PROFILE
    assert not any((profile.judge_focus, profile.rival_focus,
                    profile.tourney_focus, profile.glance_focus))


def test_an_unknown_profile_degrades_to_the_default():
    assert get_gate_profile("nonsense") is DEFAULT_GATE_PROFILE


@pytest.mark.parametrize(
    "key,expected",
    [("amp", "comedy_useful"), ("straight_up", "claims"),
     ("session", "useful_legible"), ("multiverse", "continuity_likeness"),
     ("the_stack", "diagnostic_pull")],
)
def test_shows_are_judged_on_their_own_bar(key, expected):
    profile = gate_profile_for_idea(idea_on(key))
    assert profile.key == expected
    assert profile.judge_focus and profile.rival_focus
    assert profile.tourney_focus and profile.glance_focus


def test_live_wire_keeps_todays_rubric():
    """The hot-take rubric is what LIVE WIRE was tuned for — it should NOT get
    a focus block bolted on top of the thing it already does well."""
    assert gate_profile_for_idea(idea_on("live_wire")) is DEFAULT_GATE_PROFILE


def test_with_focus_is_a_pass_through_when_empty():
    from chrgd.conceptgate import _with_focus

    assert _with_focus("SYSTEM", "") == "SYSTEM"
    assert _with_focus("SYSTEM", "   ") == "SYSTEM"
    assert "SHOW-SPECIFIC BAR" in _with_focus("SYSTEM", "judge it on X")


# --- job filtering ----------------------------------------------------------


def test_pre_show_concept_jobs_still_count_as_off_format():
    """Concept jobs created before this layer have no `show` key at all. If the
    off-format filter didn't match them, a live database's whole concept
    history would go invisible and every reopened /create would pay for a new
    run."""
    clause, params = job_show_filter("")
    assert "NOT LIKE" in clause and params == ()
    clause, params = job_show_filter("amp")
    assert params == ('%"show": "amp"%',)


def test_off_format_filter_matches_legacy_and_blank_rows(store):
    rows = [
        ('{"seed": "", "fresh": false}', "legacy"),          # pre-show job
        ('{"seed": "", "fresh": false, "show": ""}', "new"),  # explicit blank
        ('{"seed": "", "fresh": false, "show": "amp"}', "amp"),
    ]
    for params_json, tag in rows:
        job_id = store.create_job("concepts")
        store.conn.execute(
            "UPDATE jobs SET status='COMPLETED', params_json=?, result_json=? "
            "WHERE job_id = ?",
            (params_json, json.dumps({"concepts": [{"title": tag}]}), job_id),
        )
    store.conn.commit()

    clause, params = job_show_filter("")
    found = store.conn.execute(
        "SELECT result_json FROM jobs WHERE kind='concepts' " + clause, params
    ).fetchall()
    titles = {json.loads(r["result_json"])["concepts"][0]["title"] for r in found}
    assert titles == {"legacy", "new"}

    clause, params = job_show_filter("amp")
    found = store.conn.execute(
        "SELECT result_json FROM jobs WHERE kind='concepts' " + clause, params
    ).fetchall()
    assert len(found) == 1


# --- the concept sketch -----------------------------------------------------


def test_sketch_pitches_inside_the_shows_format(store, settings):
    from chrgd.concepts import sketch_concepts

    seen = {}

    class Fake:
        def complete(self, system, user):
            seen["user"] = user

            class R:
                content = json.dumps({"concepts": [
                    {"title": "t", "hook": "h", "flavour": "observation"}
                ]})
                prompt_tokens = completion_tokens = 1
            return R()

    out = sketch_concepts(store, settings, show="straight_up", generator=Fake())
    assert out["show"] == "straight_up"
    assert "EPISODE OF **STRAIGHT UP**" in seen["user"]
    assert "EPISODES OF THIS SHOW" in seen["user"]
    # the forced Amp slot is off-format only — on a show it would cost a concept
    assert "One of them stars Amp" not in seen["user"]


def test_off_format_sketch_still_forces_an_amp_concept(store, settings):
    from chrgd.concepts import sketch_concepts

    seen = {}

    class Fake:
        def complete(self, system, user):
            seen["user"] = user

            class R:
                content = json.dumps({"concepts": [
                    {"title": "t", "hook": "h", "flavour": "observation"}
                ]})
                prompt_tokens = completion_tokens = 1
            return R()

    out = sketch_concepts(store, settings, generator=Fake())
    assert out["show"] == ""
    assert "EPISODE OF" not in seen["user"]
    assert "One of them stars Amp" in seen["user"]
    assert any(c["amp"] for c in out["concepts"])
