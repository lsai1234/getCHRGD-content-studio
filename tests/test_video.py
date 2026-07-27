"""Tests for the Higgsfield image-to-video pipeline (M6).

The feature ships OFF by default; these exercise the built pipeline offline —
no paid provider calls, no ffmpeg binary required. Provider HTTP is driven
through a fake client, and ffmpeg is faked so the assembly logic is covered
without the binary present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image as PILImage

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea
from chrgd import video as video_mod
from chrgd.video import (
    HiggsfieldProvider,
    VideoDisabledError,
    VideoError,
    VideoJobStatus,
    _data_uri,
    _map_status,
    assemble_clips,
    build_audio_mux_command,
    build_concat_command,
    build_normalize_command,
    ensure_video_enabled,
    get_video_provider,
    motion_prompt_for_slide,
    render_video,
    video_enabled,
)


# --- fixtures / fakes -------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _enabled_settings(tmp_path, **over):
    base = dict(
        CHRGD_VIDEO_ENABLED=True,
        HIGGSFIELD_API_KEY="hf-test",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_VIDEO_POLL_INTERVAL=0,  # don't sleep in tests
    )
    base.update(over)
    return Settings(**base)


class FakeResp:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data or {}
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        yield self._content


class FakeClient:
    """Stand-in for the provider's httpx client — records calls, returns fakes."""

    def __init__(self, submit_json=None, poll_json=None, download=b"clip-bytes"):
        self.submit_json = submit_json or {"id": "gen-123"}
        self.poll_json = poll_json or {"status": "completed", "results": [{"url": "http://x/y.mp4"}]}
        self.download = download
        self.posts, self.gets, self.streams = [], [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, path, json=None):
        self.posts.append((path, json))
        return FakeResp(self.submit_json)

    def get(self, path):
        self.gets.append(path)
        return FakeResp(self.poll_json)

    def stream(self, method, url):
        self.streams.append((method, url))
        return FakeResp(content=self.download)


class FakeProvider:
    """Deterministic provider: submit returns ids, poll completes immediately."""

    def __init__(self):
        self.submits, self.polls, self.downloads = [], [], []
        self._n = 0

    def submit(self, image_path, motion_prompt, *, seed):
        self.submits.append((image_path, motion_prompt, seed))
        self._n += 1
        return f"gen-{self._n}"

    def poll(self, external_id):
        self.polls.append(external_id)
        return VideoJobStatus.COMPLETED, f"http://x/{external_id}.mp4"

    def download(self, media_url, dest_path):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"clip")
        self.downloads.append((media_url, dest_path))
        return dest_path


def _fake_ffmpeg(argv, *, notify=None):
    """Pretend ffmpeg ran: create whatever output file the argv names last."""
    Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
    Path(argv[-1]).write_bytes(b"video")


def _rendered_idea(store, settings, idea_id="G-0001", n=2, route=None):
    """An idea whose n slide images exist on disk (what a reel is built from)."""
    out = Path(settings.output_dir) / idea_id
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = out / f"slide_{i + 1}.jpg"
        PILImage.new("RGB", (108, 162)).save(p, "JPEG")
        paths.append(str(p))
    idea = Idea(
        idea_id=idea_id, concept_note="x",
        asset_paths_json=json.dumps(paths),
        route_json=json.dumps(route) if route else None,
    )
    store.add_idea(idea)
    return idea, paths


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


def test_provider_requires_key_when_enabled():
    s = Settings(CHRGD_VIDEO_ENABLED=True)  # enabled but no key
    with pytest.raises(VideoError):
        get_video_provider(s)


# --- provider HTTP shapes (fake client, no real network) ---------------------


def test_provider_submit_sends_image_and_prompt(tmp_path, monkeypatch):
    img = tmp_path / "slide.jpg"
    PILImage.new("RGB", (10, 12)).save(img, "JPEG")
    provider = HiggsfieldProvider(_enabled_settings(tmp_path))
    fake = FakeClient(submit_json={"generation_id": "abc-9"})
    monkeypatch.setattr(provider, "_client", lambda: fake)

    ext = provider.submit(str(img), "gentle push-in", seed=7)
    assert ext == "abc-9"  # id parsed from the aggregator spelling too
    path, payload = fake.posts[0]
    assert path == HiggsfieldProvider.SUBMIT_PATH
    assert payload["input_image"].startswith("data:image/jpeg;base64,")
    assert payload["prompt"] == "gentle push-in"
    assert payload["seed"] == 7


def test_provider_poll_maps_status_and_url(tmp_path, monkeypatch):
    provider = HiggsfieldProvider(_enabled_settings(tmp_path))
    monkeypatch.setattr(
        provider, "_client",
        lambda: FakeClient(poll_json={"status": "in_progress"}),
    )
    status, url = provider.poll("gen-1")
    assert status is VideoJobStatus.PROCESSING and url is None

    monkeypatch.setattr(
        provider, "_client",
        lambda: FakeClient(poll_json={"status": "succeeded", "video_url": "http://x/done.mp4"}),
    )
    status, url = provider.poll("gen-1")
    assert status is VideoJobStatus.COMPLETED and url == "http://x/done.mp4"


def test_provider_download_streams_to_disk(tmp_path, monkeypatch):
    provider = HiggsfieldProvider(_enabled_settings(tmp_path))
    monkeypatch.setattr(provider, "_client", lambda: FakeClient(download=b"MP4DATA"))
    dest = tmp_path / "clips" / "clip_1.mp4"
    provider.download("http://x/y.mp4", str(dest))
    assert dest.read_bytes() == b"MP4DATA"


def test_status_map_is_liberal():
    assert _map_status("QUEUED") is VideoJobStatus.QUEUED
    assert _map_status("Running") is VideoJobStatus.PROCESSING
    assert _map_status("succeeded") is VideoJobStatus.COMPLETED
    assert _map_status("failed") is VideoJobStatus.FAILED
    assert _map_status("error") is VideoJobStatus.ERROR
    # Unknown → treated as still-processing rather than crashing the poll loop.
    assert _map_status("weird") is VideoJobStatus.PROCESSING


def test_data_uri_requires_file(tmp_path):
    with pytest.raises(VideoError):
        _data_uri(str(tmp_path / "missing.jpg"))


def test_base_url_override_for_aggregator(tmp_path):
    provider = HiggsfieldProvider(
        _enabled_settings(tmp_path, CHRGD_HIGGSFIELD_BASE_URL="https://api.segmind.com/v1/")
    )
    assert provider._base == "https://api.segmind.com/v1"  # trailing slash trimmed


# --- motion prompt ----------------------------------------------------------


def test_motion_prompt_locks_frame_and_uses_evolution():
    p = motion_prompt_for_slide(1, 4, {"evolution": "palette heats toward red"})
    assert "do not add, remove or restyle any text" in p
    assert "palette heats toward red" in p
    # First and last slides get distinct beats.
    assert "stop-scroll" in motion_prompt_for_slide(0, 4, None)
    assert "hero beat" in motion_prompt_for_slide(3, 4, None)


# --- ffmpeg command builders (pure) -----------------------------------------


def test_normalize_command_scales_and_pads():
    cmd = build_normalize_command("in.mp4", "out.mp4", (1080, 1920), 30)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4"
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920" in vf and "fps=30" in vf
    assert "-an" in cmd  # source audio dropped before concat


def test_concat_and_audio_commands():
    concat = build_concat_command("list.txt", "reel.mp4")
    assert concat[:5] == ["ffmpeg", "-y", "-f", "concat", "-safe"]
    assert concat[-1] == "reel.mp4"

    mux = build_audio_mux_command("reel.mp4", "bed.mp3", "final.mp4")
    assert "-shortest" in mux and mux[-1] == "final.mp4"
    assert "0:v:0" in mux and "1:a:0" in mux


def test_run_ffmpeg_errors_without_binary(monkeypatch):
    monkeypatch.setattr(video_mod, "ffmpeg_available", lambda: False)
    with pytest.raises(VideoError, match="ffmpeg not found"):
        video_mod._run_ffmpeg(["ffmpeg", "-y", "x.mp4"])


# --- assembly ---------------------------------------------------------------


def test_assemble_draft_ships_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(video_mod, "_run_ffmpeg", _fake_ffmpeg)
    clips = []
    for i in range(2):
        c = tmp_path / f"clip_{i}.mp4"
        c.write_bytes(b"x")
        clips.append(str(c))
    out = tmp_path / "video.mp4"
    settings = _enabled_settings(tmp_path, CHRGD_AUDIO_MODE="draft")
    result = assemble_clips(clips, str(out), settings, audio_mode="draft")
    assert Path(result).exists()


def test_assemble_auto_muxes_bed_when_present(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        video_mod, "_run_ffmpeg",
        lambda argv, notify=None: (calls.append(argv), _fake_ffmpeg(argv))[1],
    )
    bed = tmp_path / "bed.mp3"
    bed.write_bytes(b"audio")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    settings = _enabled_settings(tmp_path, CHRGD_AUDIO_MODE="auto", CHRGD_VIDEO_AUDIO_BED=str(bed))
    assemble_clips([str(clip)], str(tmp_path / "video.mp4"), settings, audio_mode="auto")
    # The last ffmpeg call was the audio mux (bed present).
    assert any("-shortest" in argv for argv in calls)


def test_assemble_auto_without_bed_falls_back_to_silent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        video_mod, "_run_ffmpeg",
        lambda argv, notify=None: (calls.append(argv), _fake_ffmpeg(argv))[1],
    )
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    settings = _enabled_settings(tmp_path, CHRGD_AUDIO_MODE="auto")  # no bed configured
    out = tmp_path / "video.mp4"
    assemble_clips([str(clip)], str(out), settings, audio_mode="auto")
    assert out.exists()
    assert not any("-shortest" in argv for argv in calls)  # no mux attempted


# --- orchestrator: end-to-end (fake provider + fake ffmpeg) ------------------


def test_render_video_end_to_end(tmp_path, store, monkeypatch):
    monkeypatch.setattr(video_mod, "_run_ffmpeg", _fake_ffmpeg)
    settings = _enabled_settings(tmp_path)
    idea, _ = _rendered_idea(store, settings, n=3)
    job_id = store.create_job("video", idea_id=idea.idea_id, provider="higgsfield")
    provider = FakeProvider()

    path = render_video(idea, settings, store, job_id=job_id, provider=provider)

    assert path.endswith(f"{idea.idea_id}/video.mp4")
    assert Path(path).exists()
    assert len(provider.submits) == 3  # one clip per slide
    # Each clip landed under output/<idea>/clips/.
    for i in range(1, 4):
        assert (Path(settings.output_dir) / idea.idea_id / "clips" / f"clip_{i}.mp4").exists()
    job = store.get_job(job_id)
    assert job["output_path"] == path
    assert job["progress"] == 100


def test_render_video_reuses_seed_across_slides(tmp_path, store, monkeypatch):
    monkeypatch.setattr(video_mod, "_run_ffmpeg", _fake_ffmpeg)
    settings = _enabled_settings(tmp_path)
    idea, _ = _rendered_idea(store, settings, n=2)
    provider = FakeProvider()
    render_video(idea, settings, store, provider=provider, seed=42)
    assert {seed for _, _, seed in provider.submits} == {42}  # one seed, consistent character


def test_render_video_resumes_without_resubmitting(tmp_path, store, monkeypatch):
    monkeypatch.setattr(video_mod, "_run_ffmpeg", _fake_ffmpeg)
    settings = _enabled_settings(tmp_path)
    idea, _ = _rendered_idea(store, settings, n=2)
    job_id = store.create_job("video", idea_id=idea.idea_id, provider="higgsfield")

    # Simulate slide 0 already rendered on a prior (crashed) run.
    clip0 = Path(settings.output_dir) / idea.idea_id / "clips" / "clip_1.mp4"
    clip0.parent.mkdir(parents=True, exist_ok=True)
    clip0.write_bytes(b"done")
    store.update_job(job_id, result_json=json.dumps(
        {"clips": {"0": {"external_id": "old-0", "status": "COMPLETED", "path": str(clip0)}}}
    ))

    provider = FakeProvider()
    render_video(idea, settings, store, job_id=job_id, provider=provider)
    # Only slide 1 was submitted — slide 0 resumed from its downloaded clip.
    assert len(provider.submits) == 1
    assert provider.submits[0][0].endswith("slide_2.jpg")


def test_render_video_requires_rendered_slides(tmp_path, store):
    settings = _enabled_settings(tmp_path)
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))  # never rendered
    with pytest.raises(VideoError, match="no rendered slides"):
        render_video(store.get_idea("G-0001"), settings, store, provider=FakeProvider())


def test_render_video_raises_on_provider_failure(tmp_path, store, monkeypatch):
    settings = _enabled_settings(tmp_path)
    idea, _ = _rendered_idea(store, settings, n=1)

    class Failing(FakeProvider):
        def poll(self, external_id):
            return VideoJobStatus.FAILED, None

    with pytest.raises(VideoError, match="failed at the provider"):
        render_video(idea, settings, store, provider=Failing())


# --- worker + db wiring ------------------------------------------------------


def test_worker_video_job_completes(tmp_path, store, monkeypatch):
    from chrgd.worker import Worker, enqueue_video

    settings = _enabled_settings(tmp_path)
    idea, _ = _rendered_idea(store, settings, n=2)
    provider = FakeProvider()
    monkeypatch.setattr(video_mod, "_run_ffmpeg", _fake_ffmpeg)
    monkeypatch.setattr(video_mod, "get_video_provider", lambda s: provider)

    job_id = enqueue_video(store, idea.idea_id)
    assert enqueue_video(store, idea.idea_id) == job_id  # deduped, no re-submit

    worker = Worker(settings, kinds=frozenset({"video"}))
    assert worker.run_once() == job_id
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED", job["error"]
    assert job["output_path"].endswith("video.mp4")


def test_requeue_interrupted_video_jobs(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    job_id = store.create_job("video", idea_id="G-0001", provider="higgsfield")
    store.update_job(job_id, status="PROCESSING")
    # A build job left PROCESSING is reaped; a video job is requeued to resume.
    other = store.create_job("render", idea_id="G-0001")
    store.update_job(other, status="PROCESSING")

    assert store.requeue_interrupted_video_jobs() == 1
    assert store.get_job(job_id)["status"] == "QUEUED"
    assert store.get_job(other)["status"] == "PROCESSING"  # untouched here


# --- job status enum + jobs table -------------------------------------------


def test_job_status_terminal():
    assert VideoJobStatus.COMPLETED.terminal
    assert VideoJobStatus.FAILED.terminal
    assert not VideoJobStatus.QUEUED.terminal
    assert not VideoJobStatus.PROCESSING.terminal


def test_jobs_table_crud(store):
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
