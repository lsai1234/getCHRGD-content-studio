"""Tests for Phase A2: the background job worker."""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.worker import Worker, enqueue_render


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db", CHRGD_OUTPUT_DIR=tmp_path / "out")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def _rendered_idea(store, idea_id="G-0001"):
    slides = [
        {"headline": f"h{i}", "supporting": "s", "image_prompt": "p", "visual_intent": "v"}
        for i in range(5)
    ]
    store.add_idea(Idea(idea_id=idea_id, concept_note="x", slides_json=json.dumps(slides)))


def test_run_once_idle_returns_none(settings, store):
    assert Worker(settings).run_once() is None


def test_render_job_processed(settings, store):
    _rendered_idea(store)
    job_id = enqueue_render(store, "G-0001", dry_run=True)
    assert store.get_job(job_id)["status"] == "QUEUED"

    worker = Worker(settings)
    assert worker.run_once() == job_id

    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED"
    assert job["progress"] == 100
    result = json.loads(job["result_json"])
    assert len(result["paths"]) == 5


def test_enqueue_render_dedupes(settings, store):
    _rendered_idea(store)
    a = enqueue_render(store, "G-0001", dry_run=True)
    b = enqueue_render(store, "G-0001", dry_run=True)
    assert a == b  # same active job reused, not double-queued


def test_bad_job_records_error(settings, store):
    # render job for a missing idea -> handler raises -> job ERROR, loop survives
    store.create_job("render", idea_id="NOPE", params={"dry_run": True})
    worker = Worker(settings)
    worker.run_once()
    job = store.list_jobs()[0]
    assert job["status"] == "ERROR"
    assert "NOPE" in job["error"]


def test_build_job_without_key_errors_cleanly(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    store.create_job("build", params={"count": 1, "dry_run": False})
    Worker(settings).run_once()
    job = store.list_jobs()[0]
    # No OPENAI_API_KEY -> build raises LLMError -> job ERROR (not a crash).
    assert job["status"] == "ERROR"


def test_recover_interrupted_marks_error(settings, store):
    _rendered_idea(store)
    job_id = store.create_job("render", idea_id="G-0001", params={})
    store.update_job(job_id, status="PROCESSING")
    n = store.recover_interrupted_jobs()
    assert n == 1
    assert store.get_job(job_id)["status"] == "ERROR"


def test_claim_is_fifo(settings, store):
    j1 = store.create_job("render", idea_id="A", params={})
    j2 = store.create_job("render", idea_id="B", params={})
    assert store.claim_next_job()["job_id"] == j1
    assert store.claim_next_job()["job_id"] == j2
    assert store.claim_next_job() is None  # both now PROCESSING
