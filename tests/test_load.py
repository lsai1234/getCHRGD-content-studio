"""Load behaviour — the screens must not get slower with every post ever made.

Each of these pinned a real regression: the review wall rendered every post it
had ever built into one page, the library table grew a row per idea forever, the
JSON backlog shipped every slide and every prose episode in one response, and
the calendar walked the whole database to find a handful of undated cards.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from chrgd.config import Settings
from chrgd.db import Store, shared_store
from chrgd.models import Idea, Status
from chrgd.webapp import _BACKLOG_PAGE, _REVIEW_LIMIT, _TRAY_LIMIT, create_app

SLIDES = json.dumps(
    [{"headline": "h" * 80, "supporting": "s" * 80, "image_prompt": "p" * 600}] * 6
)
ROUTE = json.dumps({"story": {"prose": "z" * 3000}, "qa": {"overall": 8}})


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_WEB_USERNAME="admin",
        CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )


def _seed(settings, n: int, *, status: Status = Status.done) -> None:
    settings.ensure_dirs()
    with Store(settings.db_path) as store:
        base = datetime.now(timezone.utc) - timedelta(days=n)
        for i in range(n):
            idea_id = f"G-{i:04d}"
            store.add_idea(
                Idea(idea_id=idea_id, concept_note=f"idea {i}",
                     created_at=base + timedelta(hours=i))
            )
            store.save_build(idea_id, {
                "post_type": "carousel", "hook": f"hook {i}", "slides_json": SLIDES,
                "caption": "c" * 300, "comment_trigger": "ct", "hashtags": '["#a"]',
                "route_json": ROUTE,
            })
            if status is not Status.done:
                store.set_status(idea_id, status)


@pytest.fixture()
def client(settings):
    c = TestClient(create_app(settings, run_worker=False))
    c.post("/login", data={"username": "admin", "password": "s3cret"},
           follow_redirects=False)
    return c


# --- the payload ceilings ----------------------------------------------------


def test_review_page_is_paged_not_the_whole_archive(settings, client):
    _seed(settings, _REVIEW_LIMIT + 15)
    body = client.get("/review").text

    assert body.count('class="post"') == _REVIEW_LIMIT
    assert "Older →" in body
    # Newest first, so the most recent post is the one you land on.
    assert f"G-{_REVIEW_LIMIT + 14:04d}" in body
    assert "G-0000" not in body


def test_review_page_two_shows_the_rest(settings, client):
    _seed(settings, _REVIEW_LIMIT + 5)
    body = client.get(f"/review?page=2").text
    assert body.count('class="post"') == 5
    assert "Newer" in body


def test_review_slide_images_load_lazily(settings, client):
    """A page of posts is a page of full-size images; the browser must not
    fetch every one of them before the page settles."""
    settings.ensure_dirs()
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        store.save_build("G-0001", {"slides_json": SLIDES, "hook": "h"})
        store.save_asset_paths("G-0001", ["out/G-0001/slide_1.jpg"])
    body = client.get("/review").text
    assert 'loading="lazy"' in body


def test_library_table_is_paged(settings, client):
    _seed(settings, _BACKLOG_PAGE + 20)
    body = client.get("/backlog").text

    assert body.count("/create?idea=") == _BACKLOG_PAGE
    assert "Older →" in body
    assert f"of {_BACKLOG_PAGE + 20}" in body


def test_api_backlog_returns_summaries_not_whole_posts(settings, client):
    """This endpoint used to ship every slide and every prose episode at once."""
    _seed(settings, 5)
    rows = client.get("/api/backlog").json()

    assert len(rows) == 5
    assert "slides_json" not in rows[0]
    assert "route_json" not in rows[0]
    assert rows[0]["built"] == 1
    assert rows[0]["idea_id"] == "G-0004"  # newest first


def test_api_backlog_is_capped_and_pageable(settings, client):
    _seed(settings, 30)
    assert len(client.get("/api/backlog?limit=10").json()) == 10
    page2 = client.get("/api/backlog?limit=10&offset=10").json()
    assert page2[0]["idea_id"] == "G-0019"


def test_api_backlog_full_still_available(settings, client):
    _seed(settings, 3)
    rows = client.get("/api/backlog?full=1").json()
    assert json.loads(rows[0]["slides_json"])[0]["headline"].startswith("h")


def test_calendar_tray_is_capped(settings, client):
    _seed(settings, _TRAY_LIMIT + 30)
    data = client.get("/api/calendar").json()
    assert len(data["tray"]) == _TRAY_LIMIT


def test_calendar_tray_holds_only_undated_built_posts(settings, client):
    settings.ensure_dirs()
    with Store(settings.db_path) as store:
        for idea_id in ("G-0001", "G-0002", "G-0003"):
            store.add_idea(Idea(idea_id=idea_id, concept_note=idea_id))
        store.save_build("G-0001", {"slides_json": SLIDES, "hook": "built, undated"})
        store.save_build("G-0002", {"slides_json": SLIDES, "hook": "built, dated"})
        store.set_schedule("G-0002", datetime.now(timezone.utc) + timedelta(days=1))
        # G-0003 is a seed row — never built, so not ammunition.

    tray = client.get("/api/calendar").json()["tray"]
    assert [c["idea_id"] for c in tray] == ["G-0001"]


def test_calendar_cards_carry_what_the_grid_draws(settings, client):
    settings.ensure_dirs()
    when = datetime.now(timezone.utc).replace(hour=18, minute=30, microsecond=0)
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="c"))
        store.save_build("G-0001", {"slides_json": SLIDES, "hook": "the hook"})
        store.save_asset_paths("G-0001", ["out/G-0001/slide_1.jpg"])
        store.set_schedule("G-0001", when)
        store.set_metrics("G-0001", {"views": 12000, "rating": "hit"})

    data = client.get(
        f"/api/calendar?date_from={(when - timedelta(days=1)).replace(tzinfo=None).isoformat()}"
        f"&date_to={(when + timedelta(days=1)).replace(tzinfo=None).isoformat()}"
    ).json()
    card = data["days"][when.date().isoformat()][0]

    assert card["label"] == "the hook"
    assert card["built"] is True
    assert card["rendered"] is True
    assert card["thumb"] == "slide_1.jpg"
    assert card["views"] == 12000
    assert card["rating"] == "hit"
    # The grid slices HH:MM straight out of this string.
    assert card["scheduled_for"][11:16] == "18:30"


# --- the connection ----------------------------------------------------------


def test_a_thread_reuses_one_connection(settings):
    settings.ensure_dirs()
    first = shared_store(settings.db_path)
    with shared_store(settings.db_path) as borrowed:
        assert borrowed is first
    # A `with` block must not close a connection it only borrowed.
    assert shared_store(settings.db_path) is first
    assert first.conn.execute("SELECT 1").fetchone()[0] == 1


def test_a_recreated_database_is_prepared_again(tmp_path):
    """The prepared-schema cache is a hint, not a promise."""
    path = tmp_path / "t.db"
    Store(path).close()
    path.unlink()
    with Store(path) as store:
        assert store.count() == 0  # the schema is there, not an OperationalError


def test_recency_scans_do_not_read_the_whole_library(settings):
    """The create screen's "used recently" nudges are bounded by `scan`."""
    settings.ensure_dirs()
    with Store(settings.db_path) as store:
        for i in range(10):
            store.add_idea(Idea(idea_id=f"G-{i:04d}", concept_note=f"i{i}"))
            store.save_build(f"G-{i:04d}", {"route_json": json.dumps({"marker": i})})

        assert len(store.recent_routes('"marker"', scan=3)) == 3
        assert store.recent_routes('"nothing-here"') == []
        newest = store.recent_routes('"marker"', scan=100, limit=1)
        assert newest[0]["idea_id"] == "G-0009"
