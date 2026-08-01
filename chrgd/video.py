"""Image-to-video generation (Higgsfield) — milestone 6.

Turns a rendered carousel into a short vertical reel: each finished slide image
is animated into a short clip by the provider, then the clips are stitched in
slide order into one 9:16 file (optionally under a neutral audio bed).

Shape of the pipeline (all guarded by `settings.video_enabled`, default False):

  * `HiggsfieldProvider` — the submit → poll → download async lifecycle against
    the documented Higgsfield Cloud image2video API. The base URL can point at
    an aggregator (e.g. Segmind) so the provider swaps without code changes.
  * `assemble_clips` — ffmpeg normalise + concat (+ optional audio mux). The
    command construction is pure and unit-tested; the subprocess is guarded so a
    box without ffmpeg fails with a clear message instead of a stack trace.
  * `render_video` — the end-to-end orchestrator, driven off the `jobs` table so
    it is **resumable and never re-bills**: per-slide provider `external_id`s and
    downloaded clip paths are persisted after each step, so a crash mid-render
    resumes by re-polling / re-stitching rather than re-submitting paid clips.

Text is NOT burned on in ffmpeg: this engine bakes every word into the slide
artwork at image-gen time (`ai_design` mode), so each animated clip already
carries its copy. Re-overlaying would double it.

The HTTP request/response field names follow the documented lifecycle and are
isolated in small `_submit_payload` / `_parse_*` helpers so they can be
reconciled against the live API in one place. No paid call runs while the
feature flag is off, and the fake-client tests exercise the shapes offline.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import subprocess
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from .config import Settings

Notify = Callable[[str], None]


class VideoJobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ERROR = "ERROR"

    @property
    def terminal(self) -> bool:
        return self in (VideoJobStatus.COMPLETED, VideoJobStatus.FAILED, VideoJobStatus.ERROR)


class VideoError(RuntimeError):
    """Base error for the video subsystem."""


class VideoDisabledError(VideoError):
    """Raised when video is used while the feature flag is off."""


def video_enabled(settings: Settings) -> bool:
    return bool(settings.video_enabled)


def ensure_video_enabled(settings: Settings) -> None:
    if not settings.video_enabled:
        raise VideoDisabledError(
            "video is built but disabled — set CHRGD_VIDEO_ENABLED=true and "
            "provide HIGGSFIELD_API_KEY to turn it on"
        )


# --- provider interface -----------------------------------------------------


class VideoProvider(Protocol):
    """What the video pipeline needs from any provider."""

    def submit(self, image_path: str, motion_prompt: str, *, seed: int | None) -> str:
        """Kick off a clip; return the provider generation_id."""
        ...

    def poll(self, external_id: str) -> tuple[VideoJobStatus, str | None]:
        """Return (status, media_url-when-complete)."""
        ...

    def download(self, media_url: str, dest_path: str) -> str:
        """Download a finished clip to dest_path; return the path."""
        ...


# Provider status strings → our enum. Kept liberal so an aggregator's spelling
# ("in_progress", "succeeded", "error") maps cleanly without a code change.
_STATUS_MAP = {
    "queued": VideoJobStatus.QUEUED,
    "pending": VideoJobStatus.QUEUED,
    "starting": VideoJobStatus.QUEUED,
    "in_queue": VideoJobStatus.QUEUED,
    "processing": VideoJobStatus.PROCESSING,
    "in_progress": VideoJobStatus.PROCESSING,
    "running": VideoJobStatus.PROCESSING,
    "completed": VideoJobStatus.COMPLETED,
    "complete": VideoJobStatus.COMPLETED,
    "succeeded": VideoJobStatus.COMPLETED,
    "success": VideoJobStatus.COMPLETED,
    "failed": VideoJobStatus.FAILED,
    "failure": VideoJobStatus.FAILED,
    "canceled": VideoJobStatus.FAILED,
    "cancelled": VideoJobStatus.FAILED,
    "error": VideoJobStatus.ERROR,
}


def _map_status(raw: str | None) -> VideoJobStatus:
    return _STATUS_MAP.get((raw or "").strip().lower(), VideoJobStatus.PROCESSING)


def _data_uri(image_path: str) -> str:
    """base64 data URI for a local image, for providers that take inline images."""
    p = Path(image_path)
    if not p.exists():
        raise VideoError(f"source image not found: {image_path}")
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class HiggsfieldProvider:
    """Higgsfield Cloud image-to-video client.

    Reads key + base URL from settings; the base URL can point at an aggregator
    (e.g. Segmind) so the provider swaps without code changes. Only constructed
    when the feature flag is on.
    """

    #: Documented image2video endpoints (relative to the base URL). Isolated so
    #: a live-API reconciliation touches exactly these two lines.
    SUBMIT_PATH = "/v1/image2video"
    STATUS_PATH = "/v1/image2video/{id}"

    def __init__(self, settings: Settings, *, timeout: float = 60.0):
        ensure_video_enabled(settings)
        if not settings.higgsfield_api_key:
            raise VideoError("HIGGSFIELD_API_KEY is not set")
        self._key = settings.higgsfield_api_key
        self._base = settings.higgsfield_base_url.rstrip("/")
        self._timeout = timeout
        self._clip_seconds = settings.video_clip_seconds

    def _client(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise VideoError(
                "httpx not installed. Run: pip install -e '.[llm]'"
            ) from exc
        return httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=self._timeout,
        )

    # -- request/response shapes (reconcile here against the live API) --------

    def _submit_payload(self, image_path: str, motion_prompt: str, seed: int | None) -> dict:
        payload: dict = {
            "input_image": _data_uri(image_path),
            "prompt": motion_prompt,
            "duration": self._clip_seconds,
        }
        if seed is not None:
            payload["seed"] = seed
        return payload

    @staticmethod
    def _parse_submit(data: dict) -> str:
        # Accept the common id spellings so an aggregator response still parses.
        for key in ("id", "generation_id", "job_id", "request_id"):
            if data.get(key):
                return str(data[key])
        raise VideoError(f"submit response had no generation id: {data}")

    @staticmethod
    def _parse_status(data: dict) -> tuple[VideoJobStatus, str | None]:
        status = _map_status(data.get("status"))
        url = None
        # Result URL lives under a few documented shapes across providers.
        if isinstance(data.get("results"), list) and data["results"]:
            first = data["results"][0]
            url = first.get("url") if isinstance(first, dict) else first
        url = url or data.get("video_url") or data.get("output_url") or data.get("url")
        if isinstance(data.get("output"), dict):
            url = url or data["output"].get("url")
        return status, url

    # -- lifecycle -----------------------------------------------------------

    def submit(self, image_path: str, motion_prompt: str, *, seed: int | None) -> str:
        with self._client() as client:
            resp = client.post(self.SUBMIT_PATH, json=self._submit_payload(image_path, motion_prompt, seed))
            resp.raise_for_status()
            return self._parse_submit(resp.json())

    def poll(self, external_id: str) -> tuple[VideoJobStatus, str | None]:
        with self._client() as client:
            resp = client.get(self.STATUS_PATH.format(id=external_id))
            resp.raise_for_status()
            return self._parse_status(resp.json())

    def download(self, media_url: str, dest_path: str) -> str:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._client() as client:
            with client.stream("GET", media_url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
        return str(dest)


def get_video_provider(settings: Settings) -> VideoProvider:
    """Factory. Guards the feature flag and selects the provider."""
    ensure_video_enabled(settings)
    if settings.video_provider == "higgsfield":
        return HiggsfieldProvider(settings)
    raise VideoError(f"unknown video provider: {settings.video_provider}")


# --- motion prompt ----------------------------------------------------------


def video_pilot_show(idea) -> object | None:
    """The show this idea belongs to, if that show is a video pilot.

    Amp is the designated pilot (D5): flat vector animates far better than
    photoreal, his design is already locked, and a wrong frame costs a laugh
    rather than a likeness problem. Returns None for everything else, which is
    what keeps this a pass-through — nothing about the video path changes for
    a post that isn't on a pilot show.
    """
    from .shows import show_for_idea

    show = show_for_idea(idea)
    return show if (show is not None and show.video_pilot) else None


def motion_prompt_for_slide(
    index: int, total: int, design_system: dict | None = None,
    *, amp_state: str = "",
) -> str:
    """A short image-to-video motion instruction for one slide.

    The clip animates the *already-designed* frame — it must not restyle it or
    add text. We nudge subtle, native-feeling motion (a slow push, a parallax
    drift, sparks/energy) and lean on the design_system's `evolution` beat when
    present so the reel escalates the way the swipe does.

    `amp_state` marks this as a frame from the AMP pilot: a flat-vector cartoon
    animates differently from a photograph — the character should move, not the
    camera — and his state decides how.
    """
    if amp_state:
        return _amp_motion_prompt(index, total, amp_state)
    parts = [
        "Animate this exact frame into a short vertical clip. Keep the artwork, "
        "characters, colours and every word of on-image text exactly as-is — do "
        "not add, remove or restyle any text or element.",
        "Subtle, premium motion only: a slow camera push-in with gentle parallax, "
        "living light and small energy/spark accents.",
    ]
    evolution = (design_system or {}).get("evolution") if isinstance(design_system, dict) else None
    if evolution:
        parts.append(f"Let the energy build in step with the story: {evolution}.")
    if index == 0:
        parts.append("Open with a punchy stop-scroll beat.")
    elif index + 1 >= total:
        parts.append("Land on a confident, resolved hero beat.")
    return " ".join(parts)


#: How Amp moves in each state. A cartoon reads as alive through the CHARACTER
#: moving, not through a camera push — the generic prompt's slow dolly on a
#: flat vector frame just looks like a still image being zoomed.
_AMP_MOTION: dict[str, str] = {
    "drained": "Amp sags a little further, one slow blink, a faint flicker of dim light.",
    "flat": "Amp shifts his weight, unimpressed, a slow reluctant blink.",
    "wired": "Amp vibrates and twitches, erratic sparks flying off him, eyes darting.",
    "charging": "Amp straightens up as light builds through him, small sparks growing.",
    "beaming": "Amp glows brighter, arms opening, light blooming warmly around him.",
    "charged": "Amp crackles with energy, small lightning arcs snapping off him.",
    "smug": "Amp's grin widens fractionally, one eyebrow lifting, insufferably still.",
    "knackered_happy": "Amp slumps back contentedly, chest rising, glow softening.",
}


def _amp_motion_prompt(index: int, total: int, state: str) -> str:
    """The AMP pilot's motion brief for one slide."""
    beat = _AMP_MOTION.get(state, _AMP_MOTION["charging"])
    parts = [
        "Animate this exact 2D cartoon frame into a short vertical clip. Keep "
        "the character design, colours, line weight and every word of on-image "
        "text exactly as-is — do not restyle, redraw, add or remove anything.",
        "This is FLAT VECTOR ANIMATION, not a photograph: the CHARACTER moves, "
        "the camera stays still. No dolly, no parallax, no 3D depth, no "
        "photoreal lighting.",
        beat,
    ]
    if index == 0:
        parts.append("Open on a beat that stops the scroll.")
    elif index + 1 >= total:
        parts.append("Land the final pose and hold it.")
    return " ".join(parts)


# --- ffmpeg assembly (pure builders + guarded runner) -----------------------


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_normalize_command(src: str, dst: str, size: tuple[int, int], fps: int) -> list[str]:
    """ffmpeg argv to scale+pad one clip to exactly size at fps (no upscale skew).

    `scale=...:force_original_aspect_ratio=decrease` fits the clip inside the
    frame; `pad` centres it on black; `setsar=1` keeps square pixels so the
    concat demuxer accepts every clip as the same stream.
    """
    w, h = size
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    return [
        "ffmpeg", "-y", "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        dst,
    ]


def build_concat_command(list_file: str, out_path: str) -> list[str]:
    """ffmpeg argv to concat normalised clips (concat demuxer, stream copy)."""
    return [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", out_path,
    ]


def build_audio_mux_command(video_path: str, audio_path: str, out_path: str) -> list[str]:
    """ffmpeg argv to lay an audio bed under a finished video (trim to video)."""
    return [
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-shortest", out_path,
    ]


def _run_ffmpeg(argv: list[str], *, notify: Notify | None = None) -> None:
    if not ffmpeg_available():
        raise VideoError(
            "ffmpeg not found on PATH — install it to assemble video "
            "(e.g. `apt-get install ffmpeg`)"
        )
    if notify:
        notify(f"ffmpeg {argv[argv.index('-i') + 1] if '-i' in argv else ''}".strip())
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise VideoError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-800:]}")


def assemble_clips(
    clip_paths: list[str],
    out_path: str,
    settings: Settings,
    *,
    audio_mode: str | None = None,
    notify: Notify | None = None,
) -> str:
    """Normalise → concat (→ optional audio mux) the clips into `out_path`.

    In "draft" mode the reel ships silent; in "auto" mode a configured audio bed
    is muxed under it. Missing/failed audio never blocks the visual render — it
    falls back to the silent cut with a note.
    """
    if not clip_paths:
        raise VideoError("no clips to assemble")
    size = settings.video_target_size()
    fps = settings.video_fps
    mode = (audio_mode or settings.audio_mode or "draft").lower()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="chrgd-video-") as work:
        work_dir = Path(work)
        normalised: list[Path] = []
        for i, clip in enumerate(clip_paths):
            dst = work_dir / f"norm_{i:03d}.mp4"
            if notify:
                notify(f"normalising clip {i + 1}/{len(clip_paths)}")
            _run_ffmpeg(build_normalize_command(clip, str(dst), size, fps), notify=None)
            normalised.append(dst)

        list_file = work_dir / "concat.txt"
        # concat demuxer needs one `file '<path>'` line per clip, in order.
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in normalised), encoding="utf-8"
        )
        silent = work_dir / "silent.mp4"
        if notify:
            notify("stitching clips")
        _run_ffmpeg(build_concat_command(str(list_file), str(silent)), notify=None)

        bed = settings.video_audio_bed
        if mode == "auto" and bed and Path(bed).exists():
            if notify:
                notify("adding audio bed")
            _run_ffmpeg(build_audio_mux_command(str(silent), bed, str(out)), notify=None)
        else:
            if mode == "auto" and bed and notify:
                notify(f"audio bed not found ({bed}) — shipping silent draft")
            shutil.copyfile(silent, out)
    return str(out)


# --- orchestrator -----------------------------------------------------------


def _slide_image_paths(idea, settings: Settings) -> list[str]:
    """The rendered slide images that seed the reel, in slide order."""
    paths: list[str] = []
    if idea.asset_paths_json:
        try:
            paths = [p for p in json.loads(idea.asset_paths_json) if p]
        except (ValueError, TypeError):
            paths = []
    existing = [p for p in paths if Path(p).exists()]
    if not existing:
        raise VideoError(
            f"idea {idea.idea_id} has no rendered slides — run render first"
        )
    return existing


def _design_system(idea) -> dict | None:
    try:
        route = json.loads(idea.route_json) if idea.route_json else {}
    except (ValueError, TypeError):
        return None
    ds = route.get("design_system")
    return ds if isinstance(ds, dict) else None


def render_video(
    idea,
    settings: Settings,
    store,
    *,
    job_id: int | None = None,
    provider: VideoProvider | None = None,
    notify: Notify | None = None,
    seed: int | None = None,
) -> str:
    """End-to-end, resumable image-to-video render. Returns the reel's path.

    Per-slide state (provider external_id + downloaded clip path) is persisted on
    the job after each step, so a crash resumes by re-polling / re-stitching
    rather than re-submitting paid clips. `seed` is reused across every slide so
    the character/style stays consistent (Higgsfield "Soul"/seed behaviour).
    """
    ensure_video_enabled(settings)
    images = _slide_image_paths(idea, settings)
    provider = provider or get_video_provider(settings)
    design_system = _design_system(idea)

    out_dir = Path(settings.output_dir) / idea.idea_id
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Resume state from the job's result_json, if any.
    state: dict = {}
    if job_id is not None:
        job = store.get_job(job_id)
        if job and job.get("result_json"):
            try:
                state = json.loads(job["result_json"]) or {}
            except (ValueError, TypeError):
                state = {}
    clips_state: dict = state.get("clips") or {}

    def _persist(note: str, progress: int | None = None) -> None:
        if notify:
            notify(note)
        if job_id is not None:
            payload = {"note": note, "clips": clips_state}
            fields: dict = {"result_json": json.dumps(payload)}
            if progress is not None:
                fields["progress"] = progress
            store.update_job(job_id, **fields)

    total = len(images)
    # The AMP pilot animates as a cartoon, not as a photograph — his per-slide
    # state decides how he moves. Empty for every other post, which leaves the
    # generic motion brief exactly as it was.
    pilot = video_pilot_show(idea)
    amp_states: list[str] = []
    if pilot is not None:
        from .character import charges_for_route, state_for_charge, state_for_route
        from .images import _route_of

        route = _route_of(idea)
        free = state_for_route(route)
        if free:
            amp_states = [free] * total
        else:
            arc = charges_for_route(route, total) or []
            amp_states = [state_for_charge(c) for c in arc]

    for index, image_path in enumerate(images):
        key = str(index)
        entry = clips_state.get(key) or {}
        clip_path = clips_dir / f"clip_{index + 1}.mp4"

        # Already downloaded on a prior run → skip (no re-bill).
        if entry.get("path") and Path(entry["path"]).exists():
            continue

        external_id = entry.get("external_id")
        if not external_id:
            _persist(f"submitting clip {index + 1}/{total}", int(index * 90 / total))
            motion = motion_prompt_for_slide(
                index, total, design_system,
                amp_state=amp_states[index] if index < len(amp_states) else "",
            )
            external_id = provider.submit(image_path, motion, seed=seed)
            entry = {"external_id": external_id, "status": "QUEUED"}
            clips_state[key] = entry
            _persist(f"clip {index + 1}/{total} submitted", int(index * 90 / total))

        media_url = _poll_to_terminal(
            provider, external_id, settings,
            notify=lambda n: _persist(n, int(index * 90 / total)),
            label=f"clip {index + 1}/{total}",
        )
        _persist(f"downloading clip {index + 1}/{total}", int((index + 0.5) * 90 / total))
        provider.download(media_url, str(clip_path))
        entry["status"] = "COMPLETED"
        entry["path"] = str(clip_path)
        clips_state[key] = entry
        _persist(f"clip {index + 1}/{total} ready", int((index + 1) * 90 / total))

    ordered_clips = [clips_state[str(i)]["path"] for i in range(total)]
    _persist("assembling reel", 92)
    final_path = str(out_dir / "video.mp4")
    assemble_clips(
        ordered_clips, final_path, settings,
        audio_mode=settings.audio_mode, notify=lambda n: _persist(n, 96),
    )
    if job_id is not None:
        store.update_job(job_id, output_path=final_path, progress=100)
    _persist("reel complete", 100)
    return final_path


def _poll_to_terminal(
    provider: VideoProvider,
    external_id: str,
    settings: Settings,
    *,
    notify: Notify | None = None,
    label: str = "clip",
) -> str:
    """Poll one clip to a terminal state; return its media URL or raise."""
    deadline = time.monotonic() + settings.video_poll_timeout
    while True:
        status, url = provider.poll(external_id)
        if status is VideoJobStatus.COMPLETED:
            if not url:
                raise VideoError(f"{label} completed with no media url")
            return url
        if status in (VideoJobStatus.FAILED, VideoJobStatus.ERROR):
            raise VideoError(f"{label} {status.value.lower()} at the provider")
        if time.monotonic() >= deadline:
            raise VideoError(f"{label} timed out after {settings.video_poll_timeout:.0f}s")
        if notify:
            notify(f"{label} {status.value.lower()}…")
        time.sleep(settings.video_poll_interval)
