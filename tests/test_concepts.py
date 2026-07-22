"""The concept engine: make the creative leap from a mixed pool into finished,
ready-to-build concepts, and degrade safely."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from chrgd.concepts import CONCEPT_SYSTEM, Concept, generate_concepts
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


def test_endpoint_returns_concepts(settings, store, monkeypatch):
    # Endpoint wires generate_concepts and never 500s.
    import chrgd.concepts as concepts_mod

    monkeypatch.setattr(
        concepts_mod, "generate_concepts",
        lambda *a, **k: {"concepts": [{"title": "t", "angle": "a", "build_seed": "a"}], "seeded": False},
    )
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    r = c.post("/api/concepts", data={})
    assert r.status_code == 200
    assert r.json()["concepts"][0]["title"] == "t"
