"""Spend behaviour — what the engine is charged for, and what it stops paying for.

Two separate levers, both tested here:

* **Prompt weight.** Every reasoning call carried the full ~9,000-token carousel
  engine. Calls that rewrite one headline for a post that is already written
  need the brand's voice, not the doctrine, and were being billed for the rest
  on every attempt.
* **Wasted rounds.** The gate's sharpening loop kept whatever the last rewrite
  produced, even when the judge scored it lower than the version it replaced —
  paying for a creative call and a judge call to end up with a worse opener.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.conceptgate import gate_slide_one
from chrgd.db import Store
from chrgd.models import Idea
from chrgd.pipeline import LLMResult, engine_base, voice_base


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_CONCEPT_GATE_MIN=9,
        CHRGD_CONCEPT_GATE_ROUNDS=3,
        CHRGD_CONCEPT_TOURNAMENT=False,
        CHRGD_CONCEPT_GLANCE=False,
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def _idea(store) -> Idea:
    slides = [
        {"headline": "gym stuff", "supporting": "s", "image_prompt": "a gym",
         "visual_intent": "v"},
        {"headline": "h2", "supporting": "s2", "image_prompt": "p2"},
    ]
    idea = Idea(idea_id="G-0001", concept_note="rack hoggers", hook="gym stuff",
                slides_json=json.dumps(slides))
    store.add_idea(idea)
    return idea


class RecordingClient:
    """A ChatClient-shaped fake that keeps the system prompts it was sent."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = 0
        self.systems: list[str] = []

    def complete(self, system, user):
        self.systems.append(system)
        p = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return LLMResult(content=json.dumps(p), prompt_tokens=100, completion_tokens=50)


class FakeJudge:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = 0

    def judge(self, system, user):
        p = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return p


# --- prompt weight -----------------------------------------------------------


def test_voice_base_is_a_fraction_of_the_engine():
    assert len(voice_base()) < len(engine_base()) / 3


def test_voice_base_keeps_the_brand_and_drops_the_doctrine():
    voice = voice_base()
    assert "CHRGD" in voice
    # The six-stage carousel doctrine and the shapes library belong to calls
    # that are actually writing a post.
    assert "VIRAL PLAYBOOK" not in voice


def test_prompt_assembly_is_cached():
    assert engine_base() is engine_base()
    assert voice_base() is voice_base()


def test_sharpening_a_headline_does_not_buy_the_whole_engine(settings, store):
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 6, "weakness": "flat", "fix_direction": "callout"}',
        '{"score": 10, "weakness": ""}',
    ])
    refiner = RecordingClient([{"headline": "sharper", "image_prompt": "native shot"}])

    gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)

    assert refiner.calls == 1
    assert len(refiner.systems[0]) < len(engine_base()) / 2


# --- wasted rounds -----------------------------------------------------------


def test_a_rewrite_that_scores_worse_is_thrown_away(settings, store):
    """The old loop rendered the last rewrite even when it scored lower."""
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 7, "weakness": "flat", "fix_direction": "sharpen"}',
        '{"score": 4, "weakness": "worse"}',
    ])
    refiner = RecordingClient([{"headline": "a worse opener", "image_prompt": "x"}])

    result = gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)

    # The better of the two is what gets rendered and recorded.
    assert result.score == 7
    saved = store.get_idea("G-0001")
    assert json.loads(saved.slides_json)[0]["headline"] == "gym stuff"
    assert saved.hook == "gym stuff"


def test_a_failed_round_ends_the_loop_instead_of_paying_for_another(settings, store):
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 7, "weakness": "flat", "fix_direction": "sharpen"}',
        '{"score": 5, "weakness": "worse"}',
        '{"score": 10, "weakness": ""}',  # never reached
    ])
    refiner = RecordingClient([{"headline": "worse", "image_prompt": "x"}])

    result = gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)

    assert judge.calls == 2          # not the three rounds it was allowed
    assert refiner.calls == 1        # only one creative call was bought
    assert result.passed is False


def test_an_improving_round_still_gets_its_next_pass(settings, store):
    """Stopping early must not stop a loop that is actually working."""
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 5, "weakness": "flat", "fix_direction": "sharpen"}',
        '{"score": 7, "weakness": "closer", "fix_direction": "sharpen again"}',
        '{"score": 9, "weakness": ""}',
    ])
    refiner = RecordingClient([
        {"headline": "better", "image_prompt": "x"},
        {"headline": "best", "image_prompt": "y"},
    ])

    result = gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)

    assert result.passed is True
    assert result.rounds == 3
    assert json.loads(store.get_idea("G-0001").slides_json)[0]["headline"] == "best"


# --- visibility --------------------------------------------------------------


def test_spend_is_grouped_by_stage(store):
    for command, spend in (("build", 0.40), ("build", 0.60), ("render", 0.25)):
        run_id = store.start_run(command)
        store.finish_run(run_id, spend_usd=spend)
    store.finish_run(store.start_run("takes"), spend_usd=0.0)  # free runs excluded

    rows = store.spend_by_command()

    assert [r["command"] for r in rows] == ["build", "render"]
    assert rows[0]["runs"] == 2
    assert rows[0]["spend"] == pytest.approx(1.0)


def test_the_cheaper_defaults_ship(tmp_path):
    shipped = Settings(CHRGD_DB_PATH=tmp_path / "t.db")
    assert shipped.concept_gate_max_rounds == 2
    assert shipped.concept_candidates == 3
