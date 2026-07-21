"""Tests for the slide-1 concept gate — the pre-image quality check.

All offline: the judge and refiner are faked, so no key or spend is needed.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.conceptgate import (
    ConceptGateError,
    gate_slide_one,
    parse_concept_verdict,
)
from chrgd.db import Store
from chrgd.models import Idea
from chrgd.pipeline import LLMResult


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_CONCEPT_GATE_MIN=9,
        CHRGD_CONCEPT_GATE_ROUNDS=3,
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def _idea(store, **over) -> Idea:
    slides = [
        {"headline": "gym stuff", "supporting": "s", "image_prompt": "a gym",
         "visual_intent": "v"},
        {"headline": "h2", "supporting": "s2", "image_prompt": "p2"},
    ]
    idea = Idea(
        idea_id="G-0001", concept_note="rack hoggers", hook="gym stuff",
        slides_json=json.dumps(slides), **over,
    )
    store.add_idea(idea)
    return idea


class FakeJudge:
    """Returns queued verdict JSON payloads; records the prompts it saw."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = 0
        self.seen = []

    def judge(self, system, user):
        self.seen.append(user)
        p = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return p


class FakeRefiner:
    """A ChatClient-shaped fake returning queued slide-1 rewrites."""

    def __init__(self, rewrites):
        self.rewrites = rewrites
        self.calls = 0

    def complete(self, system, user):
        r = self.rewrites[min(self.calls, len(self.rewrites) - 1)]
        self.calls += 1
        return LLMResult(content=json.dumps(r), prompt_tokens=100, completion_tokens=50)


# --- parsing ----------------------------------------------------------------


def test_parse_verdict_clamps_and_strips_fence():
    v = parse_concept_verdict('```json\n{"score": 14, "weakness": "x"}\n```')
    assert v.score == 10
    assert v.weakness == "x"


def test_parse_verdict_bad_text_raises():
    with pytest.raises(ConceptGateError):
        parse_concept_verdict("no json here")


# --- gate behaviour ---------------------------------------------------------


def test_gate_passes_when_score_meets_bar(settings, store):
    idea = _idea(store)
    judge = FakeJudge(['{"score": 9, "weakness": "", "note": "strong"}'])
    result = gate_slide_one(idea, settings, store, judge=judge)
    assert result.passed is True
    assert result.rounds == 1 and result.refined is False
    assert judge.calls == 1
    # The verdict is persisted for the UI / learning loop.
    saved = json.loads(store.get_idea("G-0001").route_json)["concept_gate"]
    assert saved["score"] == 9 and saved["passed"] is True


def test_gate_sharpens_then_passes(settings, store):
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 6, "weakness": "flat hook", "fix_direction": "make it a callout"}',
        '{"score": 10, "weakness": ""}',
    ])
    refiner = FakeRefiner([{
        "headline": "you're the rack hogger, aren't you",
        "supporting": "we all know one",
        "image_prompt": "candid phone flash photo of a bloke asleep on the squat rack",
        "visual_intent": "person, squat rack, phone",
    }])
    result = gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)
    assert result.passed is True
    assert result.rounds == 2 and result.refined is True
    # The sharpened slide 1 (and the aligned hook) is persisted.
    saved = store.get_idea("G-0001")
    slide0 = json.loads(saved.slides_json)[0]
    assert slide0["headline"] == "you're the rack hogger, aren't you"
    assert "candid phone flash photo" in slide0["image_prompt"]
    assert saved.hook == "you're the rack hogger, aren't you"


def test_gate_exhausts_rounds_and_proceeds(settings, store):
    settings.concept_gate_max_rounds = 2
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 6, "weakness": "weak", "fix_direction": "sharpen"}',
        '{"score": 7, "weakness": "still weak"}',
    ])
    refiner = FakeRefiner([{"headline": "sharper attempt", "image_prompt": "native shot"}])
    result = gate_slide_one(idea, settings, store, judge=judge, refiner=refiner)
    # Never cleared the bar, but it refined once and does NOT block the render.
    assert result.passed is False
    assert result.rounds == 2 and result.refined is True
    assert result.score == 7
    saved = json.loads(store.get_idea("G-0001").route_json)["concept_gate"]
    assert saved["passed"] is False and saved["score"] == 7


def test_gate_disabled_skips_entirely(settings, store):
    settings.concept_gate_enabled = False
    idea = _idea(store)
    judge = FakeJudge(['{"score": 3}'])
    result = gate_slide_one(idea, settings, store, judge=judge)
    assert result.skipped is True
    assert judge.calls == 0  # not even constructed/called


def test_gate_survives_a_judge_error(settings, store):
    idea = _idea(store)
    judge = FakeJudge(["not json — the model misbehaved"])
    result = gate_slide_one(idea, settings, store, judge=judge)
    # A gate hiccup must never block the paid render.
    assert result.error is not None
    assert result.passed is False
    # Original slide 1 is untouched.
    assert json.loads(store.get_idea("G-0001").slides_json)[0]["headline"] == "gym stuff"


def test_gate_scores_the_slide_one_concept(settings, store):
    idea = _idea(store)
    judge = FakeJudge(['{"score": 9}'])
    gate_slide_one(idea, settings, store, judge=judge)
    # The headline + visual brief reached the judge.
    assert "gym stuff" in judge.seen[0]
    assert "a gym" in judge.seen[0]


# --- services wiring --------------------------------------------------------


def test_render_idea_runs_gate_before_spending(settings, store, monkeypatch):
    from PIL import Image as PILImage

    from chrgd import conceptgate, images, services
    from chrgd.conceptgate import GateResult

    settings.openai_api_key = "sk-test"
    idea = _idea(store)

    calls = {"gate": 0}

    def fake_gate(i, s, st, **kw):
        calls["gate"] += 1
        return GateResult(idea=i, passed=True, score=9, min_score=9, spend_usd=0.02)

    monkeypatch.setattr(conceptgate, "gate_slide_one", fake_gate)
    monkeypatch.setattr(
        images, "_generate_background",
        lambda *a, **k: PILImage.new("RGB", (100, 150)),
    )
    result = services.render_idea(store, settings, idea, dry_run=False)
    assert calls["gate"] == 1
    # The gate's spend is folded into the render's reported cost (this idea has
    # two slides: slide 1 at high quality, slide 2 at low).
    image = images._IMAGE_COST["high"] + images._IMAGE_COST["low"]
    assert result.spend_usd == pytest.approx(image + 0.02)


def test_render_idea_skips_gate_on_dry_run(settings, store, monkeypatch):
    from chrgd import conceptgate, services

    idea = _idea(store)
    calls = {"gate": 0}
    monkeypatch.setattr(
        conceptgate, "gate_slide_one",
        lambda *a, **k: calls.__setitem__("gate", calls["gate"] + 1),
    )
    services.render_idea(store, settings, idea, dry_run=True)
    assert calls["gate"] == 0
