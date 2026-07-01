"""Tests for Phase A1: web backend + single-user auth (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.webapp import create_app
from chrgd.webauth import hash_password, verify_credentials


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
def client(settings):
    return TestClient(create_app(settings))


def _login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "s3cret"},
        follow_redirects=False,
    )


# --- auth mechanics ---------------------------------------------------------


def test_password_hash_roundtrip():
    h = hash_password("hunter2")
    s = Settings(CHRGD_WEB_USERNAME="admin", CHRGD_WEB_PASSWORD_HASH=h)
    assert verify_credentials(s, "admin", "hunter2")
    assert not verify_credentials(s, "admin", "wrong")
    assert not verify_credentials(s, "someone", "hunter2")


def test_plaintext_password_verify():
    s = Settings(CHRGD_WEB_USERNAME="admin", CHRGD_WEB_PASSWORD="pw")
    assert verify_credentials(s, "admin", "pw")
    assert not verify_credentials(s, "admin", "nope")


def test_no_password_configured_refuses():
    s = Settings(CHRGD_WEB_USERNAME="admin")
    assert not verify_credentials(s, "admin", "anything")


# --- route protection -------------------------------------------------------


def test_home_redirects_when_anonymous(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"


def test_api_requires_auth(client):
    assert client.get("/api/backlog").status_code == 401


def test_healthz_is_open(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_login_bad_credentials(client):
    r = client.post("/login", data={"username": "admin", "password": "x"})
    assert r.status_code == 401


def test_login_then_access(client):
    r = _login(client)
    assert r.status_code == 303
    assert client.get("/").status_code == 200  # session cookie carried by client
    assert client.get("/api/backlog").status_code == 200


def test_logout_clears_session(client):
    _login(client)
    assert client.get("/api/backlog").status_code == 200
    client.post("/logout", follow_redirects=False)
    assert client.get("/api/backlog").status_code == 401


# --- API actions (no paid calls) --------------------------------------------


def test_capture_via_api(client):
    _login(client)
    r = client.post("/api/capture", data={"dump": "idea one\nidea two"})
    body = r.json()
    assert len(body["created"]) == 2
    assert client.get("/api/backlog").json().__len__() == 2


def test_approve_flagged_post(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        store.mark_review("G-0001", {"hook": "h"})
    _login(client)
    r = client.post("/api/ideas/G-0001/approve")
    assert r.json()["status"] == "done"
    with Store(settings.db_path) as store:
        assert store.get_idea("G-0001").status is Status.done


def test_void_idea(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    _login(client)
    client.post("/api/ideas/G-0001/void")
    with Store(settings.db_path) as store:
        assert store.get_idea("G-0001").status is Status.void


def test_render_via_api_dry_run(client, settings):
    with Store(settings.db_path) as store:
        slides = [
            {"headline": f"h{i}", "supporting": "s", "image_prompt": "p", "visual_intent": "v"}
            for i in range(5)
        ]
        store.add_idea(Idea(idea_id="G-0001", concept_note="x", slides_json=json.dumps(slides)))
    _login(client)
    r = client.post("/api/render/G-0001", params={"dry_run": True})
    assert len(r.json()["paths"]) == 5
    # And the rendered image is served, auth-gated.
    fname = Path(r.json()["paths"][0]).name
    assert client.get(f"/media/G-0001/{fname}").status_code == 200


# --- security ---------------------------------------------------------------


def test_media_path_traversal_blocked(client):
    _login(client)
    r = client.get("/media/G-0001/..%2f..%2f..%2fetc%2fpasswd")
    assert r.status_code == 404


def test_media_requires_auth(client):
    assert client.get("/media/G-0001/slide_1.jpg").status_code == 401


# --- background jobs (A2) ---------------------------------------------------


def test_build_job_endpoint_enqueues(client):
    _login(client)
    r = client.post("/api/jobs/build", data={"count": 2, "dry_run": True})
    assert "job_id" in r.json()
    # It's queued, waiting for the worker (not started in tests).
    job = client.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "QUEUED"
    assert job["kind"] == "build"


def test_render_job_endpoint_then_worker(client, settings):
    from chrgd.worker import Worker

    with Store(settings.db_path) as store:
        slides = [
            {"headline": f"h{i}", "supporting": "s", "image_prompt": "p", "visual_intent": "v"}
            for i in range(5)
        ]
        store.add_idea(Idea(idea_id="G-0001", concept_note="x", slides_json=json.dumps(slides)))
    _login(client)
    job_id = client.post("/api/jobs/render/G-0001", params={"dry_run": True}).json()["job_id"]

    Worker(settings).run_once()  # drive the queue once, deterministically

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert len(json.loads(job["result_json"])["paths"]) == 5


def test_render_job_missing_idea_404(client):
    _login(client)
    assert client.post("/api/jobs/render/NOPE").status_code == 404


# --- A4 pages + editing -----------------------------------------------------


def test_all_pages_render(client):
    _login(client)
    for path in ("/", "/backlog", "/build", "/trends", "/review", "/export"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "CHRGD" in r.text


def test_trends_job_enqueue_and_seed(client, settings):
    _login(client)
    # enqueue a scout job
    job_id = client.post("/api/jobs/trends", data={"count": 2}).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["kind"] == "trends"

    # simulate the worker completing it with a result
    with Store(settings.db_path) as store:
        store.update_job(
            job_id,
            status="COMPLETED",
            result_json=json.dumps(
                {
                    "limitation": "topical only",
                    "trends": [
                        {"trend": "t", "concept_note": "the fan everyone fights over",
                         "decay_speed": "days", "mechanic": "rage_agreement"}
                    ],
                }
            ),
        )
    r = client.post(f"/api/trends/seed/{job_id}")
    assert r.json()["created"]  # one row seeded
    with Store(settings.db_path) as store:
        seeded = store.list_ideas(status=Status.queued)
        assert any(i.content_category == "trend" for i in seeded)


def test_seed_unknown_trends_job_404(client):
    _login(client)
    assert client.post("/api/trends/seed/999").status_code == 404


def test_pages_redirect_when_anonymous(client):
    for path in ("/backlog", "/build", "/export"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/login"


def test_edit_updates_copy_and_keeps_review_status(client, settings):
    with Store(settings.db_path) as store:
        slides = [{"headline": "old", "supporting": "old", "image_prompt": "p", "visual_intent": "v"}]
        store.add_idea(Idea(idea_id="G-0001", concept_note="x", slides_json=json.dumps(slides)))
        store.mark_review("G-0001", {"hook": "old hook"})
    _login(client)
    r = client.post(
        "/api/ideas/G-0001/edit",
        data={
            "hook": "new hook",
            "slide_headline_0": "new headline",
            "slide_supporting_0": "new support",
            "caption": "new cap",
            "hashtags": "gym uk",
        },
    )
    assert r.json()["saved"] is True
    with Store(settings.db_path) as store:
        idea = store.get_idea("G-0001")
        assert idea.hook == "new hook"
        assert idea.status is Status.review  # edit didn't silently approve it
        slides = json.loads(idea.slides_json)
        assert slides[0]["headline"] == "new headline"
        assert slides[0]["image_prompt"] == "p"  # untouched fields preserved
        assert json.loads(idea.hashtags) == ["#gym", "#uk"]


def test_review_page_shows_slide_previews(client, settings):
    with Store(settings.db_path) as store:
        slides = [{"headline": f"h{i}", "supporting": "s", "image_prompt": "p", "visual_intent": "v"} for i in range(5)]
        store.add_idea(Idea(idea_id="G-0001", concept_note="x", slides_json=json.dumps(slides)))
        store.mark_review("G-0001", {"hook": "h"})
        store.save_asset_paths("G-0001", [f"/out/G-0001/slide_{i}.jpg" for i in range(1, 6)])
    _login(client)
    html = client.get("/review").text
    assert "/media/G-0001/slide_1.jpg" in html
    assert "Approve" in html


def test_export_run_via_page(client, settings, tmp_path):
    # a done + rendered idea
    out = Path(settings.output_dir) / "G-0001"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(1, 6):
        p = out / f"slide_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        paths.append(str(p))
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        store.save_build("G-0001", {"hook": "h", "caption": "c", "hashtags": "[]", "slides_json": "[]"})
        store.save_asset_paths("G-0001", paths)
    _login(client)
    r = client.post("/export", follow_redirects=False)
    assert r.status_code == 303
    with Store(settings.db_path) as store:
        assert store.get_idea("G-0001").exported_at is not None
    # zip download works
    assert client.get("/download/assets.zip").status_code == 200
