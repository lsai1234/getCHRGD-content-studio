"""Tests for the Higgsfield video infrastructure — present but feature OFF."""

from __future__ import annotations

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.video import (
    HiggsfieldProvider,
    VideoDisabledError,
    VideoError,
    VideoJobStatus,
    ensure_video_enabled,
    get_video_provider,
    render_video,
    video_enabled,
)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


# --- feature flag: off by default -------------------------------------------


def test_video_disabled_by_default():
    assert video_enabled(Settings()) is False


def test_ensure_raises_when_disabled():
    with pytest.raises(VideoDisabledError):
        ensure_video_enabled(Settings())


def test_get_provider_blocked_when_disabled():
    with pytest.raises(VideoDisabledError):
        get_video_provider(Settings())


def test_render_video_blocked_when_disabled():
    with pytest.raises(VideoDisabledError):
        render_video(None, Settings(), None)


# --- when enabled: infra works, feature still not built ----------------------


def test_provider_requires_key_when_enabled():
    s = Settings(CHRGD_VIDEO_ENABLED=True)  # enabled but no key
    with pytest.raises(VideoError):
        get_video_provider(s)


def test_provider_constructs_with_key_but_calls_not_built():
    s = Settings(CHRGD_VIDEO_ENABLED=True, HIGGSFIELD_API_KEY="hf-test")
    provider = HiggsfieldProvider(s)
    assert provider._base == "https://platform.higgsfield.ai"
    with pytest.raises(NotImplementedError):
        provider.submit("img.jpg", "motion", seed=1)


def test_base_url_override_for_aggregator():
    s = Settings(
        CHRGD_VIDEO_ENABLED=True,
        HIGGSFIELD_API_KEY="k",
        CHRGD_HIGGSFIELD_BASE_URL="https://api.segmind.com/v1/",
    )
    provider = HiggsfieldProvider(s)
    assert provider._base == "https://api.segmind.com/v1"  # trailing slash trimmed


def test_render_video_when_enabled_points_to_m6():
    s = Settings(CHRGD_VIDEO_ENABLED=True, HIGGSFIELD_API_KEY="k")
    with pytest.raises(NotImplementedError):
        render_video(None, s, None)


# --- job status enum --------------------------------------------------------


def test_job_status_terminal():
    assert VideoJobStatus.COMPLETED.terminal
    assert VideoJobStatus.FAILED.terminal
    assert not VideoJobStatus.QUEUED.terminal
    assert not VideoJobStatus.PROCESSING.terminal


# --- jobs table -------------------------------------------------------------


def test_jobs_table_crud(store):
    store.add_idea_min = None  # noqa: (ensure fixture usable)
    from chrgd.models import Idea

    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    job_id = store.create_job("video", idea_id="G-0001", provider="higgsfield")
    job = store.get_job(job_id)
    assert job["status"] == "QUEUED"
    assert job["kind"] == "video"

    # An unfinished job is found so we never double-submit.
    assert store.active_job_for("G-0001", "video")["job_id"] == job_id

    store.update_job(job_id, status="COMPLETED", media_url="http://x/y.mp4")
    assert store.get_job(job_id)["status"] == "COMPLETED"
    # No longer active once terminal.
    assert store.active_job_for("G-0001", "video") is None
