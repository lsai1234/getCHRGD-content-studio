"""Tests for the slide-1 concept gate — the pre-image quality check.

All offline: the judge and the creative client are faked, so no key or spend is
needed.

Two fixtures, deliberately: `settings` runs the score→sharpen loop alone (so
those tests assert on that loop and nothing else), and `full_settings` turns on
the opener tournament and the glance test that wrap it in production.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.conceptgate import (
    ConceptGateError,
    TournamentVerdict,
    _resolve_winner,
    gate_slide_one,
    needs_gate,
    parse_concept_verdict,
    parse_glance_verdict,
    parse_tournament_verdict,
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
        CHRGD_CONCEPT_TOURNAMENT=False,
        CHRGD_CONCEPT_GLANCE=False,
    )


@pytest.fixture()
def full_settings(settings):
    """The gate as it ships: tournament + score loop + glance test."""
    settings.concept_tournament_enabled = True
    settings.concept_candidates = 2
    settings.concept_glance_test = True
    return settings


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
    """A ChatClient-shaped fake returning queued JSON payloads.

    Serves both creative calls the gate makes: the rival-opener invention
    (`{"candidates": [...]}`) and the slide-1 rewrites.
    """

    def __init__(self, rewrites):
        self.rewrites = rewrites
        self.calls = 0
        self.seen = []

    def complete(self, system, user):
        self.seen.append(user)
        r = self.rewrites[min(self.calls, len(self.rewrites) - 1)]
        self.calls += 1
        return LLMResult(content=json.dumps(r), prompt_tokens=100, completion_tokens=50)


def _rivals(*headlines):
    """A `{"candidates": [...]}` payload for the rival-opener call."""
    return {
        "candidates": [
            {
                "headline": h,
                "supporting": "",
                "image_prompt": f"candid phone photo — {h}",
                "visual_intent": "person, gym, prop",
                "angle": f"angle {i}",
                "question": f"what happened with {h}?",
                "why_not_obvious": "nobody else has this detail",
            }
            for i, h in enumerate(headlines)
        ]
    }


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


# --- stage A/B: the opener tournament ---------------------------------------


def test_tournament_swaps_in_a_sharper_opener(full_settings, store):
    idea = _idea(store)
    creative = FakeRefiner([_rivals("the £40 tub in your mate's boot", "rival two")])
    judge = FakeJudge([
        # the head-to-head: candidate 1 (the first rival) wins
        json.dumps({
            "winner": 1, "why": "only one that plants a real question",
            "ranking": [
                {"index": 0, "intrigue": 4, "originality": 2, "instant": 7,
                 "verdict": "every gym account has opened with this"},
                {"index": 1, "intrigue": 9, "originality": 9, "instant": 8,
                 "verdict": "you need to know what's in the boot"},
                {"index": 2, "intrigue": 5, "originality": 4, "instant": 6,
                 "verdict": "fine, forgettable"},
            ],
        }),
        '{"score": 9, "weakness": "", "note": "strong"}',
        '{"took_away": "someone selling protein out of a car", "stops": true}',
    ])
    result = gate_slide_one(
        idea, full_settings, store, judge=judge, refiner=creative
    )
    assert result.swapped is True
    assert len(result.candidates) == 3  # the built opener + two rivals
    assert result.tournament.winner == 1
    # The winning opener is what gets rendered, and the hook follows it.
    saved = store.get_idea("G-0001")
    slide0 = json.loads(saved.slides_json)[0]
    assert slide0["headline"] == "the £40 tub in your mate's boot"
    assert saved.hook == "the £40 tub in your mate's boot"
    # ...and the field is recorded for the editor.
    cg = json.loads(saved.route_json)["concept_gate"]
    assert cg["tournament"]["candidates"] == 3
    assert cg["tournament"]["swapped"] is True
    assert "every gym account" in cg["tournament"]["beaten"][0]["verdict"]


def test_tournament_keeps_the_built_opener_when_it_wins(full_settings, store):
    idea = _idea(store)
    creative = FakeRefiner([_rivals("weaker one", "weaker two")])
    judge = FakeJudge([
        '{"winner": 0, "why": "the built one is genuinely the sharpest here"}',
        '{"score": 9}',
        '{"took_away": "rack hoggers", "stops": true}',
    ])
    result = gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    assert result.swapped is False
    assert json.loads(store.get_idea("G-0001").slides_json)[0]["headline"] == "gym stuff"


def test_tournament_failure_still_lets_the_score_loop_run(full_settings, store):
    """A field we couldn't invent is no reason to skip the check we can do."""
    idea = _idea(store)
    creative = FakeRefiner([{"nope": "no candidates key"}])
    judge = FakeJudge([
        '{"score": 9, "note": "fine"}',
        '{"took_away": "rack hoggers", "stops": true}',
    ])
    result = gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    assert result.error is not None       # the tournament failure is reported
    assert result.passed is True          # but the concept was still scored
    assert result.score == 9


def test_rivals_prompt_carries_the_next_slide(full_settings, store):
    """The opener has to set up slide 2 — so the inventor is shown slide 2."""
    idea = _idea(store)
    creative = FakeRefiner([_rivals("a rival")])
    judge = FakeJudge([
        '{"winner": 0}', '{"score": 9}', '{"took_away": "x", "stops": true}',
    ])
    gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    assert "h2" in creative.seen[0]
    assert "MECHANIC MENU" in creative.seen[0]


def test_resolve_winner_falls_back_on_a_bogus_index():
    """A malformed index must not silently hand the render to candidate 0."""
    v = TournamentVerdict(
        winner=9,
        ranking=[
            {"index": 0, "intrigue": 3, "originality": 3, "instant": 3},
            {"index": 1, "intrigue": 9, "originality": 8, "instant": 9},
        ],
    )
    assert _resolve_winner(v, 2) == 1
    # Nothing usable at all → the built opener.
    assert _resolve_winner(TournamentVerdict(winner=9), 2) == 0


def test_parse_tournament_verdict_clamps_scores():
    v = parse_tournament_verdict(
        '{"winner": 1, "ranking": [{"index": 0, "intrigue": 44, "instant": -3}]}'
    )
    assert v.ranking[0].intrigue == 10 and v.ranking[0].instant == 0


def test_tournament_disabled_skips_the_field(settings, store):
    idea = _idea(store)
    creative = FakeRefiner([_rivals("never asked for")])
    judge = FakeJudge(['{"score": 9}'])
    result = gate_slide_one(idea, settings, store, judge=judge, refiner=creative)
    assert result.candidates == [] and result.tournament is None
    assert creative.calls == 0


# --- stage D: the fraction-of-a-second glance test ---------------------------


def test_glance_failure_triggers_one_targeted_fix(full_settings, store):
    full_settings.concept_tournament_enabled = False
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 9, "note": "clever"}',
        # ...but at glance speed it's mush.
        json.dumps({
            "took_away": "something about the gym", "question": "", "stops": False,
            "fix": "cut it to four words and put the number in the picture",
        }),
        '{"took_away": "someone hogged the rack for 40 minutes", "stops": true}',
    ])
    creative = FakeRefiner([{
        "headline": "40 minutes. one rack.",
        "supporting": "",
        "image_prompt": "candid phone photo of a timer next to a squat rack",
        "visual_intent": "timer, rack",
    }])
    result = gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    assert result.glance_fixed is True
    assert result.glance.stops is True
    saved = store.get_idea("G-0001")
    assert json.loads(saved.slides_json)[0]["headline"] == "40 minutes. one rack."
    assert saved.hook == "40 minutes. one rack."
    cg = json.loads(saved.route_json)["concept_gate"]
    assert cg["glance"]["stops"] is True and cg["glance"]["fixed"] is True


def test_glance_fix_happens_at_most_once(full_settings, store):
    """A second failure is reported honestly, not looped on."""
    full_settings.concept_tournament_enabled = False
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 9}',
        '{"took_away": "mush", "stops": false, "fix": "sharpen"}',
        '{"took_away": "still mush", "stops": false, "fix": "sharpen again"}',
    ])
    creative = FakeRefiner([{"headline": "second attempt", "image_prompt": "native shot"}])
    result = gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    assert result.glance_fixed is True
    assert result.glance.stops is False        # honest about still failing
    assert creative.calls == 1                 # exactly one fix pass
    assert json.loads(store.get_idea("G-0001").route_json)["concept_gate"]["glance"][
        "stops"
    ] is False


def test_glance_sees_only_what_a_scroller_sees(full_settings, store):
    """The glance payload withholds the reasoning — that asymmetry IS the test."""
    full_settings.concept_tournament_enabled = False
    idea = _idea(store, target_viewer="lads who train at 6pm")
    judge = FakeJudge(['{"score": 9}', '{"took_away": "gym", "stops": true}'])
    gate_slide_one(idea, full_settings, store, judge=judge)
    glance_prompt = judge.seen[-1]
    assert "gym stuff" in glance_prompt            # the words on the image
    assert "rack hoggers" not in glance_prompt     # ...but not the topic
    assert "lads who train at 6pm" not in glance_prompt  # ...nor the intent


def test_glance_failure_never_blocks_the_render(full_settings, store):
    full_settings.concept_tournament_enabled = False
    idea = _idea(store)
    judge = FakeJudge(['{"score": 9}', "the model returned prose"])
    result = gate_slide_one(idea, full_settings, store, judge=judge)
    assert result.error is not None
    assert result.passed is True   # the concept still cleared the bar


def test_parse_glance_verdict_coerces_a_string_bool():
    assert parse_glance_verdict('{"stops": "yes"}').stops is True
    assert parse_glance_verdict('{"stops": "no"}').stops is False


def test_sub_scores_are_persisted_for_the_editor(settings, store):
    idea = _idea(store)
    judge = FakeJudge([
        '{"score": 9, "intrigue": 9, "originality": 8, "instant": 10, "note": "n"}'
    ])
    gate_slide_one(idea, settings, store, judge=judge)
    cg = json.loads(store.get_idea("G-0001").route_json)["concept_gate"]
    assert cg["intrigue"] == 9 and cg["originality"] == 8 and cg["instant"] == 10


# --- the live stage log ------------------------------------------------------


def test_stage_log_streams_every_check(full_settings, store):
    """The editor watches the four checks happen — each one reports twice."""
    idea = _idea(store)
    creative = FakeRefiner([_rivals("a rival", "another")])
    judge = FakeJudge([
        '{"winner": 0, "why": "the built one holds"}',
        '{"score": 9, "note": "strong"}',
        '{"took_away": "rack hoggers", "stops": true}',
    ])
    seen = []
    result = gate_slide_one(
        idea, full_settings, store, judge=judge, refiner=creative,
        on_stage=lambda stages: seen.append([(s["key"], s["state"]) for s in stages]),
    )
    # Every stage ran and finished, in order.
    assert [(s["key"], s["state"]) for s in result.stages] == [
        ("rivals", "done"), ("tournament", "done"), ("score", "done"), ("glance", "done"),
    ]
    # ...and the UI saw it progress, not just the end state.
    assert ("rivals", "running") in seen[0]
    assert len(seen) > len(result.stages)
    # Each carries the label the UI shows and a human note.
    assert result.stages[0]["label"] == "Inventing rival openers"
    assert "9" in result.stages[2]["note"]


def test_stage_log_marks_disabled_stages_off(settings, store):
    idea = _idea(store)
    gate_slide_one(idea, settings, store, judge=FakeJudge(['{"score": 9}']))
    saved = json.loads(store.get_idea("G-0001").route_json)["concept_gate"]
    states = {s["key"]: s["state"] for s in saved["stages"]}
    assert states == {"rivals": "off", "tournament": "off", "score": "done", "glance": "off"}


def test_stage_log_reports_a_failed_stage(full_settings, store):
    idea = _idea(store)
    creative = FakeRefiner([{"not": "candidates"}])
    judge = FakeJudge(['{"score": 9}', '{"took_away": "x", "stops": true}'])
    result = gate_slide_one(idea, full_settings, store, judge=judge, refiner=creative)
    states = {s["key"]: s["state"] for s in result.stages}
    assert states["tournament"] == "failed"
    assert states["score"] == "done"      # the check we could still do, still ran


# --- "has this opener been checked?" ----------------------------------------


def test_needs_gate_is_true_until_the_concept_is_checked(settings, store):
    idea = _idea(store)
    assert needs_gate(idea) is True
    gate_slide_one(idea, settings, store, judge=FakeJudge(['{"score": 9}']))
    assert needs_gate(store.get_idea("G-0001")) is False


def test_needs_gate_reopens_when_slide_one_changes(settings, store):
    """A hand-edited hook is a new concept, and gets checked like one."""
    idea = _idea(store)
    gate_slide_one(idea, settings, store, judge=FakeJudge(['{"score": 9}']))
    checked = store.get_idea("G-0001")
    slides = json.loads(checked.slides_json)
    slides[0]["headline"] = "something the editor typed instead"
    checked.slides_json = json.dumps(slides)
    assert needs_gate(checked) is True


def test_fingerprint_ignores_the_other_slides(settings, store):
    """Editing slide 3 must not re-open the (expensive) slide-1 tournament."""
    idea = _idea(store)
    gate_slide_one(idea, settings, store, judge=FakeJudge(['{"score": 9}']))
    checked = store.get_idea("G-0001")
    slides = json.loads(checked.slides_json)
    slides[1]["headline"] = "a different second slide"
    checked.slides_json = json.dumps(slides)
    assert needs_gate(checked) is False


# --- services wiring --------------------------------------------------------


def test_ensure_concept_gated_skips_an_already_checked_opener(settings, store, monkeypatch):
    """Asking for new pixels on an approved opener must not rewrite the post."""
    from chrgd import conceptgate, services
    from chrgd.conceptgate import GateResult

    idea = _idea(store)
    gate_slide_one(idea, settings, store, judge=FakeJudge(['{"score": 9}']))
    idea = store.get_idea("G-0001")

    calls = {"n": 0}
    monkeypatch.setattr(
        conceptgate, "gate_slide_one",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or GateResult(idea=idea),
    )
    _, gate = services.ensure_concept_gated(store, settings, idea)
    assert gate is None and calls["n"] == 0   # same opener → not fought over again
    services.ensure_concept_gated(store, settings, idea, force=True)
    assert calls["n"] == 1                    # ...unless explicitly asked for


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


# --- every door into a paid slide 1 ------------------------------------------
#
# The gate is only worth having if there is no way round it. These cover the
# render paths that don't go through the create journey's main button.


@pytest.fixture()
def gate_spy(monkeypatch):
    """Counts gate runs and stands in for the real (paid) one."""
    from chrgd import conceptgate
    from chrgd.conceptgate import GateResult

    calls = []

    def fake_gate(idea, s, st, **kw):
        calls.append(idea.idea_id)
        # Mark the concept checked, exactly as the real gate does, so the
        # "already checked" path is exercised honestly.
        conceptgate._persist(
            idea, st, json.loads(idea.slides_json),
            GateResult(idea=idea, passed=True, score=9, min_score=9,
                       verdict=conceptgate.ConceptVerdict(score=9)),
        )
        return GateResult(idea=idea, passed=True, score=9, min_score=9)

    monkeypatch.setattr(conceptgate, "gate_slide_one", fake_gate)
    return calls


@pytest.fixture()
def fake_images(monkeypatch):
    from PIL import Image as PILImage

    from chrgd import images

    monkeypatch.setattr(
        images, "_generate_background",
        lambda *a, **k: PILImage.new("RGB", (100, 150)),
    )


def _regen_slide(store, settings, n):
    """Run one render_slide job through its handler (no queue, no scroll test)."""
    from chrgd.worker import _handle_render_slide

    job_id = store.create_job("render_slide", idea_id="G-0001", params={"slide": n})
    return _handle_render_slide(store, settings, store.get_job(job_id))


def test_regenerating_slide_one_gates_a_changed_opener(settings, store, gate_spy, fake_images):
    """The scroll test's 'use a sharper hook' rewrites slide 1 — that new
    concept must be checked before it reaches a paid image."""
    from chrgd import services

    settings.openai_api_key = "sk-test"
    idea = _idea(store)
    services.render_idea(store, settings, idea, dry_run=False)
    assert len(gate_spy) == 1          # the first render checked the opener

    _regen_slide(store, settings, 0)
    assert len(gate_spy) == 1          # same concept, new pixels — not re-fought

    # ...but change the hook, and slide 1 is a new concept again.
    slides = json.loads(store.get_idea("G-0001").slides_json)
    slides[0]["headline"] = "a hook the scroll test invented"
    store.save_build("G-0001", {"slides_json": json.dumps(slides)})
    _regen_slide(store, settings, 0)
    assert len(gate_spy) == 2


def test_regenerating_a_later_slide_never_gates(settings, store, gate_spy, fake_images):
    settings.openai_api_key = "sk-test"
    _idea(store)
    _regen_slide(store, settings, 1)
    assert gate_spy == []


def test_sync_render_endpoint_cannot_sidestep_the_gate(settings, store, gate_spy, fake_images):
    """/api/render is a real door into a paid render — it goes through the gate."""
    from fastapi.testclient import TestClient

    from chrgd.webapp import create_app

    settings.openai_api_key = "sk-test"
    settings.web_password = "pw"
    _idea(store)
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "pw"})
    r = client.post("/api/render/G-0001")
    assert r.status_code == 200
    assert gate_spy == ["G-0001"]


def test_render_job_streams_the_stage_list_to_the_ui(settings, store, fake_images):
    """The four checks reach the job the create journey is polling."""
    from chrgd.worker import Worker, enqueue_render

    settings.openai_api_key = "sk-test"
    settings.concept_tournament_enabled = True
    settings.concept_candidates = 1
    settings.concept_glance_test = True
    _idea(store)

    import chrgd.conceptgate as cg

    class _Judge:
        def __init__(self):
            self.n = 0

        def judge(self, system, user):
            self.n += 1
            return [
                '{"winner": 0, "why": "holds"}',
                '{"score": 9}',
                '{"took_away": "rack hoggers", "stops": true}',
            ][min(self.n - 1, 2)]

    orig = cg.OpenAIConceptJudge
    cg.OpenAIConceptJudge = lambda s: _Judge()
    orig_invent = cg.invent_rivals
    cg.invent_rivals = lambda *a, **k: (
        [cg.ConceptCandidate(headline="a rival", image_prompt="p")], 0.01
    )
    try:
        job_id = enqueue_render(store, "G-0001", dry_run=False)
        Worker(settings).run_once()
    finally:
        cg.OpenAIConceptJudge = orig
        cg.invent_rivals = orig_invent

    # The job carried the live stage list while it ran; the finished post keeps it.
    saved = json.loads(store.get_idea("G-0001").route_json)["concept_gate"]
    assert [s["key"] for s in saved["stages"]] == [
        "rivals", "tournament", "score", "glance"
    ]
    assert all(s["state"] == "done" for s in saved["stages"])
    assert store.get_job(job_id)["status"] == "COMPLETED"
