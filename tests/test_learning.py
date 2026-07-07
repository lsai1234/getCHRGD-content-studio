"""The learning loop: logged results → insights → engine steering."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.learning import MIN_POSTS_FOR_NOTES, insights, performance_notes
from chrgd.models import Idea, Status
from chrgd.pipeline import LLMResult, build_single_idea
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


@pytest.fixture()
def client(settings):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    return c


def _logged_post(store, idea_id, views, *, category="", mechanic="", hook=""):
    store.add_idea(
        Idea(
            idea_id=idea_id,
            concept_note=idea_id,
            content_category=category,
            hook=hook,
            route_json=json.dumps({"mechanic": mechanic}) if mechanic else None,
        )
    )
    store.set_metrics(idea_id, {"views": views, "likes": views // 20})


def test_insights_empty(store):
    d = insights(store)
    assert d["posts_logged"] == 0 and d["top"] == []


def test_insights_ranks_and_finds_traits(store):
    _logged_post(store, "G-0001", 300, category="humour", mechanic="identity_exposure")
    _logged_post(store, "G-0002", 450, category="humour", mechanic="identity_exposure")
    _logged_post(store, "G-0003", 20000, category="moment", mechanic="rage_agreement",
                 hook="staying up for the game? read this")
    _logged_post(store, "G-0004", 15000, category="moment", mechanic="rage_agreement")

    d = insights(store)
    assert d["posts_logged"] == 4
    assert d["top"][0]["idea_id"] == "G-0003"
    assert d["top"][0]["multiple"] > 1
    moment = [t for t in d["traits"] if t["trait"] == "category" and t["value"] == "moment"][0]
    humour = [t for t in d["traits"] if t["trait"] == "category" and t["value"] == "humour"][0]
    assert moment["median_views"] > humour["median_views"]
    assert moment["vs_baseline"] > 1


def test_notes_need_enough_data(store):
    _logged_post(store, "G-0001", 300)
    _logged_post(store, "G-0002", 400)
    assert performance_notes(store) == ""  # 2 < MIN_POSTS_FOR_NOTES
    _logged_post(store, "G-0003", 9000, category="moment",
                 hook="the 2am kick-off survival guide")
    notes = performance_notes(store)
    assert "PERFORMANCE NOTES" in notes
    assert "2am kick-off" in notes
    assert MIN_POSTS_FOR_NOTES == 3


def test_zero_view_logs_are_ignored(store):
    _logged_post(store, "G-0001", 0)
    assert insights(store)["posts_logged"] == 0


def test_notes_reach_the_engine(settings, store):
    for i, views in enumerate((300, 400, 12000), 1):
        _logged_post(store, f"G-000{i}", views, category="moment",
                     hook=f"hook {views}")
    store.add_idea(Idea(idea_id="G-0100", concept_note="new idea"))

    class CapturingChat:
        def complete(self, system, user):
            self.user = user
            post = {
                "post_type": "carousel", "hook": "h",
                "slides": [{"headline": "h", "supporting": "", "image_prompt": "p",
                            "visual_intent": ""}] * 3,
                "caption": "", "comment_trigger": "", "hashtags": [],
                "route": {"qa": {"hook": 9, "swipe_loop": 9, "identity_recognition": 9,
                                 "group_chat_share": 9, "comment_fight": 9,
                                 "saveability": 9, "visual_originality": 9,
                                 "dopamine_density": 9, "clarity": 9,
                                 "layout_safety": 9, "claim_safety": 9, "overall": 9}},
            }
            return LLMResult(content=json.dumps(post), prompt_tokens=10, completion_tokens=10)

    chat = CapturingChat()
    result = build_single_idea(store, settings, "G-0100", client=chat)
    assert result.status is Status.done
    assert "PERFORMANCE NOTES" in chat.user
    assert "hook 12000" in chat.user


def test_metrics_endpoint_and_insights_api(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    r = client.post(
        "/api/ideas/G-0001/metrics",
        data={"views": 20000, "likes": 900, "shares": 240},
    )
    assert r.status_code == 200
    with Store(settings.db_path) as store:
        m = json.loads(store.get_idea("G-0001").metrics_json)
        assert m["views"] == 20000 and m["shares"] == 240 and m["logged_at"]

    assert client.post("/api/ideas/G-0001/metrics", data={"views": -5}).status_code == 400
    assert client.post("/api/ideas/NOPE/metrics", data={"views": 1}).status_code == 404

    d = client.get("/api/insights").json()
    assert d["posts_logged"] == 1
    assert d["feeding_engine"] is False  # needs 3

    # Calendar cards carry the logged views.
    with Store(settings.db_path) as store:
        store.set_status("G-0001", Status.done)
        store.save_build("G-0001", {"slides_json": json.dumps([{"headline": "h"}])})
    data = client.get("/api/calendar").json()
    card = [c for c in data["tray"] if c["idea_id"] == "G-0001"][0]
    assert card["views"] == 20000
