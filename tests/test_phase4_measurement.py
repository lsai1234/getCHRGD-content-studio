"""Phase 4 — per-show measurement, and the AMP video pilot.

The scoreboard's whole job is to answer "which of the five is working" honestly,
including the answer "not enough data yet". Most of what's tested here is that
it refuses to claim a finding it hasn't earned — a confident median over two
posts is worse than no number at all, because it gets acted on.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.learning import (
    METRIC_FIELDS,
    MIN_POSTS_FOR_VERDICT,
    SKEW_TRAITS,
    _traits,
    insights,
    show_scoreboard,
)
from chrgd.models import Idea
from chrgd.shows import load_shows
from chrgd.video import motion_prompt_for_slide, video_pilot_show


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db",
                    CHRGD_OUTPUT_DIR=tmp_path / "out")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def logged(store, idea_id: str, show: str, *, views=1000, rating=None, **metrics):
    store.add_idea(Idea(idea_id=idea_id, concept_note="x", hook=f"hook {idea_id}",
                        route_json=json.dumps({"show": show})))
    patch = {"views": views, **metrics}
    if rating:
        patch["rating"] = rating
    store.merge_metrics(idea_id, patch)


# --- every show can actually be measured ------------------------------------


def test_every_show_declares_a_measurable_kpi():
    """`kpi` is a sentence and can't be ranked; `kpi_metric` is the number."""
    for show in load_shows().values():
        assert show.kpi, f"{show.key} has no stated KPI"
        assert show.kpi_metric in METRIC_FIELDS, (
            f"{show.key}'s kpi_metric {show.kpi_metric!r} isn't a logged metric"
        )


def test_the_kpis_match_what_each_show_is_built_for():
    shows = load_shows()
    assert shows["amp"].kpi_metric == "shares"
    assert shows["session"].kpi_metric == "saves"
    assert shows["straight_up"].kpi_metric == "saves"
    assert shows["multiverse"].kpi_metric == "comments"
    assert shows["live_wire"].kpi_metric == "comments"


def test_show_is_a_trait_the_learning_loop_compares_on():
    assert "show" in SKEW_TRAITS
    idea = Idea(idea_id="G1", route_json=json.dumps({"show": "amp"}))
    assert _traits(idea)["show"] == "amp"
    assert _traits(Idea(idea_id="G2"))["show"] == ""


# --- the scoreboard ---------------------------------------------------------


def test_an_empty_scoreboard_says_nothing_is_logged(store):
    board = show_scoreboard(store)
    assert board["posts_logged"] == 0
    assert len(board["shows"]) == len(load_shows())
    assert all(r["verdict"] == "no posts logged yet" for r in board["shows"])
    assert all(r["posts"] == 0 for r in board["shows"])


def test_a_show_is_scored_on_its_own_kpi_not_on_views(store):
    """THE SESSION is built for saves. A save-heavy show would look mediocre
    ranked on views while doing exactly its job."""
    for i in range(4):
        logged(store, f"S{i}", "session", views=500, saves=100, shares=1)
    row = next(r for r in show_scoreboard(store)["shows"] if r["show"] == "session")
    assert row["kpi_metric"] == "saves"
    assert row["median_kpi"] == 100
    assert row["kpi_per_1k_views"] == 200.0     # 100 saves per 500 views
    assert "saves per 1k views" in row["verdict"]


def test_the_kpi_rate_is_per_view_so_reach_luck_does_not_win(store):
    """Two shows with the same rate should read the same, even if one got ten
    times the reach on a fluke."""
    for i in range(4):
        logged(store, f"A{i}", "amp", views=1000, shares=50)
        logged(store, f"M{i}", "multiverse", views=10000, comments=50)
    rows = {r["show"]: r for r in show_scoreboard(store)["shows"]}
    assert rows["amp"]["kpi_per_1k_views"] == 50.0
    assert rows["multiverse"]["kpi_per_1k_views"] == 5.0   # same count, 10x reach


def test_a_thin_show_refuses_to_report_a_finding(store):
    """A confident number over two posts is worse than none — it gets acted on."""
    logged(store, "A1", "amp", views=1000, shares=90)
    logged(store, "A2", "amp", views=1000, shares=90)
    row = next(r for r in show_scoreboard(store)["shows"] if r["show"] == "amp")
    assert row["posts"] == 2
    assert "2 more before this means anything" in row["verdict"]
    assert "per 1k views" not in row["verdict"]


def test_an_undecided_show_never_outranks_a_decided_one(store):
    """Sorting on rate alone would float a lucky single post to the top."""
    logged(store, "A1", "amp", views=100, shares=99)          # freak result, n=1
    for i in range(4):
        logged(store, f"S{i}", "session", views=1000, saves=100)
    order = [r["show"] for r in show_scoreboard(store)["shows"] if r["posts"]]
    assert order.index("session") < order.index("amp")


def test_the_verdict_reads_reach_against_the_account_baseline(store):
    for i in range(4):
        logged(store, f"A{i}", "amp", views=5000, shares=10)
    for i in range(4):
        logged(store, f"S{i}", "session", views=200, saves=10)
    rows = {r["show"]: r for r in show_scoreboard(store)["shows"]}
    assert "above the account's median" in rows["amp"]["verdict"]
    assert "below the account's median" in rows["session"]["verdict"]


def test_ratings_feed_the_verdict(store):
    for i in range(4):
        logged(store, f"M{i}", "multiverse", views=1000, comments=5, rating="flop")
    row = next(r for r in show_scoreboard(store)["shows"] if r["show"] == "multiverse")
    assert row["ratings"]["flop"] == 4
    assert "worth a hard look" in row["verdict"]


def test_the_best_post_is_the_best_on_the_shows_kpi(store):
    logged(store, "A1", "amp", views=9000, shares=1)     # most views
    logged(store, "A2", "amp", views=100, shares=80)     # most shares
    for i in range(2):
        logged(store, f"A{i+3}", "amp", views=500, shares=5)
    row = next(r for r in show_scoreboard(store)["shows"] if r["show"] == "amp")
    assert row["best"]["idea_id"] == "A2"


def test_off_format_posts_are_not_attributed_to_any_show(store):
    store.add_idea(Idea(idea_id="X1", concept_note="x"))
    store.merge_metrics("X1", {"views": 5000, "shares": 500})
    board = show_scoreboard(store)
    assert board["posts_logged"] == 1        # it counts toward the baseline
    assert all(r["posts"] == 0 for r in board["shows"])   # but to no show


def test_posts_without_views_are_ignored(store):
    logged(store, "A1", "amp", views=0, shares=10)
    assert show_scoreboard(store)["posts_logged"] == 0


def test_the_existing_insights_digest_still_works(store):
    """Phase 4 added a trait to the shared extractor — the old digest that
    reads it must not have broken."""
    for i in range(3):
        logged(store, f"A{i}", "amp", views=1000 + i, shares=10, rating="hit")
    digest = insights(store)
    assert digest["posts_logged"] == 3
    assert digest["baseline_views"] > 0
    assert any(t["trait"] == "show" and t["value"] == "amp"
               for t in digest["traits"])


# --- the AMP video pilot ----------------------------------------------------


def test_amp_is_the_only_video_pilot():
    """D5 — flat vector animates far better than photoreal, and a wrong frame
    costs a laugh rather than a likeness problem."""
    pilots = [s.key for s in load_shows().values() if s.video_pilot]
    assert pilots == ["amp"]


def test_video_pilot_show_is_a_pass_through_for_everything_else():
    def idea(**route):
        return Idea(idea_id="G1",
                    route_json=json.dumps(route) if route else None)

    assert video_pilot_show(idea(show="amp")).key == "amp"
    assert video_pilot_show(idea(show="multiverse")) is None
    assert video_pilot_show(idea()) is None


def test_the_generic_motion_brief_is_unchanged():
    """The null case: a non-pilot post animates exactly as it did before."""
    prompt = motion_prompt_for_slide(0, 5)
    assert "push-in" in prompt and "parallax" in prompt
    assert "FLAT VECTOR" not in prompt


def test_a_cartoon_moves_the_character_not_the_camera():
    """A slow dolly across a flat vector frame just looks like a still image
    being zoomed — which is the failure this exists to avoid."""
    prompt = motion_prompt_for_slide(0, 5, amp_state="wired")
    assert "FLAT VECTOR" in prompt
    assert "camera stays still" in prompt
    # the camera moves are explicitly ruled OUT, not merely absent
    assert "no dolly, no parallax" in prompt.lower()
    assert "no photoreal lighting" in prompt.lower()
    assert "vibrates" in prompt          # the state's own motion


@pytest.mark.parametrize("state", [
    "drained", "flat", "wired", "charging", "beaming", "charged",
    "smug", "knackered_happy",
])
def test_every_amp_state_has_its_own_motion(state):
    from chrgd.character import AMP_STATES

    assert state in AMP_STATES
    prompt = motion_prompt_for_slide(1, 5, amp_state=state)
    assert "Amp" in prompt and "FLAT VECTOR" in prompt


def test_an_unknown_state_still_produces_a_usable_brief():
    prompt = motion_prompt_for_slide(1, 5, amp_state="nonsense")
    assert "FLAT VECTOR" in prompt and "Amp" in prompt


def test_the_pilot_brief_keeps_the_artwork_and_the_text_locked():
    """Whatever else changes, the clip must not redraw or re-letter the frame."""
    for prompt in (motion_prompt_for_slide(0, 5),
                   motion_prompt_for_slide(0, 5, amp_state="smug")):
        assert "exactly as-is" in prompt
        assert "do not" in prompt.lower()
