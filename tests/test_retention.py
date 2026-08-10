"""Retention — the daily sweep that deletes content past its window.

This deletes real work, so the tests here are mostly about what it must NOT
take: anything still in flight, anything scheduled ahead, and the results the
learning loop reads back into every build.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.retention import LAST_SWEEP_KEY, prune, sweep_if_due

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
    )


@pytest.fixture()
def store(settings):
    settings.ensure_dirs()
    with Store(settings.db_path) as s:
        yield s


def _seed(store, settings, idea_id: str, *, age_days: int, assets: bool = True,
          status: Status = Status.done, **fields) -> Idea:
    """An idea created `age_days` ago, with a folder of rendered slides."""
    created = NOW - timedelta(days=age_days)
    store.add_idea(Idea(idea_id=idea_id, concept_note=idea_id, created_at=created))
    store.conn.execute(
        "UPDATE ideas SET status = ?, created_at = ? WHERE idea_id = ?",
        (status.value, created.isoformat(), idea_id),
    )
    if assets:
        out = settings.output_dir / idea_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "slide_1.jpg").write_bytes(b"x" * 2048)
        store.save_asset_paths(idea_id, [str(out / "slide_1.jpg")])
    for column, value in fields.items():
        store.conn.execute(
            f"UPDATE ideas SET {column} = ? WHERE idea_id = ?", (value, idea_id)
        )
    store.conn.commit()
    return store.get_idea(idea_id)


# --- what goes ---------------------------------------------------------------


def test_old_content_and_its_images_are_deleted(store, settings):
    _seed(store, settings, "G-0001", age_days=45)
    result = prune(store, settings, days=30, now=NOW)

    assert result.ideas_deleted == ["G-0001"]
    assert store.get_idea("G-0001") is None
    assert not (settings.output_dir / "G-0001").exists()
    assert result.bytes_freed >= 2048


def test_recent_content_is_untouched(store, settings):
    _seed(store, settings, "G-0001", age_days=10)
    result = prune(store, settings, days=30, now=NOW)

    assert result.ideas_deleted == []
    assert store.get_idea("G-0001") is not None
    assert (settings.output_dir / "G-0001" / "slide_1.jpg").exists()


def test_sweep_is_idempotent(store, settings):
    _seed(store, settings, "G-0001", age_days=45)
    prune(store, settings, days=30, now=NOW)
    again = prune(store, settings, days=30, now=NOW)
    assert again.total_ideas == 0


def test_orphaned_asset_folders_are_reclaimed(store, settings):
    """Assets left behind by an idea deleted from the UI."""
    stray = settings.output_dir / "G-9999"
    stray.mkdir(parents=True)
    (stray / "slide_1.jpg").write_bytes(b"y" * 1024)

    result = prune(store, settings, days=30, now=NOW)

    assert result.orphan_dirs_removed == 1
    assert not stray.exists()


@pytest.mark.parametrize("folder, name", [
    ("ready", "metricool.csv"),        # the export folder
    ("_brand", "character.png"),       # the LOCKED character portrait
])
def test_sidecar_folders_are_never_treated_as_ideas(store, settings, folder, name):
    sidecar = settings.output_dir / folder
    sidecar.mkdir(parents=True)
    (sidecar / name).write_bytes(b"keep me")

    result = prune(store, settings, days=30, now=NOW)

    assert result.orphan_dirs_removed == 0
    assert (sidecar / name).exists()


def test_old_runs_and_finished_jobs_go_too(store, settings):
    old = (NOW - timedelta(days=60)).isoformat()
    run_id = store.start_run("build")
    store.conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (old, run_id))
    job_id = store.create_job("build")
    store.conn.execute(
        "UPDATE jobs SET status = 'COMPLETED', updated_at = ? WHERE job_id = ?",
        (old, job_id),
    )
    store.conn.commit()

    result = prune(store, settings, days=30, now=NOW)

    assert result.runs_deleted == 1
    assert result.jobs_deleted == 1
    assert store.get_job(job_id) is None


# --- what survives -----------------------------------------------------------


def test_a_post_scheduled_ahead_is_not_old(store, settings):
    """Captured months ago, going out on Friday — age is the newest activity."""
    _seed(store, settings, "G-0001", age_days=90,
          scheduled_for=(NOW + timedelta(days=4)).isoformat())

    prune(store, settings, days=30, now=NOW)

    assert store.get_idea("G-0001") is not None
    assert (settings.output_dir / "G-0001" / "slide_1.jpg").exists()


def test_an_old_idea_built_yesterday_is_not_old(store, settings):
    _seed(store, settings, "G-0001", age_days=90,
          processed_at=(NOW - timedelta(days=1)).isoformat())

    prune(store, settings, days=30, now=NOW)

    assert store.get_idea("G-0001") is not None


def test_work_in_flight_is_never_swept(store, settings):
    _seed(store, settings, "G-0001", age_days=45)
    store.create_job("render", idea_id="G-0001")  # QUEUED

    result = prune(store, settings, days=30, now=NOW)

    assert result.total_ideas == 0
    assert store.get_idea("G-0001") is not None


def test_rated_posts_keep_their_row_and_lose_their_images(store, settings):
    """The learning loop reads these results into every build — the text stays."""
    _seed(store, settings, "G-0001", age_days=45)
    store.set_metrics("G-0001", {"rating": "hit", "views": 40000})

    result = prune(store, settings, days=30, keep_rated=True, now=NOW)

    assert result.ideas_stripped == ["G-0001"]
    idea = store.get_idea("G-0001")
    assert idea is not None
    assert json.loads(idea.metrics_json)["rating"] == "hit"
    assert idea.asset_paths_json is None
    assert not (settings.output_dir / "G-0001").exists()


def test_keep_rated_off_deletes_the_row_as_well(store, settings):
    _seed(store, settings, "G-0001", age_days=45)
    store.set_metrics("G-0001", {"rating": "hit"})

    result = prune(store, settings, days=30, keep_rated=False, now=NOW)

    assert result.ideas_deleted == ["G-0001"]
    assert store.get_idea("G-0001") is None


def test_retention_off_deletes_nothing(store, settings):
    _seed(store, settings, "G-0001", age_days=400)

    result = prune(store, settings, days=0, now=NOW)

    assert result.total_ideas == 0
    assert store.get_idea("G-0001") is not None


def test_dry_run_reports_without_deleting(store, settings):
    _seed(store, settings, "G-0001", age_days=45)

    result = prune(store, settings, days=30, dry_run=True, now=NOW)

    assert result.ideas_deleted == ["G-0001"]
    assert result.bytes_freed >= 2048
    assert store.get_idea("G-0001") is not None
    assert (settings.output_dir / "G-0001" / "slide_1.jpg").exists()


# --- the schedule ------------------------------------------------------------


def test_sweep_runs_once_a_day(store, settings):
    _seed(store, settings, "G-0001", age_days=45)
    _seed(store, settings, "G-0002", age_days=45)

    first = sweep_if_due(store, settings, now=NOW)
    assert first is not None and first.total_ideas == 2

    assert sweep_if_due(store, settings, now=NOW + timedelta(hours=6)) is None
    assert sweep_if_due(store, settings, now=NOW + timedelta(days=1, minutes=1)) is not None


def test_the_nightly_run_sweeps_too(store, settings):
    """A studio driven by the `chrgd run` timer never starts the web app."""
    from chrgd.orchestrate import run_chain

    _seed(store, settings, "G-0001", age_days=45)
    summary = run_chain(
        store, settings, 0, do_render=False, do_export=False, build_client=object()
    )

    assert summary.pruned is not None and "1 ideas deleted" in summary.pruned
    assert store.get_idea("G-0001") is None


def test_sweep_is_skipped_entirely_when_retention_is_off(store, tmp_path):
    off = Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_RETENTION_DAYS=0,
    )
    assert sweep_if_due(store, off, now=NOW) is None
    assert store.get_setting(LAST_SWEEP_KEY) is None


def test_default_window_is_thirty_days(settings):
    assert settings.retention_days == 30
    assert settings.retention_keep_rated is True


# --- the studio's own controls -----------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from chrgd.webapp import create_app

    web = Settings(
        CHRGD_DB_PATH=tmp_path / "web.db",
        CHRGD_OUTPUT_DIR=tmp_path / "webout",
        CHRGD_WEB_USERNAME="admin",
        CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )
    web.ensure_dirs()
    c = TestClient(create_app(web, run_worker=False))
    c.post("/login", data={"username": "admin", "password": "s3cret"},
           follow_redirects=False)
    c.chrgd_settings = web
    return c


def _seed_old(client, idea_id="G-0001", age_days=45):
    settings = client.chrgd_settings
    with Store(settings.db_path) as store:
        _seed(store, settings, idea_id, age_days=age_days)


def test_retention_endpoints_require_a_login(tmp_path):
    from fastapi.testclient import TestClient

    from chrgd.webapp import create_app

    web = Settings(
        CHRGD_DB_PATH=tmp_path / "w.db", CHRGD_OUTPUT_DIR=tmp_path / "o",
        CHRGD_WEB_USERNAME="admin", CHRGD_WEB_PASSWORD="pw",
        CHRGD_SECRET_KEY="k" * 16,
    )
    anon = TestClient(create_app(web, run_worker=False))
    assert anon.get("/api/retention").status_code == 401
    assert anon.post("/api/retention/sweep").status_code == 401


def test_preview_reports_without_deleting(client):
    _seed_old(client)
    body = client.get("/api/retention").json()

    assert body["days"] == 30
    assert body["preview"]["ideas"] == 1
    with Store(client.chrgd_settings.db_path) as store:
        assert store.get_idea("G-0001") is not None


def test_sweep_now_deletes_and_stamps_the_schedule(client):
    _seed_old(client)
    body = client.post("/api/retention/sweep").json()

    assert "1 ideas deleted" in body["summary"]
    with Store(client.chrgd_settings.db_path) as store:
        assert store.get_idea("G-0001") is None
        assert store.get_setting(LAST_SWEEP_KEY)


def test_settings_page_explains_the_window(client):
    body = client.get("/settings").text
    assert "30 days" in body
    assert "Clear out now" in body
