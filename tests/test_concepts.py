"""The concept engine: make the creative leap from a mixed pool into finished,
ready-to-build concepts, and degrade safely."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chrgd.concepts import (
    CONCEPT_SYSTEM,
    Concept,
    cached_concepts,
    develop_concept,
    generate_concepts,
    sketch_concepts,
)
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.pipeline import LLMResult
from chrgd.webapp import create_app


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_WEB_USERNAME="admin",
        CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def _completed_scan(store, kind, moments):
    job_id = store.create_job(kind)
    store.update_job(job_id, status="COMPLETED", result_json=json.dumps({"moments": moments}))
    return job_id


class _FakeGen:
    """Stands in for the creative model — records what it was asked, returns a
    canned concept set."""

    def __init__(self, payload):
        self._payload = payload
        self.system = self.user = None

    def complete(self, system, user):
        self.system, self.user = system, user
        return LLMResult(content=json.dumps(self._payload), prompt_tokens=1, completion_tokens=1)


def test_build_seed_carries_angle_and_hook():
    c = Concept(angle="make gyms free like Burnham's buses", hook="He's cut buses to £2. Gyms next?")
    seed = c.build_seed()
    assert "make gyms free" in seed
    assert "open with:" in seed and "£2" in seed


def test_generates_finished_concepts_from_the_pool(store, settings):
    _completed_scan(store, "moments", [{"title": "Andy Burnham becomes PM", "why": "huge UK story"}])
    gen = _FakeGen({"concepts": [
        {"flavour": "topical", "title": "Make gyms free like Burnham's buses",
         "hook": "He's making buses £2. Here's why gyms should be next.",
         "why": "cheeky, opinionated, shareable", "angle": "argue gym memberships should be subsidised",
         "source": "Andy Burnham becomes PM"},
        {"flavour": "stat", "title": "Your real odds of a 6pm squat rack",
         "hook": "6,000 members. 30 racks.", "why": "relatable rage", "angle": "the PureGym maths"},
    ]})
    out = generate_concepts(store, settings, generator=gen)
    concepts = out["concepts"]
    assert len(concepts) == 2
    assert concepts[0]["flavour"] == "topical"
    # build_seed carries the angle (what to write) + the hook (how to open).
    assert "build_seed" in concepts[0]
    assert "subsidised" in concepts[0]["build_seed"] and "open with:" in concepts[0]["build_seed"]
    # The real live signal was handed to the model as grounding fuel.
    assert "Andy Burnham becomes PM" in gen.user
    # Variety of flavour, not one rigid lane.
    assert {c["flavour"] for c in concepts} == {"topical", "stat"}


def test_editor_seed_is_threaded_as_grounding(store, settings):
    gen = _FakeGen({"concepts": [{"title": "x", "angle": "y"}]})
    generate_concepts(store, settings, seed="Andy Burnham just became PM", generator=gen)
    assert "Andy Burnham just became PM" in gen.user
    assert "EDITOR JUST FED YOU THIS" in gen.user


def test_works_cold_with_no_scans(store, settings):
    # No live signal at all → the engine still asks for evergreen concepts.
    gen = _FakeGen({"concepts": [{"flavour": "stat", "title": "a", "angle": "b"}]})
    out = generate_concepts(store, settings, generator=gen)
    assert len(out["concepts"]) == 1
    assert "evergreen" in gen.user.lower()


def test_bad_json_degrades_to_empty(store, settings):
    class Broken:
        def complete(self, system, user):
            return LLMResult(content="not json", prompt_tokens=0, completion_tokens=0)

    out = generate_concepts(store, settings, generator=Broken())
    assert out["concepts"] == []
    assert "error" in out


def test_system_prompt_encodes_the_creative_leap():
    # The prompt is what the operator signs off — assert the north-star rules.
    assert "creative LEAP" in CONCEPT_SYSTEM
    assert "Burnham" in CONCEPT_SYSTEM              # the worked example
    assert "GROUND THE FACTS, INVENT THE ANGLE" in CONCEPT_SYSTEM
    assert "VARY the flavour" in CONCEPT_SYSTEM     # not rigid lanes
    assert "awareness-day" in CONCEPT_SYSTEM        # no calendar filler


def test_system_prompt_is_grounded_in_real_engagement_mechanics():
    # Not "be clever" hand-waving — the actual named drivers of why things spread,
    # a real comedy/interest craft section, and a self-audit that kills lazy takes.
    assert "WHY THINGS ACTUALLY SPREAD" in CONCEPT_SYSTEM
    for driver in ("CURIOSITY GAP", "HIGH-AROUSAL EMOTION", "SOCIAL CURRENCY",
                   "IDENTITY / TRIBE", "COMMENT WAR"):
        assert driver in CONCEPT_SYSTEM
    assert "FUNNY, DONE PROPERLY" in CONCEPT_SYSTEM
    assert "INTERESTING, DONE PROPERLY" in CONCEPT_SYSTEM
    assert "SELF-AUDIT" in CONCEPT_SYSTEM
    assert "DELETE" in CONCEPT_SYSTEM              # it must bin the ones that don't land


def test_concepts_are_grounded_in_brand_evidence(store, settings, monkeypatch):
    # The engine stands on the brand's own voice/examples + proven shapes, not a
    # blank slate — so the leaps sound like CHRGD, not generic AI banter.
    import chrgd.concepts as cm

    monkeypatch.setattr(cm, "load_brand_bible", lambda: "GOLD EXAMPLE: dry UK lifter voice", raising=False)
    monkeypatch.setattr("chrgd.pipeline.load_brand_bible", lambda: "GOLD EXAMPLE: dry UK lifter voice")
    gen = _FakeGen({"concepts": [{"title": "x", "angle": "y"}]})
    generate_concepts(store, settings, generator=gen)
    assert "GOLD-STANDARD EXAMPLES" in gen.user
    assert "GOLD EXAMPLE: dry UK lifter voice" in gen.user


def test_endpoint_enqueues_a_job(settings):
    # The endpoint now kicks a background job (the creative call is too slow for
    # the web request) and returns a job_id to poll.
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    r = c.post("/api/concepts", data={})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "concepts" and isinstance(body["job_id"], int)


def test_no_seed_enqueue_is_deduped(settings, store):
    from chrgd.worker import enqueue_concepts

    a = enqueue_concepts(store, seed="")
    b = enqueue_concepts(store, seed="")
    assert a == b  # a reopened page shouldn't stack idle concept runs
    c = enqueue_concepts(store, seed="Andy Burnham just became PM")
    assert c != a  # a seeded spin is always fresh


def test_worker_handles_concepts_job(settings, store, monkeypatch):
    # The worker handler runs the FAST sketch (stage 1) and stores its concepts.
    import chrgd.concepts as cm

    monkeypatch.setattr(
        cm, "sketch_concepts",
        lambda s, se, seed="": {"concepts": [{"title": "t", "angle": "a"}], "seeded": bool(seed)},
    )
    from chrgd.worker import _handle_concepts

    job_id = store.create_job("concepts", params={"seed": ""})
    out = _handle_concepts(store, settings, store.get_job(job_id))
    assert out["concepts"][0]["title"] == "t"


# --- stage 1: the fast sketch --------------------------------------------------


def test_sketch_is_headline_level_and_buildable(store, settings):
    # Five concepts at headline level — no `why`/`angle` essay — and each one is
    # still buildable as it stands, so nobody waits on the drill-down.
    gen = _FakeGen({"concepts": [
        {"flavour": "topical", "title": "Make gyms free like Burnham's buses",
         "hook": "He's making buses £2. Gyms next?", "source": "Burnham"},
        {"flavour": "stat", "title": "Your real odds of a 6pm squat rack",
         "hook": "6,000 members. 30 racks."},
    ]})
    out = sketch_concepts(store, settings, generator=gen)
    assert out["stage"] == "sketch"
    first = out["concepts"][0]
    assert first["detailed"] is False
    assert "Burnham's buses" in first["build_seed"] and "open with:" in first["build_seed"]


def test_sketch_prompt_skips_the_heavy_evidence_base(store, settings, monkeypatch):
    # The whole point: the bible and the playbook (thousands of tokens) are NOT
    # in the fast prompt — they belong to the drill-down.
    monkeypatch.setattr("chrgd.pipeline.load_brand_bible", lambda: "GOLD EXAMPLE: dry UK lifter voice")
    monkeypatch.setattr("chrgd.pipeline.load_playbook", lambda: "PROVEN SHAPE: the ranking")
    gen = _FakeGen({"concepts": [{"title": "x"}]})
    sketch_concepts(store, settings, generator=gen)
    assert "GOLD EXAMPLE" not in gen.user
    assert "PROVEN SHAPE" not in gen.user
    assert "PITCHING" in gen.system or "pitch" in gen.system.lower()


def test_sketch_is_still_grounded_in_live_signal(store, settings):
    _completed_scan(store, "moments", [{"title": "Andy Burnham becomes PM", "why": "huge UK story"}])
    gen = _FakeGen({"concepts": [{"title": "x"}]})
    sketch_concepts(store, settings, seed="my own spark", generator=gen)
    assert "Andy Burnham becomes PM" in gen.user
    assert "my own spark" in gen.user


def test_sketch_degrades_to_empty_on_bad_json(store, settings):
    class Broken:
        def complete(self, system, user):
            return LLMResult(content="not json")

    out = sketch_concepts(store, settings, generator=Broken())
    assert out["concepts"] == [] and "error" in out


# --- stage 2: the drill-down ---------------------------------------------------


def test_drill_down_fills_in_the_treatment(store, settings):
    gen = _FakeGen({
        "flavour": "topical", "title": "Make gyms free like Burnham's buses",
        "hook": "He's cut buses to £2. Gyms next?",
        "why": "SOCIAL CURRENCY — sharing it says 'I've done the maths'",
        "angle": "argue gym memberships should be the next thing subsidised",
        "source": "Burnham bus fares", "beats": ["the policy", "the gym maths", "the ask"],
    })
    sketch = {"flavour": "topical", "title": "Make gyms free like Burnham's buses",
              "hook": "He's making buses £2. Gyms next?"}
    out = develop_concept(store, settings, sketch, generator=gen)
    c = out["concept"]
    assert out["stage"] == "developed" and c["detailed"] is True
    assert "subsidised" in c["angle"] and c["beats"][0] == "the policy"
    # The build seed now carries the developed angle, not just the headline.
    assert "subsidised" in c["build_seed"]
    # It was handed the chosen concept AND the full evidence base.
    assert "THE CONCEPT THE EDITOR CHOSE" in gen.user
    assert "Make gyms free like Burnham's buses" in gen.user


def test_drill_down_keeps_the_sketch_when_it_fails(store, settings):
    class Broken:
        def complete(self, system, user):
            raise RuntimeError("model exploded")

    sketch = {"title": "Your real odds of a 6pm squat rack", "hook": "6,000 members. 30 racks."}
    out = develop_concept(store, settings, sketch, generator=Broken())
    # The card keeps what it had and stays buildable — the drill is a bonus.
    assert out["concept"]["title"] == sketch["title"]
    assert out["concept"]["detailed"] is False
    assert "6pm squat rack" in out["concept"]["build_seed"]
    assert "model exploded" in out["error"]


def test_drill_down_never_loses_fields_the_sketch_had(store, settings):
    # A thin response mustn't blank the hook/source the editor is looking at.
    gen = _FakeGen({"why": "CURIOSITY GAP — you have to see slide 2",
                    "angle": "the PureGym maths"})
    sketch = {"flavour": "stat", "title": "Your real odds of a 6pm squat rack",
              "hook": "6,000 members. 30 racks.", "source": "PureGym numbers"}
    c = develop_concept(store, settings, sketch, generator=gen)["concept"]
    assert c["hook"] == "6,000 members. 30 racks."
    assert c["source"] == "PureGym numbers" and c["flavour"] == "stat"


# --- today's set, cached -------------------------------------------------------


def test_cached_set_is_returned_without_spending(store, settings):
    assert cached_concepts(store) is None  # nothing run yet
    job_id = store.create_job("concepts", params={"seed": ""})
    store.update_job(job_id, status="COMPLETED",
                     result_json=json.dumps({"concepts": [{"title": "t"}]}))
    cached = cached_concepts(store)
    assert cached["concepts"][0]["title"] == "t"
    assert cached["cached"] is True and cached["job_id"] == job_id


def test_cached_set_ignores_seeded_and_stale_runs(store, settings):
    seeded = store.create_job("concepts", params={"seed": "my spark"})
    store.update_job(seeded, status="COMPLETED",
                     result_json=json.dumps({"concepts": [{"title": "seeded"}]}))
    # A one-off spin off the editor's own seed isn't "today's set".
    assert cached_concepts(store) is None
    job_id = store.create_job("concepts", params={"seed": ""})
    store.update_job(job_id, status="COMPLETED",
                     result_json=json.dumps({"concepts": [{"title": "t"}]}))
    assert cached_concepts(store) is not None
    # Yesterday's set is not today's — a stale one is dropped, not reused.
    old = (datetime.now().astimezone() - timedelta(hours=20)).isoformat()
    store.conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?", (old, job_id))
    store.conn.commit()
    assert cached_concepts(store) is None


def test_cached_endpoint_paints_without_a_job(settings, store):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    # Cold: nothing to show yet, and no job in flight.
    assert c.get("/api/concepts").json()["status"] == "cold"
    job_id = store.create_job("concepts", params={"seed": ""})
    store.update_job(job_id, status="COMPLETED",
                     result_json=json.dumps({"concepts": [{"title": "t"}]}))
    body = c.get("/api/concepts").json()
    assert body["status"] == "ready" and body["concepts"][0]["title"] == "t"


def test_develop_endpoint_enqueues_a_drill_job(settings):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    r = c.post("/api/concepts/develop", data={"title": "Make gyms free", "hook": "Buses are £2."})
    assert r.status_code == 200 and r.json()["kind"] == "concept_detail"
    # Nothing to develop → a clean 400, not a job.
    assert c.post("/api/concepts/develop", data={}).status_code == 400


def test_drill_enqueue_is_deduped_per_concept(settings, store):
    from chrgd.worker import enqueue_concept_detail

    a = enqueue_concept_detail(store, {"title": "Make gyms free"})
    b = enqueue_concept_detail(store, {"title": "Make gyms free"})
    assert a == b  # a double-tap shouldn't pay twice
    assert enqueue_concept_detail(store, {"title": "A different concept"}) != a


def test_worker_handles_the_drill_job(settings, store, monkeypatch):
    import chrgd.concepts as cm

    monkeypatch.setattr(
        cm, "develop_concept",
        lambda s, se, concept, seed="": {"concept": {**concept, "why": "w"}, "stage": "developed"},
    )
    from chrgd.worker import _handle_concept_detail

    job_id = store.create_job("concept_detail", params={"concept": {"title": "t"}, "seed": ""})
    out = _handle_concept_detail(store, settings, store.get_job(job_id))
    assert out["concept"]["title"] == "t" and out["concept"]["why"] == "w"
