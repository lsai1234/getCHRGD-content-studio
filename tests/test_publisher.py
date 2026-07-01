"""Tests for milestone 4: the Metricool CSV export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.publisher import (
    MetricoolCSVPublisher,
    build_caption,
    load_columns,
)


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_OUTPUT_DIR=tmp_path / "out", CHRGD_DB_PATH=tmp_path / "t.db")


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _rendered_idea(store, settings, idea_id="G-0001"):
    """Insert a done idea with five fake rendered slide files on disk."""
    out = Path(settings.output_dir) / idea_id
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(1, 6):
        p = out / f"slide_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes
        paths.append(str(p))
    store.add_idea(Idea(idea_id=idea_id, concept_note="x"))
    store.save_build(
        idea_id,
        {
            "hook": "hook",
            "caption": "line one\nline two",
            "hashtags": json.dumps(["gym", "#uk"]),
            "slides_json": json.dumps([]),
        },
    )
    store.save_asset_paths(idea_id, paths)
    return paths


# --- caption handling -------------------------------------------------------


def test_caption_strips_line_breaks_and_appends_hashtags():
    out = build_caption("line one\nline two", ["gym", "#uk"], 2200)
    assert "\n" not in out
    assert out == "line one line two #gym #uk"


def test_caption_respects_max_length():
    out = build_caption("x" * 5000, [], 100)
    assert len(out) == 100
    assert out.endswith("…")


# --- config -----------------------------------------------------------------


def test_columns_load_from_repo_config():
    cols = load_columns()  # the real config/metricool_columns.toml
    assert cols.header.text
    assert "tiktok" in cols.networks
    assert len(cols.media.image_columns) == 5


# --- export -----------------------------------------------------------------


def test_export_writes_csv_and_copies_assets(store, settings):
    _rendered_idea(store, settings)
    pub = MetricoolCSVPublisher()
    result = pub.export(store, settings)

    assert len(result.exported_ids) == 1
    csv_path = Path(result.csv_path)
    assert csv_path.exists()

    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 1
    row = rows[0]
    # caption present, line breaks stripped, hashtags appended
    text_col = pub.cols.header.text
    assert "\n" not in row[text_col]
    assert "#gym" in row[text_col]
    # network markers set
    assert row[pub.cols.networks["tiktok"]] == pub.cols.format.network_on_value
    # five picture columns filled with lined-up filenames in ready/
    ready = Path(result.ready_dir)
    for i, col in enumerate(pub.cols.media.image_columns, 1):
        assert row[col] == f"G-0001_slide_{i}.jpg"
        assert (ready / f"G-0001_slide_{i}.jpg").exists()


def test_export_marks_exported_and_is_idempotent(store, settings):
    _rendered_idea(store, settings)
    pub = MetricoolCSVPublisher()
    first = pub.export(store, settings)
    assert first.exported_ids == ["G-0001"]
    assert store.get_idea("G-0001").exported_at is not None

    second = pub.export(store, settings)  # nothing new to export
    assert second.exported_ids == []
    assert second.csv_path is None


def test_export_skips_unrendered_done_posts(store, settings):
    store.add_idea(Idea(idea_id="G-0009", concept_note="x"))
    store.save_build("G-0009", {"hook": "h", "caption": "c", "hashtags": "[]"})
    pub = MetricoolCSVPublisher()
    result = pub.export(store, settings)
    assert result.exported_ids == []
    assert any("G-0009" in s[0] for s in result.skipped)


def test_export_respects_limit(store, settings):
    for n in range(3):
        _rendered_idea(store, settings, idea_id=f"G-000{n + 1}")
    pub = MetricoolCSVPublisher()
    result = pub.export(store, settings, limit=2)
    assert len(result.exported_ids) == 2


def test_export_schedules_across_days(store, settings):
    for n in range(2):
        _rendered_idea(store, settings, idea_id=f"G-000{n + 1}")
    pub = MetricoolCSVPublisher()
    result = pub.export(store, settings)
    d1 = store.get_idea("G-0001").scheduled_for
    d2 = store.get_idea("G-0002").scheduled_for
    assert d1 is not None and d2 is not None
    assert d2 > d1  # per_day=1 -> consecutive days


def test_url_media_reference(store, settings):
    _rendered_idea(store, settings)
    pub = MetricoolCSVPublisher()
    pub.cols.format.media_reference = "url"
    pub.cols.format.media_base_url = "https://cdn.example.com/chrgd"
    result = pub.export(store, settings)
    row = next(csv.DictReader(Path(result.csv_path).open()))
    assert (
        row[pub.cols.media.image_columns[0]]
        == "https://cdn.example.com/chrgd/G-0001_slide_1.jpg"
    )


def test_sample_csv(settings):
    pub = MetricoolCSVPublisher()
    path = pub.write_sample(settings)
    assert Path(path).exists()
    rows = list(csv.DictReader(Path(path).open()))
    assert len(rows) == 1
