"""Tests for the TikTok analytics CSV import (no key, no LLM)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from chrgd.analytics import import_tiktok_csv, parse_tiktok_csv
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea
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


CSV = (
    "Video title,Post time,Total Views,Total Likes,Total Comments,Total Shares,Total Saves\n"
    '"the 5 lads every gym has","2026-07-01","12,345",600,45,30,"1.2K"\n'
    '"toning isnt a thing and your PT knows it","2026-07-02",800,90,12,4,20\n'
    '"a random post that is not ours","2026-07-03",100,2,0,0,0\n'
)


# --- parsing ----------------------------------------------------------------


def test_parse_detects_columns_and_normalises_counts():
    rows = parse_tiktok_csv(CSV)
    assert len(rows) == 3
    first = rows[0]
    assert first["title"] == "the 5 lads every gym has"
    assert first["metrics"]["views"] == 12345      # comma stripped
    assert first["metrics"]["saves"] == 1200        # 1.2K expanded
    assert first["metrics"]["likes"] == 600


def test_parse_empty_or_headerless_is_safe():
    assert parse_tiktok_csv("") == []
    # A row with no numbers at all is skipped, not returned as zeros.
    assert parse_tiktok_csv("Video title,Total Views\n\"just a title\",\n") == []


# --- import + matching ------------------------------------------------------


def _idea(store, idea_id, caption):
    store.add_idea(Idea(idea_id=idea_id, concept_note=idea_id, caption=caption))


def test_import_matches_by_caption_and_merges(store):
    _idea(store, "G-0001", "the 5 lads every gym has (you're one of them)")
    _idea(store, "G-0002", "toning isn't a thing — and deep down you know it")
    # A rating already logged must survive the metric import.
    store.merge_metrics("G-0001", {"rating": "hit"})

    result = import_tiktok_csv(store, CSV)
    assert set(result["updated"]) == {"G-0001", "G-0002"}
    assert result["unmatched"] == ["a random post that is not ours"]

    m1 = json.loads(store.get_idea("G-0001").metrics_json)
    assert m1["views"] == 12345 and m1["saves"] == 1200
    assert m1["rating"] == "hit"            # preserved
    assert m1["source"] == "tiktok_csv"


def test_import_no_rows(store):
    _idea(store, "G-0001", "something")
    out = import_tiktok_csv(store, "not,a,real\nexport,with,metrics\n")
    assert out["updated"] == []
    assert "error" in out


def test_one_row_claims_one_idea(store):
    # Two near-identical captions; a single matching row must not double-log.
    _idea(store, "G-0001", "the 5 lads every gym has (part one)")
    _idea(store, "G-0002", "the 5 lads every gym has (part two)")
    csv_one = (
        "Video title,Total Views\n"
        '"the 5 lads every gym has","5000"\n'
    )
    result = import_tiktok_csv(store, csv_one)
    assert len(result["updated"]) == 1


# --- endpoint ---------------------------------------------------------------


def test_import_endpoint(settings, store):
    _idea(store, "G-0001", "the 5 lads every gym has (you're one of them)")
    client = TestClient(create_app(settings))
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    r = client.post(
        "/api/metrics/import",
        files={"file": ("export.csv", CSV.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert "G-0001" in body["updated"]
