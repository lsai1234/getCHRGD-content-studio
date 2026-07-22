"""Today's pick (Bet 3): gather candidates from already-run scans, choose one
with an injectable selector, and degrade safely."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.todayspick import gather_candidates, todays_pick
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


def test_no_scans_reports_warming(store, settings):
    out = todays_pick(store, settings, selector=object())
    assert out["status"] == "warming"
    assert out["pick"] is None


def test_gather_round_robins_across_lanes(store):
    _completed_scan(store, "moments", [{"title": "Heatwave hits"}, {"title": "England win"}])
    _completed_scan(store, "ragebait", [{"title": "PTs are a waste"}])
    cands = gather_candidates(store)
    lanes = [c.lane for c in cands]
    titles = [c.title for c in cands]
    # Both lanes represented; the moments lane leads (priority order).
    assert "moments" in lanes and "ragebait" in lanes
    assert titles[0] == "Heatwave hits"
    # Only the latest completed scan per lane is used and empty titles are dropped.
    assert "England win" in titles


def test_gather_uses_only_the_latest_scan_per_lane(store):
    _completed_scan(store, "moments", [{"title": "old story"}])
    _completed_scan(store, "moments", [{"title": "fresh story"}])
    titles = [c.title for c in gather_candidates(store)]
    assert titles == ["fresh story"]


class _FakeSelector:
    def __init__(self, payload):
        self._payload = payload

    def select(self, system, user):
        self._system, self._user = system, user
        return json.dumps(self._payload)


def test_selector_shapes_the_pick_and_maps_build_route(store, settings):
    m_job = _completed_scan(store, "moments", [
        {"title": "3am kick-off", "why": "everyone's up late"},
        {"title": "heatwave", "why": "hottest weekend"},
    ])
    sel = _FakeSelector({
        "pick_index": 1,
        "why_today": "the heatwave is the shared moment right now",
        "build_angle": "training in 32°C without dying",
        "slide1_concept": "sweat-drenched phone selfie in a boiling PureGym",
        "cast": "a fit UK gym-goer mid-session",
        "alternate_indices": [0],
    })
    out = todays_pick(store, settings, selector=sel)
    assert out["status"] == "ready" and out["shaped"] is True
    pick = out["pick"]
    assert pick["title"] == "heatwave"
    assert pick["why_today"].startswith("the heatwave")
    assert pick["build_angle"] == "training in 32°C without dying"
    assert pick["slide1_concept"]
    # The build route points back at the REAL scan job + story index (one-tap
    # build reuses the existing moment-use endpoint, no new scan).
    assert pick["job_id"] == m_job and pick["moment"] == 1
    assert [a["title"] for a in out["alternates"]] == ["3am kick-off"]


def test_bad_selector_json_falls_back_to_top_candidate(store, settings):
    _completed_scan(store, "moments", [{"title": "top story", "why": "big"}])

    class Broken:
        def select(self, system, user):
            return "not json at all"

    out = todays_pick(store, settings, selector=Broken())
    assert out["status"] == "ready" and out["shaped"] is False
    assert out["pick"]["title"] == "top story"


def test_out_of_range_pick_index_falls_back(store, settings):
    _completed_scan(store, "moments", [{"title": "only one"}])
    sel = _FakeSelector({"pick_index": 9, "alternate_indices": []})
    out = todays_pick(store, settings, selector=sel)
    assert out["shaped"] is False
    assert out["pick"]["title"] == "only one"


def test_endpoint_warming_when_cold(settings):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    r = c.get("/api/todays-pick")
    assert r.status_code == 200
    assert r.json()["status"] == "warming"
