"""Bulk download — every post's content, one folder each, in one zip.

The archive is the thing a user opens on another machine, so most of what
matters here is shape: folders that sort in posting order, slides that keep
their order past slide 9, and nothing in the zip that came from outside
`output/`.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chrgd import bundle
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.webapp import create_app

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


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
    settings.ensure_dirs()
    with Store(settings.db_path) as s:
        yield s


def _built(
    store,
    settings,
    idea_id: str,
    *,
    slides: int = 2,
    hook: str = "The gym lied to you",
    scheduled: datetime | None = None,
    status: Status = Status.done,
    caption: str = "here's what actually works",
    comment_trigger: str = "which one are you?",
) -> Idea:
    """A finished post with `slides` rendered images on disk."""
    store.add_idea(Idea(idea_id=idea_id, concept_note=idea_id, created_at=NOW))
    out = settings.output_dir / idea_id
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in range(1, slides + 1):
        f = out / f"slide_{n}.jpg"
        f.write_bytes(b"jpegdata" * n)
        paths.append(str(f))
    store.save_build(
        idea_id,
        {
            "hook": hook,
            "slides_json": json.dumps(
                [{"headline": f"line {n}", "supporting": "s", "role": "hook"}
                 for n in range(1, slides + 1)]
            ),
            "caption": caption,
            "comment_trigger": comment_trigger,
            "hashtags": json.dumps(["gym", "#uk"]),
            "route_json": json.dumps({"show": "AMP", "qa": {"overall": 9}}),
            "asset_paths_json": json.dumps(paths),
            "scheduled_for": scheduled,
        },
    )
    if status is not Status.done:
        store.set_status(idea_id, status)
    return store.get_idea(idea_id)


def _zip(store, settings, **kw) -> tuple[zipfile.ZipFile, bundle.BundleResult]:
    buf = io.BytesIO()
    result = bundle.bundle_for(store, settings, dest=buf, name="B", **kw)
    buf.seek(0)
    return zipfile.ZipFile(buf), result


# --- what lands in a post's folder ------------------------------------------


def test_each_post_gets_a_folder_of_its_content(store, settings):
    _built(store, settings, "G-0001")
    zf, result = _zip(store, settings)
    folder = result.entries[0].folder
    names = {n.split("/", 2)[-1] for n in zf.namelist() if n.startswith(f"B/{folder}/")}
    assert names == {
        "slide_01.jpg", "slide_02.jpg",
        "caption.txt", "first_comment.txt", "post.md", "post.json",
    }


def test_caption_file_is_paste_ready(store, settings):
    _built(store, settings, "G-0001")
    zf, result = _zip(store, settings)
    text = zf.read(f"B/{result.entries[0].folder}/caption.txt").decode()
    # Caption, blank line, hashtags — the same block the manual modal copies,
    # with bare tags hashed and already-hashed ones left alone.
    assert text.rstrip("\n") == "here's what actually works\n\n#gym #uk"


def test_first_comment_only_when_there_is_one(store, settings):
    _built(store, settings, "G-0001", comment_trigger="")
    zf, result = _zip(store, settings)
    assert f"B/{result.entries[0].folder}/first_comment.txt" not in zf.namelist()


def test_post_json_carries_the_whole_record(store, settings):
    _built(store, settings, "G-0001")
    zf, result = _zip(store, settings)
    rec = json.loads(zf.read(f"B/{result.entries[0].folder}/post.json"))
    assert rec["idea_id"] == "G-0001"
    assert rec["hook"] == "The gym lied to you"
    assert rec["hashtags"] == ["gym", "#uk"]
    assert rec["route"]["show"] == "AMP"
    assert len(rec["slides"]) == 2


def test_post_md_reads_as_the_post(store, settings):
    _built(store, settings, "G-0001")
    zf, result = _zip(store, settings)
    md = zf.read(f"B/{result.entries[0].folder}/post.md").decode()
    assert md.startswith("# The gym lied to you")
    assert "## Slides" in md and "### Slide 2" in md
    assert "here's what actually works" in md
    assert "which one are you?" in md


def test_slide_numbers_stay_in_order_past_nine(store, settings):
    _built(store, settings, "G-0001", slides=10)
    zf, result = _zip(store, settings)
    folder = result.entries[0].folder
    slides = sorted(n for n in zf.namelist() if "/slide_" in n)
    # Zero-padded, so an alphabetical listing is still slide order.
    assert slides[0].endswith("slide_01.jpg")
    assert slides[-1].endswith("slide_10.jpg")


def test_video_file_comes_along(store, settings):
    _built(store, settings, "G-0001")
    (settings.output_dir / "G-0001" / "video.mp4").write_bytes(b"mp4")
    zf, result = _zip(store, settings)
    assert f"B/{result.entries[0].folder}/video.mp4" in zf.namelist()


def test_working_files_stay_out(store, settings):
    _built(store, settings, "G-0001")
    folder = settings.output_dir / "G-0001"
    (folder / "bg_1.jpg").write_bytes(b"bg")
    (folder / "clips").mkdir()
    (folder / "clips" / "clip_1.mp4").write_bytes(b"clip")
    zf, _ = _zip(store, settings)
    assert not any("bg_1" in n or "clip_1" in n for n in zf.namelist())


# --- folder naming and ordering ---------------------------------------------


def test_folders_are_named_for_the_post_and_sort_in_posting_order(store, settings):
    _built(store, settings, "G-0002", hook="Second up", scheduled=NOW + timedelta(days=1))
    _built(store, settings, "G-0001", hook="First up!", scheduled=NOW)
    _, result = _zip(store, settings)
    assert [e.folder for e in result.entries] == [
        "001_G-0001_first-up", "002_G-0002_second-up",
    ]


def test_unscheduled_posts_sort_after_scheduled_ones(store, settings):
    _built(store, settings, "G-0002", hook="No date")
    _built(store, settings, "G-0001", hook="Has a date", scheduled=NOW + timedelta(days=5))
    _, result = _zip(store, settings)
    assert [e.idea_id for e in result.entries] == ["G-0001", "G-0002"]


def test_a_hook_with_no_usable_characters_still_gets_a_folder(store, settings):
    _built(store, settings, "G-0001", hook="🔥🔥🔥")
    _, result = _zip(store, settings)
    assert result.entries[0].folder == "001_G-0001"


# --- the index and the readme -----------------------------------------------


def test_index_csv_lists_every_post(store, settings):
    _built(store, settings, "G-0001")
    _built(store, settings, "G-0002", hook="Another")
    zf, _ = _zip(store, settings)
    rows = list(csv.DictReader(io.StringIO(zf.read("B/index.csv").decode())))
    assert [r["idea_id"] for r in rows] == ["G-0001", "G-0002"]
    assert rows[0]["images"] == "2"


def test_readme_explains_the_archive(store, settings):
    _built(store, settings, "G-0001")
    zf, _ = _zip(store, settings)
    readme = zf.read("B/README.txt").decode()
    assert "1 post(s)" in readme and "caption.txt" in readme


# --- selection ---------------------------------------------------------------


def test_bare_seeds_are_skipped_with_a_reason(store, settings):
    store.add_idea(Idea(idea_id="G-0009", concept_note="never built"))
    _built(store, settings, "G-0001")
    _, result = _zip(store, settings)
    assert [e.idea_id for e in result.entries] == ["G-0001"]
    assert result.skipped == [("G-0009", "not built yet")]


def test_include_unbuilt_takes_the_seeds_too(store, settings):
    store.add_idea(Idea(idea_id="G-0009", concept_note="never built"))
    _, result = _zip(store, settings, include_unbuilt=True)
    assert [e.idea_id for e in result.entries] == ["G-0009"]


def test_status_filter(store, settings):
    _built(store, settings, "G-0001")
    _built(store, settings, "G-0002", status=Status.review)
    _, result = _zip(store, settings, status=Status.review)
    assert [e.idea_id for e in result.entries] == ["G-0002"]


def test_date_range_is_the_scheduled_slot(store, settings):
    _built(store, settings, "G-0001", scheduled=NOW)
    _built(store, settings, "G-0002", scheduled=NOW + timedelta(days=10))
    _, result = _zip(
        store, settings,
        date_from=datetime(2026, 8, 24), date_to=datetime(2026, 8, 25),
    )
    assert [e.idea_id for e in result.entries] == ["G-0001"]


def test_ids_select_exactly_those_posts(store, settings):
    _built(store, settings, "G-0001")
    _built(store, settings, "G-0002")
    _, result = _zip(store, settings, ids=["G-0002", "nope"])
    assert [e.idea_id for e in result.entries] == ["G-0002"]


def test_limit_caps_the_archive(store, settings):
    for n in range(4):
        _built(store, settings, f"G-000{n}", scheduled=NOW + timedelta(days=n))
    _, result = _zip(store, settings, limit=2)
    assert result.posts == 2


# --- safety ------------------------------------------------------------------


def test_an_asset_path_outside_output_is_never_zipped(store, settings, tmp_path):
    """A row pointing anywhere else on the box yields nothing, not a leak."""
    outsider = tmp_path / "secrets.txt"
    outsider.write_text("not yours")
    _built(store, settings, "G-0001", slides=1)
    store.save_asset_paths("G-0001", [str(outsider)])
    zf, result = _zip(store, settings)
    assert result.entries[0].images == 0
    assert not any("secrets" in n for n in zf.namelist())


def test_an_id_can_never_climb_out_of_its_folder(store, settings):
    """Ids become paths inside an archive someone extracts — nothing escapes."""
    assert bundle._safe_name("../../etc/passwd") == "etc-passwd"
    assert bundle._safe_name("..") == "post"
    assert bundle._safe_name("") == "post"
    assert bundle._safe_name("G-0001") == "G-0001"


def test_an_odd_id_still_zips_to_a_flat_folder(store, settings):
    store.add_idea(Idea(idea_id="../escape", concept_note="x"))
    store.save_build("../escape", {"hook": "Hi", "caption": "c"})
    zf, result = _zip(store, settings)
    assert result.entries[0].folder == "001_escape_hi"
    assert not any(".." in n for n in zf.namelist())


def test_a_missing_asset_file_does_not_break_the_archive(store, settings):
    _built(store, settings, "G-0001", slides=2)
    (settings.output_dir / "G-0001" / "slide_2.jpg").unlink()
    zf, result = _zip(store, settings)
    assert result.entries[0].images == 1
    assert zf.testzip() is None


# --- estimate ----------------------------------------------------------------


def test_estimate_counts_without_building(store, settings):
    _built(store, settings, "G-0001", slides=2)
    est = bundle.estimate(store, settings)
    assert est["posts"] == 1 and est["files"] == 2
    assert est["bytes"] == len(b"jpegdata") * 3  # slide 1 + slide 2
    assert est["size"].endswith("B")


def test_estimate_of_nothing_is_zero(store, settings):
    assert bundle.estimate(store, settings)["posts"] == 0


# --- the web routes -----------------------------------------------------------


@pytest.fixture()
def client(settings):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    return c


def test_download_route_needs_auth(settings):
    anon = TestClient(create_app(settings))
    assert anon.get("/download/posts.zip").status_code == 401


def test_download_route_returns_a_real_zip(client, settings):
    with Store(settings.db_path) as s:
        _built(s, settings, "G-0001")
    r = client.get("/download/posts.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert ".zip" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    assert any(n.endswith("index.csv") for n in zf.namelist())


def test_download_route_cleans_up_its_temp_file(client, settings):
    with Store(settings.db_path) as s:
        _built(s, settings, "G-0001")
    client.get("/download/posts.zip")
    assert not list(Path(settings.output_dir).glob(".bundle_*"))


def test_download_route_404s_when_nothing_matches(client):
    assert client.get("/download/posts.zip").status_code == 404


def test_download_route_rejects_an_unknown_scope(client):
    assert client.get("/download/posts.zip?scope=sideways").status_code == 400


def test_download_route_rejects_a_bad_date(client):
    r = client.get("/download/posts.zip?scope=scheduled&date_from=nope&date_to=nope")
    assert r.status_code == 400


def test_single_post_route(client, settings):
    with Store(settings.db_path) as s:
        _built(s, settings, "G-0001")
    r = client.get("/download/post/G-0001.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "G-0001/001_G-0001_the-gym-lied-to-you/caption.txt" in zf.namelist()


def test_single_post_route_404s_on_an_unknown_id(client):
    assert client.get("/download/post/G-9999.zip").status_code == 404


def test_estimate_route(client, settings):
    with Store(settings.db_path) as s:
        _built(s, settings, "G-0001")
    body = client.get("/api/download/estimate").json()
    assert body["posts"] == 1 and body["files"] == 2
