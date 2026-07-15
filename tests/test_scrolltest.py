"""Tests for the cold scroll test — the vision verdict on the finished slide 1.

All offline: the vision judge is faked, so no key or spend is needed.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea
from chrgd.scrolltest import (
    ScrollTestError,
    ScrollVerdict,
    parse_verdict,
    run_scroll_test,
)
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


class FakeJudge:
    """Captures the prompt/image it was handed and returns canned JSON."""

    def __init__(self, payload: str):
        self.payload = payload
        self.system = self.user = self.mime = ""
        self.image_b64 = ""

    def judge(self, system, user, image_b64, mime):
        self.system, self.user, self.image_b64, self.mime = system, user, image_b64, mime
        return self.payload


def _rendered_idea(settings, store, **over) -> Idea:
    """An idea with a real rendered slide-1 image on disk."""
    out = settings.output_dir / "G-0001"
    out.mkdir(parents=True, exist_ok=True)
    slide1 = out / "slide_1.jpg"
    Image.new("RGB", (1080, 1620), (20, 20, 24)).save(slide1)
    slides = [{"headline": "how many scoops?", "supporting": "confess below"}]
    idea = Idea(
        idea_id="G-0001",
        concept_note="x",
        hook="how many scoops?",
        slides_json=json.dumps(slides),
        asset_paths_json=json.dumps([str(slide1)]),
        **over,
    )
    store.add_idea(idea)
    return idea


# --- parsing ----------------------------------------------------------------


def test_parse_verdict_stop_clears_fix():
    v = parse_verdict(
        '{"verdict":"stop","confidence":72,"killer":"x","fix_kind":"sharpen_hook",'
        '"fix":"y","note":"strong callout"}'
    )
    assert v.stops is True
    assert v.fix_kind == "none" and v.fix == ""  # a stop needs no fix


def test_parse_verdict_clamps_confidence():
    v = parse_verdict('{"verdict":"scroll","confidence":140,"fix_kind":"none"}')
    assert v.confidence == 100
    assert v.stops is False


def test_parse_verdict_strips_fence():
    v = parse_verdict('```json\n{"verdict":"scroll","confidence":10}\n```')
    assert v.confidence == 10


def test_parse_verdict_bad_text_raises():
    with pytest.raises(ScrollTestError):
        parse_verdict("no json here")


# --- run_scroll_test --------------------------------------------------------


def test_run_scroll_test_returns_verdict(settings, store):
    idea = _rendered_idea(settings, store)
    judge = FakeJudge(
        '{"verdict":"scroll","confidence":18,"killer":"flat hook, generic image",'
        '"fix_kind":"sharpen_hook","fix":"lead with the confession",'
        '"note":"nothing here stops the thumb"}'
    )
    v = run_scroll_test(idea, settings, store, judge=judge)
    assert isinstance(v, ScrollVerdict)
    assert v.verdict == "scroll" and v.fix_kind == "sharpen_hook"
    # The hook + the actual image reached the judge.
    assert "how many scoops?" in judge.user
    assert judge.image_b64 and judge.mime == "image/jpeg"


def test_run_scroll_test_needs_a_rendered_slide(settings, store):
    idea = Idea(idea_id="G-0002", concept_note="x", hook="h",
                slides_json=json.dumps([{"headline": "h"}]))
    store.add_idea(idea)
    with pytest.raises(ScrollTestError):
        run_scroll_test(idea, settings, store, judge=FakeJudge("{}"))


def test_scroll_test_is_calibrated_on_real_results(settings, store):
    """When the account has logged hits/flops, that history is fed to the judge."""
    from chrgd.learning import performance_notes

    idea = _rendered_idea(settings, store)
    # Log enough rated posts that performance_notes returns a real block:
    # one category that consistently hits, another that consistently flops.
    for i in range(3):
        win = Idea(idea_id=f"G-1{i}", concept_note=f"w{i}", content_category="humour")
        store.add_idea(win)
        store.merge_metrics(win.idea_id, {"rating": "hit"})
        lose = Idea(idea_id=f"G-2{i}", concept_note=f"l{i}", content_category="advice")
        store.add_idea(lose)
        store.merge_metrics(lose.idea_id, {"rating": "flop"})
    notes = performance_notes(store)
    assert notes  # sanity: the account now has a calibration block

    judge = FakeJudge('{"verdict":"stop","confidence":60}')
    run_scroll_test(idea, settings, store, judge=judge)
    # The real history travelled into the judge's prompt.
    assert "THIS account" in judge.user


# --- worker + web endpoint --------------------------------------------------


def test_worker_handler_stores_verdict(settings, store, monkeypatch):
    from chrgd import scrolltest, worker

    idea = _rendered_idea(settings, store)
    monkeypatch.setattr(
        scrolltest, "run_scroll_test",
        lambda *a, **k: ScrollVerdict(verdict="scroll", confidence=12,
                                      killer="generic image", fix_kind="regenerate_image",
                                      fix="make slide 1 a real scene"),
    )
    job_id = worker.enqueue_scroll_test(store, idea.idea_id)
    worker.Worker(settings).run_once()
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED"
    verdict = json.loads(job["result_json"])["verdict"]
    assert verdict["fix_kind"] == "regenerate_image"


def test_endpoint_400_without_render(settings, store):
    idea = Idea(idea_id="G-0003", concept_note="x", hook="h",
                slides_json=json.dumps([{"headline": "h"}]))
    store.add_idea(idea)
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    r = client.post("/api/scroll-test/G-0003")
    assert r.status_code == 400


def test_endpoint_enqueues_for_rendered_idea(settings, store):
    _rendered_idea(settings, store)
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    r = client.post("/api/scroll-test/G-0001")
    assert r.status_code == 200
    assert r.json()["kind"] == "scroll_test" and r.json()["job_id"]
