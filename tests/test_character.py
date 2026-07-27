"""Tests for Amp — the mascot character bible and the charge-cycle arc.

Amp is additive and opt-in: these cover the arc/state maths, the locked prompt
prefix, and (critically) that a non-Amp post's image prompt is completely
unchanged by the hook.
"""

from __future__ import annotations

import json

from chrgd.brand import load_brand
from chrgd.character import (
    MECHANIC_KEY,
    SIGN_OFF,
    CHARGE_STATES,
    build_brief,
    character_block,
    charge_arc,
    charges_for_route,
    is_amp_route,
    state_for_charge,
)
from chrgd.images import compose_design_prompt
from chrgd.mechanics import load_mechanics
from chrgd.models import Idea, Slide


# --- the charge arc ----------------------------------------------------------


def test_arc_rises_monotonically_to_full_charge():
    arc = charge_arc(5)
    assert arc[0] < arc[-1]
    assert arc[-1] == 100  # every post ends fully charged
    assert arc == sorted(arc)  # never dips back down mid-swipe
    assert len(arc) == 5


def test_arc_matches_the_concept_shape_for_four_slides():
    # Drained opener → two charging beats → fully charged payoff.
    assert charge_arc(4) == [10, 40, 70, 100]


def test_arc_edge_cases():
    assert charge_arc(1) == [100]  # a single slide is just the payoff
    assert charge_arc(2) == [10, 100]
    assert charge_arc(0) == [100]  # degenerate input never crashes a render


def test_state_bands():
    assert state_for_charge(0) == "drained"
    assert state_for_charge(10) == "drained"
    assert state_for_charge(50) == "charging"
    assert state_for_charge(100) == "charged"
    # Every band name resolves to a real state block.
    for pct in (0, 25, 45, 85, 100):
        assert state_for_charge(pct) in CHARGE_STATES


# --- the locked character prefix ---------------------------------------------


def test_character_block_locks_identity_and_reflects_charge():
    drained = character_block(10)
    charged = character_block(100)

    # The immutable identity is present at both ends of the arc.
    for block in (drained, charged):
        assert "lightning bolt" in block
        assert "#29C2F2" in block
        assert "locked" in block.lower()
        assert "flat vector" in block

    # …but the state visibly differs.
    assert "drained" in drained and "no glow" in drained
    assert "charged" in charged and "unstoppable" in charged
    assert "10% charge" in drained and "100% charge" in charged
    # The swipe-to-charge retention hook rides on every slide.
    assert "charge meter" in drained


def test_character_block_can_add_the_world():
    assert "The Cell" in character_block(50, include_world=True)
    assert "The Cell" not in character_block(50)


# --- route detection (what makes a post an Amp post) -------------------------


def test_is_amp_route_reads_the_mechanic_lock_and_flag():
    assert is_amp_route({"mechanic_lock": {"key": MECHANIC_KEY}}) is True
    assert is_amp_route({"mechanic": MECHANIC_KEY}) is True
    assert is_amp_route({"amp": True}) is True
    # Everything else is not Amp.
    assert is_amp_route({"mechanic": "myth_fact"}) is False
    assert is_amp_route({}) is False
    assert is_amp_route(None) is False


def test_charges_for_route_is_none_for_normal_posts():
    # The pass-through that keeps every other journey untouched.
    assert charges_for_route({"mechanic": "myth_fact"}, 5) is None
    assert charges_for_route(None, 5) is None


def test_stored_arc_wins_and_is_topped_up():
    route = {"amp": True, "charge_arc": [5, 50]}
    # An editor's stored arc survives…
    assert charges_for_route(route, 2) == [5, 50]
    # …and slides added later are filled in rather than dropped.
    arc = charges_for_route(route, 4)
    assert arc[:2] == [5, 50] and len(arc) == 4


# --- the mechanic is registered and selectable -------------------------------


def test_amp_mechanic_is_available_but_not_default():
    mechanics = load_mechanics()
    assert MECHANIC_KEY in mechanics
    amp = mechanics[MECHANIC_KEY]
    assert amp.skeleton, "the charge cycle needs a slide skeleton"
    # The arc's shape is baked into the skeleton: drained opener, charged close.
    assert "drain" in amp.skeleton[0].lower()
    assert SIGN_OFF.rstrip(".").lower() in amp.skeleton[-1].lower()


# --- the render hook ---------------------------------------------------------


def _slide():
    return Slide(headline="3pm and I'm done", supporting="every single day",
                 image_prompt="a desk at 3pm", visual_intent="slumped")


def test_amp_prompt_leads_with_the_locked_character():
    brand = load_brand()
    prompt = compose_design_prompt(_slide(), brand, None, charge=10)
    # The character block leads, so it outranks the scene brief that follows.
    assert prompt.startswith("CHARACTER (locked")
    assert "lightning bolt" in prompt
    assert "10% charge" in prompt
    # The slide's own brief still makes it in — Amp adds, it doesn't replace.
    assert "a desk at 3pm" in prompt
    assert "3pm and I'm done" in prompt


def test_non_amp_prompt_is_completely_unchanged():
    """The hook must be invisible to every other journey."""
    brand = load_brand()
    without = compose_design_prompt(_slide(), brand, None)
    assert "Amp" not in without
    assert "charge" not in without.lower()
    # Explicitly passing no charge is identical to not passing one at all.
    assert compose_design_prompt(_slide(), brand, None, charge=None) == without


def test_route_from_the_real_create_flow_is_detected():
    """Regression: the create journey stores the mechanic's LABEL as `name`.

    Detection originally keyed only on the mechanic key, so a post made through
    the UI got Amp's skeleton but no charge states and no locked character —
    the journey silently half-worked. Both spellings must match.
    """
    from chrgd.character import MECHANIC_LABEL

    as_stored_by_the_ui = {
        "mechanic_lock": {"key": MECHANIC_KEY, "name": MECHANIC_LABEL,
                          "skeleton": ["a", "b"]},
    }
    assert is_amp_route(as_stored_by_the_ui) is True
    # An idea created BEFORE the key was stored carries the label alone.
    assert is_amp_route({"mechanic_lock": {"name": MECHANIC_LABEL}}) is True
    # A different mechanic stored the same way is still not Amp.
    assert is_amp_route({"mechanic_lock": {"key": "myth_fact",
                                           "name": "Myth vs fact"}}) is False


def test_build_brief_makes_the_cycle_the_story():
    brief = build_brief(5)
    assert "Amp" in brief
    assert SIGN_OFF in brief             # the sign-off is mandated
    assert "10%" in brief and "100%" in brief  # the arc is spelled out
    assert "drain" in brief.lower()
    assert "dry British humour" in brief  # the voice, not a brand voice doing fun


def test_build_prompt_carries_amp_only_for_amp_posts():
    from chrgd.character import MECHANIC_LABEL
    from chrgd.pipeline import build_user_message

    amp = Idea(idea_id="G-1", concept_note="3pm slump", route_json=json.dumps(
        {"mechanic_lock": {"key": MECHANIC_KEY, "name": MECHANIC_LABEL,
                           "skeleton": ["drain", "why", "fix", "payoff"]}}))
    msg = build_user_message(amp)
    assert "AMP POST" in msg
    assert SIGN_OFF in msg

    # Every other mechanic is untouched.
    normal = Idea(idea_id="G-2", concept_note="x", route_json=json.dumps(
        {"mechanic_lock": {"key": "myth_fact", "name": "Myth vs fact",
                           "skeleton": ["a", "b"]}}))
    other = build_user_message(normal)
    assert "AMP POST" not in other and "Amp" not in other


def test_charge_threading_from_an_idea_route():
    from chrgd.images import _charge_for

    amp = Idea(idea_id="G-1", concept_note="x",
               route_json=json.dumps({"amp": True}))
    assert _charge_for(amp, 0, 4) == 10     # opens drained
    assert _charge_for(amp, 3, 4) == 100    # closes charged

    normal = Idea(idea_id="G-2", concept_note="x",
                  route_json=json.dumps({"mechanic": "myth_fact"}))
    assert _charge_for(normal, 0, 4) is None

    # No route at all → no charge, no behaviour change.
    assert _charge_for(Idea(idea_id="G-3", concept_note="x"), 0, 4) is None


# --- end-to-end through the real create flow ---------------------------------


def test_create_amp_post_end_to_end(tmp_path):
    """The journey as the editor actually uses it: pick Amp in the gallery →
    the idea carries the charge arc → the detail endpoint exposes the rail."""
    from fastapi.testclient import TestClient

    from chrgd.config import Settings
    from chrgd.db import Store
    from chrgd.webapp import create_app

    settings = Settings(
        CHRGD_DB_PATH=tmp_path / "t.db", CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_WEB_USERNAME="admin", CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "s3cret"})

    # Pick Amp from the blank-canvas gallery.
    r = client.post("/api/create/start",
                    data={"mode": "blank", "manual": "true",
                          "mechanic": MECHANIC_KEY})
    assert r.status_code == 200
    idea_id = r.json()["idea_id"]

    with Store(settings.db_path) as store:
        route = json.loads(store.get_idea(idea_id).route_json)
    # The key is stored, so the post is recognisable as Amp downstream.
    assert route["mechanic_lock"]["key"] == MECHANIC_KEY
    assert is_amp_route(route) is True

    # The detail endpoint hands the review UI a full rising charge rail.
    d = client.get(f"/api/ideas/{idea_id}/detail").json()
    arc = d["charge_arc"]
    assert arc and arc[-1] == 100 and arc == sorted(arc)
    assert len(arc) == len(d["slides"])


def test_detail_charge_arc_empty_for_normal_posts(tmp_path):
    from fastapi.testclient import TestClient

    from chrgd.config import Settings
    from chrgd.webapp import create_app

    settings = Settings(
        CHRGD_DB_PATH=tmp_path / "t2.db", CHRGD_OUTPUT_DIR=tmp_path / "out2",
        CHRGD_WEB_USERNAME="admin", CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    r = client.post("/api/create/start",
                    data={"mode": "blank", "manual": "true",
                          "mechanic": "myth_fact"})
    d = client.get(f"/api/ideas/{r.json()['idea_id']}/detail").json()
    assert d["charge_arc"] == []  # the rail stays hidden on every other post
