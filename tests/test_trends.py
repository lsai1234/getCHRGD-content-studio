"""Tests for Milestone 5 / B1: the trend scout (offline)."""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import DecaySpeed, Status
from chrgd.trends import (
    LIMITATION,
    Trend,
    TrendError,
    parse_trends,
    scout_trends,
    seed_trends,
)


def _payload(n=2):
    return json.dumps(
        {
            "limitation": LIMITATION,
            "trends": [
                {
                    "trend": f"heatwave gym hook {i}",
                    "why_now": "it's 30C and the gym has no aircon",
                    "decay_speed": "days" if i == 0 else "weeks",
                    "mechanic": "rage_agreement",
                    "visual_engine": "real_gym_micro_scene",
                    "brand_fit": "ok",
                    "claim_safety": "ok",
                    "concept_note": f"the one fan everyone fights over, day {i}",
                    "target_viewer": "lads who train after work",
                    "pain_point": "sweating buckets",
                    "core_tension": "one fan, twenty people",
                }
                for i in range(n)
            ],
        }
    )


class FakeSearch:
    def __init__(self, text):
        self.text = text

    def search(self, system, user):
        return self.text


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db")


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


# --- parsing ----------------------------------------------------------------


def test_parse_plain_json():
    res = parse_trends(_payload(2))
    assert len(res.trends) == 2
    assert res.limitation == LIMITATION


def test_parse_strips_markdown_fence():
    res = parse_trends("```json\n" + _payload(1) + "\n```")
    assert len(res.trends) == 1


def test_parse_bad_text_raises():
    with pytest.raises(TrendError):
        parse_trends("no json here")


# --- scouting ---------------------------------------------------------------


def test_scout_returns_trends(settings):
    res = scout_trends(settings, count=2, client=FakeSearch(_payload(2)))
    assert len(res.trends) == 2
    assert res.trends[0].ships_fast  # first has decay_speed=days


def test_scout_caps_count(settings):
    res = scout_trends(settings, count=1, client=FakeSearch(_payload(3)))
    assert len(res.trends) == 1


def test_scout_states_limitation(settings):
    res = scout_trends(settings, count=1, client=FakeSearch(_payload(1)))
    assert "TikTok" in res.limitation


# --- seeding ----------------------------------------------------------------


def test_seed_creates_rows_with_priority_and_decay(store, settings):
    res = scout_trends(settings, count=2, client=FakeSearch(_payload(2)))
    outcome = seed_trends(store, settings, res.trends)
    assert len(outcome.created) == 2
    fast, slow = outcome.created
    assert fast.decay_speed is DecaySpeed.days
    assert fast.priority == 1  # ship-fast
    assert slow.priority == 2
    assert all(i.content_category == "trend" for i in outcome.created)
    assert all(i.status is Status.queued for i in outcome.created)


def test_seed_dedupes(store, settings):
    trends = scout_trends(settings, count=2, client=FakeSearch(_payload(2))).trends
    seed_trends(store, settings, trends)
    outcome = seed_trends(store, settings, trends)  # again
    assert outcome.created == []
    assert len(outcome.skipped) == 2


def test_seed_ids_continue_sequence(store, settings):
    from chrgd.models import Idea

    store.add_idea(Idea(idea_id="G-0005", concept_note="existing"))
    trends = scout_trends(settings, count=1, client=FakeSearch(_payload(1))).trends
    outcome = seed_trends(store, settings, trends)
    assert outcome.created[0].idea_id == "G-0006"


def test_trend_model_defaults():
    t = Trend(trend="x", concept_note="y")
    assert t.decay_speed is DecaySpeed.weeks
    assert not t.ships_fast
