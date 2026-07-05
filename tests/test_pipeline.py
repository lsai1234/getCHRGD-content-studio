"""Tests for milestone 2: the pipeline runner, QA gate, and spend logging.

A fake chat client stands in for OpenAI so these run offline with no key.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Post, Status
from chrgd.pipeline import (
    LLMResult,
    build_ideas,
    estimate_cost,
    run_pipeline_for_idea,
)


def make_post_dict(
    *, overall=9, hook=9, visual=9, group=9, save=8, slides=5, fmt="sketch"
):
    return {
        "post_type": "carousel",
        "hook": "you're the locker hogger, aren't you",
        "slides": [
            {
                "headline": f"slide {i}",
                "supporting": "supporting line",
                "image_prompt": "prompt",
                "visual_intent": "intent",
            }
            for i in range(slides)
        ],
        "caption": "caption text",
        "comment_trigger": "which one are you?",
        "pinned_comments": [
            "if this goes to extra time, Monday is cancelled",
            "tag the mate with zero plan",
        ],
        "hashtags": ["#gym", "#uk"],
        "route": {
            "format": fmt,
            "mechanic": "identity_exposure",
            "visual_engine": "real_gym_micro_scene",
            "primary_goal": "comments",
            "build_note": "note",
            "qa": {
                "hook": hook,
                "swipe_loop": 8,
                "identity_recognition": 8,
                "group_chat_share": group,
                "comment_fight": 8,
                "saveability": save,
                "visual_originality": visual,
                "dopamine_density": 8,
                "clarity": 9,
                "layout_safety": 9,
                "claim_safety": 10,
                "overall": overall,
            },
        },
    }


class FakeClient:
    """Returns queued canned responses; records how many times it was called."""

    def __init__(self, contents: list[str]):
        self._contents = contents
        self.calls = 0

    def complete(self, system: str, user: str) -> LLMResult:
        content = self._contents[min(self.calls, len(self._contents) - 1)]
        self.calls += 1
        return LLMResult(content=content, prompt_tokens=1000, completion_tokens=500)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db", CHRGD_MAX_SPEND_PER_RUN=100)


# --- QA gate ----------------------------------------------------------------


def test_qa_passes_on_good_scores():
    post = Post.model_validate(make_post_dict())
    assert post.passes_qa()
    assert post.qa_failures() == []


def test_qa_fails_on_low_hook():
    post = Post.model_validate(make_post_dict(hook=6))
    assert not post.passes_qa()
    assert any("hook" in r for r in post.qa_failures())


def test_qa_fails_when_no_engagement_gate_hits_8():
    post = Post.model_validate(make_post_dict(group=6))
    # group_chat_share is the only high engagement gate in the fixture; drop it
    # and the others are all at 8 in the fixture, so this still passes. Force all low:
    d = make_post_dict()
    d["route"]["qa"].update(
        group_chat_share=6, comment_fight=6, saveability=6, dopamine_density=6
    )
    post = Post.model_validate(d)
    assert not post.passes_qa()


def test_qa_fails_on_wrong_slide_count():
    post = Post.model_validate(make_post_dict(slides=4))
    assert any("slides" in r for r in post.qa_failures())
    post = Post.model_validate(make_post_dict(slides=11))
    assert any("slides" in r for r in post.qa_failures())


def test_qa_passes_nine_slide_playbook():
    """The proven best-performer shape: a 9-slide funny-useful playbook."""
    post = Post.model_validate(make_post_dict(slides=9, fmt="playbook"))
    assert post.post_format == "playbook"
    assert post.passes_qa(), post.qa_failures()


def test_qa_playbook_needs_seven_slides_and_saves():
    # Too short for a playbook.
    post = Post.model_validate(make_post_dict(slides=5, fmt="playbook"))
    assert any("playbook" in r for r in post.qa_failures())
    # Long enough, but not save-worthy — playbooks live on saves.
    post = Post.model_validate(make_post_dict(slides=8, fmt="playbook", save=6))
    assert any("saveability" in r for r in post.qa_failures())


# --- cost -------------------------------------------------------------------


def test_estimate_cost_uses_model_price():
    cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert cost == pytest.approx(2.5 + 10.0)


def test_estimate_cost_unknown_model_falls_back():
    assert estimate_cost("mystery-model", 1_000_000, 0) == pytest.approx(2.5)


# --- runner -----------------------------------------------------------------


def test_runner_returns_done_on_good_post():
    idea = Idea(idea_id="G-0001", concept_note="locker hoggers")
    client = FakeClient([json.dumps(make_post_dict())])
    result = run_pipeline_for_idea(idea, client, "gpt-4o")
    assert result.status is Status.done
    assert result.attempts == 1
    assert result.post.hook.startswith("you're the locker")
    assert result.spend_usd > 0


def test_runner_re_requests_then_flags_review():
    idea = Idea(idea_id="G-0001", concept_note="x")
    bad = json.dumps(make_post_dict(hook=5))
    client = FakeClient([bad, bad])  # fails both attempts
    result = run_pipeline_for_idea(idea, client, "gpt-4o")
    assert result.status is Status.review
    assert client.calls == 2  # first try + one re-request
    assert result.post is not None  # last post kept for inspection


def test_runner_recovers_on_second_attempt():
    idea = Idea(idea_id="G-0001", concept_note="x")
    client = FakeClient([json.dumps(make_post_dict(hook=5)), json.dumps(make_post_dict())])
    result = run_pipeline_for_idea(idea, client, "gpt-4o")
    assert result.status is Status.done
    assert result.attempts == 2


def test_runner_handles_invalid_json():
    idea = Idea(idea_id="G-0001", concept_note="x")
    client = FakeClient(["not json at all", "still not json"])
    result = run_pipeline_for_idea(idea, client, "gpt-4o")
    assert result.status is Status.review
    assert result.post is None


# --- build_ideas end to end -------------------------------------------------


def test_build_ideas_persists_and_marks(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="good one"))
    store.add_idea(Idea(idea_id="G-0002", concept_note="bad one"))
    client = FakeClient([json.dumps(make_post_dict())])  # first idea good
    # second idea: make client return a failing post by exhausting; use a client
    # that returns good for both to keep it simple, then assert both done.
    results = build_ideas(store, settings, 2, client=client)
    assert all(r.status is Status.done for r in results)
    g1 = store.get_idea("G-0001")
    assert g1.status is Status.done
    assert g1.hook is not None
    assert g1.slides_json is not None
    assert json.loads(g1.hashtags) == ["#gym", "#uk"]
    assert len(json.loads(g1.pinned_comments_json)) == 2


def test_build_ideas_flags_review_in_db(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    bad = json.dumps(make_post_dict(hook=4))
    results = build_ideas(store, settings, 1, client=FakeClient([bad, bad]))
    assert results[0].status is Status.review
    assert store.get_idea("G-0001").status is Status.review
    # A review row still has its build fields saved for inspection.
    assert store.get_idea("G-0001").hook is not None


def test_build_ideas_respects_spend_cap(store, settings):
    settings.max_spend_per_run = 0.0  # cap already reached
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    results = build_ideas(store, settings, 1, client=FakeClient([json.dumps(make_post_dict())]))
    assert results[0].status is Status.queued
    assert "cap" in results[0].error
    assert store.get_idea("G-0001").status is Status.queued  # untouched


def test_build_ideas_records_run(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    build_ideas(store, settings, 1, client=FakeClient([json.dumps(make_post_dict())]))
    row = store.conn.execute("SELECT * FROM runs").fetchone()
    assert row["command"] == "build"
    assert row["built"] == 1
    assert row["finished_at"] is not None
