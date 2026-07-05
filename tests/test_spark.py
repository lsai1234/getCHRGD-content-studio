"""Tests for the Create page engine: instant posts, story research, facts."""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.pipeline import build_one
from chrgd.spark import (
    Fact,
    SparkError,
    fact_to_idea,
    find_facts,
    research_story,
    seed_facts,
)
from chrgd.worker import Worker, enqueue_facts, enqueue_spark

from test_pipeline import FakeClient, make_post_dict


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db", CHRGD_OUTPUT_DIR=tmp_path / "out")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


class FakeSearch:
    """Canned web-search client (mirrors TrendSearchClient)."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[str] = []

    def search(self, system: str, user: str) -> str:
        self.calls.append(user)
        return json.dumps(self.payload)


def _fact_dict(i=1):
    return {
        "fact": f"fact {i}: England last played a 1AM UK kickoff in 1986",
        "why_it_lands": "half the audience wasn't born",
        "source_note": "FIFA archive",
        "decay_speed": "days",
        "concept_note": f"first-ever late-night England game moment {i}",
        "target_viewer": "20s UK gym-goers",
        "pain_point": "work tomorrow",
        "core_tension": "loyalty vs sleep",
    }


# --- story research -----------------------------------------------------------


def test_research_story_parses_digest(settings):
    client = FakeSearch(
        {
            "found": True,
            "headline": "England play Mexico at 1AM UK",
            "digest": "Kickoff is 01:00 BST Monday. Altitude 2,240m.",
            "uk_relevance": "everyone works Monday",
            "decay_speed": "days",
        }
    )
    d = research_story(settings, "https://example.com/story", client=client)
    assert d.found
    assert "01:00" in d.digest
    assert "example.com/story" in client.calls[0]


class _NotJSON:
    def search(self, system, user):
        return "definitely not json"


def test_research_story_bad_json_raises(settings):
    with pytest.raises(SparkError):
        research_story(settings, "x", client=_NotJSON())


# --- fact finder ----------------------------------------------------------------


def test_find_facts_parses_and_clamps(settings):
    client = FakeSearch({"facts": [_fact_dict(i) for i in range(5)]})
    result = find_facts(settings, "first England late kickoff", 3, client=client)
    assert len(result.facts) == 3
    assert "first England late kickoff" in client.calls[0]


def test_seed_facts_dedupes(settings, store):
    facts = [Fact.model_validate(_fact_dict(1)), Fact.model_validate(_fact_dict(1))]
    outcome = seed_facts(store, settings, facts)
    assert len(outcome.created) == 1
    assert len(outcome.skipped) == 1
    idea = outcome.created[0]
    assert idea.content_category == "fact"
    assert idea.priority == 1  # decay days -> ship fast
    assert "FIFA archive" in idea.source_context


# --- build_one ------------------------------------------------------------------


def test_build_one_builds_named_idea_not_queue_head(settings, store):
    # An older, higher-priority idea sits in the queue; build_one must ignore it.
    store.add_idea(Idea(idea_id="G-0001", concept_note="older", priority=1))
    store.add_idea(Idea(idea_id="G-0002", concept_note="target", priority=3))
    client = FakeClient([json.dumps(make_post_dict())])
    result = build_one(store, settings, "G-0002", client=client)
    assert result.status is Status.done
    assert store.get_idea("G-0002").status is Status.done
    assert store.get_idea("G-0001").status is Status.queued  # untouched


def test_build_one_source_context_reaches_prompt(settings, store):
    story = "Kickoff 01:00 BST; altitude 2,240m; squad landed Tuesday."
    store.add_idea(
        Idea(idea_id="G-0001", concept_note="england 1am", source_context=story)
    )

    class RecordingClient(FakeClient):
        def complete(self, system, user):
            self.last_user = user
            return super().complete(system, user)

    client = RecordingClient([json.dumps(make_post_dict())])
    build_one(store, settings, "G-0001", client=client)
    assert "altitude 2,240m" in client.last_user


def test_build_one_flags_review_on_qa_fail(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    bad = json.dumps(make_post_dict(hook=4))
    result = build_one(store, settings, "G-0001", client=FakeClient([bad, bad]))
    assert result.status is Status.review
    assert store.get_idea("G-0001").status is Status.review


# --- worker handlers --------------------------------------------------------------


def test_spark_job_researches_then_builds(settings, store, monkeypatch):
    from chrgd import pipeline as pl
    from chrgd import spark as sp

    store.add_idea(Idea(idea_id="G-0001", concept_note="england 1am"))
    monkeypatch.setattr(
        sp,
        "research_story",
        lambda s, q, client=None: sp.StoryDigest(
            found=True,
            headline="England at 1AM",
            digest="Kickoff 01:00 BST Monday.",
            uk_relevance="work next day",
        ),
    )
    # The worker builds with the real client class — inject the fake.
    monkeypatch.setattr(
        pl, "OpenAIChatClient", lambda settings: FakeClient([json.dumps(make_post_dict())])
    )
    job_id = enqueue_spark(store, "G-0001", research="https://example.com/story")
    assert Worker(settings).run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED"
    assert json.loads(job["result_json"])["status"] == "done"
    idea = store.get_idea("G-0001")
    assert idea.status is Status.done
    assert "Kickoff 01:00 BST" in idea.source_context


def test_spark_job_without_research_skips_search(settings, store, monkeypatch):
    from chrgd import pipeline as pl
    from chrgd import spark as sp

    def boom(*a, **k):  # research must NOT be called with no url
        raise AssertionError("research_story called unexpectedly")

    monkeypatch.setattr(sp, "research_story", boom)
    monkeypatch.setattr(
        pl, "OpenAIChatClient", lambda settings: FakeClient([json.dumps(make_post_dict())])
    )
    store.add_idea(Idea(idea_id="G-0001", concept_note="quick thought"))
    job_id = enqueue_spark(store, "G-0001")
    Worker(settings).run_once()
    assert store.get_job(job_id)["status"] == "COMPLETED"


def test_enqueue_spark_dedupes_active(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    a = enqueue_spark(store, "G-0001")
    b = enqueue_spark(store, "G-0001")
    assert a == b


def test_facts_job_records_results(settings, store, monkeypatch):
    from chrgd import spark as sp

    monkeypatch.setattr(
        sp,
        "find_facts",
        lambda settings, seed, count, client=None: sp.FactResult(
            facts=[sp.Fact.model_validate(_fact_dict(1))]
        ),
    )
    job_id = enqueue_facts(store, seed="first late kickoff", count=3)
    Worker(settings).run_once()
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED"
    assert len(json.loads(job["result_json"])["facts"]) == 1


def test_fact_to_idea_fields(settings, store):
    fact = Fact.model_validate(_fact_dict(2))
    idea = fact_to_idea(store, settings, fact)
    assert idea.status is Status.queued
    assert idea.learning_tag == "fact"
    assert idea.concept_note == fact.concept_note
