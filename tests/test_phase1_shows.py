"""Phase 1 — AMP's free state system and LIVE WIRE's interest territories.

Same guarantee as Phase 0 and worth restating: every hook here is a
pass-through when it doesn't apply. An Amp post created before the AMP show
existed still runs the rising charge arc; every lane except Live Wire still
scans the country the way it always did.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from chrgd.brand import load_brand
from chrgd.character import (
    AMP_STATES,
    STATE_ORDER,
    amp_brief_for,
    charge_arc,
    charges_for_route,
    character_block,
    character_prefix,
    get_situation,
    is_amp_route,
    load_situations,
    state_block,
    state_for_route,
    used_situations,
)
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.images import _amp_state_for, _charge_for, compose_design_prompt
from chrgd.models import Idea, Slide
from chrgd.pipeline import build_user_message
from chrgd.territories import (
    allocate,
    in_season,
    load_territories,
    scan_brief,
)

LEGACY_AMP = {"mechanic_lock": {"key": "amp_charge_cycle",
                                "name": "Amp's charge cycle",
                                "skeleton": ["a", "b", "c", "d", "e"]}}


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db",
                    CHRGD_OUTPUT_DIR=tmp_path / "out",
                    OPENAI_API_KEY="test-key")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def idea(**route) -> Idea:
    return Idea(idea_id="G1", concept_note="a gym thing",
                route_json=json.dumps(route) if route else None)


# --- AMP: the free state system --------------------------------------------


def test_amp_states_cover_the_range_the_show_needs():
    """D5 — deflated and low one week, beaming and full of colour the next."""
    assert set(STATE_ORDER) == set(AMP_STATES)
    assert {"drained", "flat", "beaming", "smug", "knackered_happy"} <= set(AMP_STATES)
    for spec in AMP_STATES.values():
        assert spec["label"] and spec["body"] and spec["posture"] and spec["mood"]


def test_state_block_falls_back_rather_than_breaking():
    assert "smug" in state_block("smug").lower()
    # a typo in a stored route must never break a paid render
    assert state_block("nonsense") == state_block("charging")


def test_the_show_route_is_recognised_as_amp():
    assert is_amp_route({"show": "amp"})
    assert is_amp_route(LEGACY_AMP)        # the gallery path still works
    assert is_amp_route({"amp": True})     # and the explicit flag
    assert not is_amp_route({"show": "session"})
    assert not is_amp_route(None)


def test_a_free_state_replaces_the_charge_arc():
    """The arc used to be compulsory; under D5 it's one of Amp's spines."""
    assert charges_for_route({"show": "amp", "amp_state": "smug"}, 5) is None
    assert state_for_route({"show": "amp", "amp_state": "smug"}) == "smug"


def test_pre_show_amp_posts_keep_the_rising_arc():
    """The null case for D5: nothing that predates the show changes."""
    assert charges_for_route(LEGACY_AMP, 5) == charge_arc(5)
    assert state_for_route(LEGACY_AMP) == ""


def test_an_unknown_stored_state_falls_back_to_the_arc():
    assert state_for_route({"show": "amp", "amp_state": "nope"}) == ""
    assert charges_for_route({"show": "amp", "amp_state": "nope"}, 4) == charge_arc(4)


def test_the_locked_character_survives_both_spines():
    """Whichever spine runs, Amp has to stay on-model — that's the whole point
    of the locked prefix, and the free state must not have lost it."""
    arc = character_prefix(charge=10)
    free = character_prefix(state="beaming")
    for block in (arc, free):
        assert "CHARACTER (locked" in block
        assert "lightning bolt" in block
        assert "flat vector" in block
    assert "charge meter" in arc          # the cycle keeps its meter
    assert "charge meter" not in free     # the free state has no percentage
    assert character_block(10) == arc     # the old entry point is unchanged


def test_the_renderer_picks_exactly_one_spine():
    slide = Slide(headline="H", image_prompt="a gym")
    brand = load_brand()
    free = idea(show="amp", amp_state="smug")
    assert _amp_state_for(free) == "smug" and _charge_for(free, 0, 5) is None
    legacy = idea(**LEGACY_AMP)
    assert _amp_state_for(legacy) == "" and _charge_for(legacy, 0, 5) is not None
    # and a non-Amp post gets neither
    plain = idea(show="session")
    assert _amp_state_for(plain) == "" and _charge_for(plain, 0, 5) is None
    assert "CHARACTER (locked" not in compose_design_prompt(slide, brand, None)


# --- AMP: the situation bank ------------------------------------------------


def test_the_situation_bank_loads_and_is_usable():
    bank = load_situations()
    assert len(bank) >= 10, "a thin bank is how Amp gets repetitive"
    for sit in bank.values():
        assert sit.label and sit.setup
        assert sit.default_state() in AMP_STATES
        assert all(s in AMP_STATES for s in sit.states)


def test_used_situations_reads_recent_posts(store):
    store.add_idea(Idea(idea_id="G1", concept_note="x",
                        route_json=json.dumps({"show": "amp",
                                               "amp_situation": "broken_lift"})))
    store.add_idea(Idea(idea_id="G2", concept_note="y",
                        route_json=json.dumps({"show": "amp"})))
    assert used_situations(store) == {"broken_lift"}


def test_coverage_is_a_nudge_not_a_rota(store):
    """D12 — the engine marks what's stale and suggests; it never blocks. A
    used situation is still selectable, which is the whole point."""
    store.add_idea(Idea(idea_id="G1", concept_note="x",
                        route_json=json.dumps({"show": "amp",
                                               "amp_situation": "broken_lift"})))
    assert "broken_lift" in used_situations(store)
    assert get_situation("broken_lift") is not None  # still perfectly usable


# --- AMP: the build brief ---------------------------------------------------


def test_the_show_brief_is_situation_led_not_arc_led():
    msg = build_user_message(idea(
        show="amp", amp_state="smug", amp_situation="forgot_kit",
        amp_tip="the one-bag system",
    ))
    assert "THE SITUATION" in msg and "Forgot the kit" in msg
    assert "smug" in msg
    assert "the one-bag system" in msg
    assert "TIP-LED" in msg
    assert "CHARGE ARC" not in msg          # no forced energy curve


def test_a_written_situation_works_without_the_bank():
    msg = build_user_message(idea(show="amp", amp_state="flat",
                                  amp_situation_text="his headphones died"))
    assert "his headphones died" in msg


def test_legacy_amp_posts_keep_the_charge_brief():
    """The null case for the brief."""
    msg = build_user_message(idea(**LEGACY_AMP))
    assert "CHARGE ARC" in msg
    assert "THE SITUATION" not in msg


def test_amp_brief_is_chosen_by_the_route_not_the_caller():
    assert "CHARGE ARC" in amp_brief_for(LEGACY_AMP, 5)
    assert "TIP-LED" in amp_brief_for({"show": "amp", "amp_state": "flat"}, 5)


def test_a_non_amp_post_gets_no_amp_brief():
    assert "AMP POST" not in build_user_message(idea(show="session"))
    assert "AMP POST" not in build_user_message(idea())


# --- LIVE WIRE: the territories ---------------------------------------------


def test_territories_load_with_the_weighting_d9_asked_for():
    terr = load_territories()
    assert {"gym_news", "science"} <= set(terr)
    # D9: gym news and science heaviest
    assert terr["gym_news"].weight >= max(
        t.weight for k, t in terr.items() if k not in ("gym_news", "science")
    )
    for t in terr.values():
        assert t.label and t.probe.strip()


def test_seasonal_territories_drop_out_of_season():
    live_july = {t.key for t in in_season(date(2026, 7, 1))}
    live_nov = {t.key for t in in_season(date(2026, 11, 1))}
    assert "holidays" in live_july and "holidays" not in live_nov
    assert "gym_news" in live_july and "gym_news" in live_nov


@pytest.mark.parametrize("count", [1, 3, 6, 8, 12, 20])
def test_allocation_always_spends_exactly_the_slots_it_was_given(count):
    plan = allocate(count, when=date(2026, 7, 1))
    assert sum(n for _t, n in plan) == count
    assert all(n >= 1 for _t, n in plan)


def test_every_territory_gets_at_least_one_slot_when_there_is_room():
    """A territory rounded out of existence is just a comment in a file."""
    live = in_season(date(2026, 7, 1))
    plan = allocate(len(live) + 4, when=date(2026, 7, 1))
    assert {t.key for t, _n in plan} == {t.key for t in live}


def test_narrowing_to_a_selection_works():
    plan = allocate(4, keys=["science", "football"])
    assert {t.key for t, _n in plan} == {"science", "football"}


def test_an_empty_or_bogus_selection_falls_back_to_everything():
    assert {t.key for t, _n in allocate(6, keys=["nonsense"])} == {
        t.key for t in in_season()[:6]
    }


def test_the_scan_brief_names_the_territories_and_the_trap():
    brief = scan_brief(8, when=date(2026, 7, 1))
    assert "SCAN THESE TERRITORIES" in brief
    assert "Big news in UK gyms" in brief
    # the point of D9: broader than fitness, and explicitly not generic
    assert "isn't about fitness is fine" in brief
    assert "everyone is already posting" in brief


def test_no_territories_means_no_brief():
    """Which is what lets the scout fall back to its old country-wide scan."""
    assert scan_brief(0) == ""


def test_the_scout_only_changes_when_territories_are_passed(settings):
    from chrgd import trends

    seen = {}

    class Fake:
        def search(self, system, ask):
            seen["ask"] = ask
            return json.dumps({"moments": []})

    trends.scout_discover(settings, "trending", 6, client=Fake())
    assert "SCAN THESE TERRITORIES" not in seen["ask"]

    trends.scout_discover(settings, "trending", 6, client=Fake(), territories=[])
    assert "SCAN THESE TERRITORIES" in seen["ask"]

    trends.scout_discover(settings, "trending", 6, client=Fake(),
                          territories=["science"])
    assert "The science" in seen["ask"] and "Football" not in seen["ask"]


def test_the_discover_job_passes_territories_through(store, settings, monkeypatch):
    from chrgd import trends as trends_mod
    from chrgd.worker import _handle_discover, enqueue_discover

    seen = {}

    def fake_scout(s, kind="moments", count=6, client=None,
                   headlines_only=False, territories=None):
        seen["territories"] = territories
        return trends_mod.MomentsResult()

    monkeypatch.setattr(trends_mod, "scout_discover", fake_scout)

    job_id = enqueue_discover(store, "trending", territories=["science"])
    _handle_discover(store, settings, store.get_job(job_id))
    assert seen["territories"] == ["science"]

    # a scan enqueued without territories (every other lane, and anything
    # queued before this shipped) keeps the country-wide behaviour
    _handle_discover(store, settings, {"kind": "moments", "params_json": "{}"})
    assert seen["territories"] is None
