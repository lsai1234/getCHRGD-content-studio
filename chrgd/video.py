"""Video generation infrastructure (Higgsfield) — SCAFFOLDED, FEATURE OFF.

This module puts the *plumbing* for image-to-video in place so enabling it
later (milestone 6) is a small step, without turning on any paid video calls
now. Concretely it provides:

  * a feature guard tied to `settings.video_enabled` (default False),
  * a provider-agnostic `VideoProvider` interface,
  * a `HiggsfieldProvider` client skeleton for the submit → poll → download
    async lifecycle (base-URL override supports the Segmind aggregator),
  * the async job-status enum matching the brief.

What is deliberately NOT built yet: the end-to-end orchestration (ffmpeg
stitching, text burn-in, audio bed). `render_video` raises until M6 so nobody
mistakes the scaffold for a working feature. The provider HTTP calls are
written to the documented shape but are unverified against the live API and
never execute while the feature is off.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from .config import Settings


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
            "video is scaffolded but disabled — set CHRGD_VIDEO_ENABLED=true and "
            "provide HIGGSFIELD_API_KEY once the M6 pipeline is built"
        )


# --- provider interface -----------------------------------------------------


class VideoProvider(Protocol):
    """What the (future) video pipeline needs from any provider."""

    def submit(self, image_path: str, motion_prompt: str, *, seed: int | None) -> str:
        """Kick off a clip; return the provider generation_id."""
        ...

    def poll(self, external_id: str) -> tuple[VideoJobStatus, str | None]:
        """Return (status, media_url-when-complete)."""
        ...

    def download(self, media_url: str, dest_path: str) -> str:
        """Download a finished clip to dest_path; return the path."""
        ...


class HiggsfieldProvider:
    """Higgsfield Cloud image-to-video client (skeleton).

    Reads key + base URL from settings; the base URL can point at an
    aggregator (e.g. Segmind) so the provider swaps without code changes.
    Only constructed/called when the feature is enabled.
    """

    def __init__(self, settings: Settings, *, timeout: float = 60.0):
        ensure_video_enabled(settings)
        if not settings.higgsfield_api_key:
            raise VideoError("HIGGSFIELD_API_KEY is not set")
        self._key = settings.higgsfield_api_key
        self._base = settings.higgsfield_base_url.rstrip("/")
        self._timeout = timeout

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

    # NOTE: request/response shapes below follow the documented lifecycle and
    # will be reconciled against the live API when video is switched on (M6).
    def submit(self, image_path: str, motion_prompt: str, *, seed: int | None) -> str:
        raise NotImplementedError("Higgsfield submit lands in milestone 6")

    def poll(self, external_id: str) -> tuple[VideoJobStatus, str | None]:
        raise NotImplementedError("Higgsfield poll lands in milestone 6")

    def download(self, media_url: str, dest_path: str) -> str:
        raise NotImplementedError("Higgsfield download lands in milestone 6")


def get_video_provider(settings: Settings) -> VideoProvider:
    """Factory. Guards the feature flag and selects the provider."""
    ensure_video_enabled(settings)
    if settings.video_provider == "higgsfield":
        return HiggsfieldProvider(settings)
    raise VideoError(f"unknown video provider: {settings.video_provider}")


def render_video(idea, settings: Settings, store) -> None:
    """End-to-end video render. Not built yet (M6).

    Guarded so the intent is unambiguous: off → VideoDisabledError; on → a
    clear NotImplementedError pointing at the milestone that builds it.
    """
    ensure_video_enabled(settings)
    raise NotImplementedError(
        "video assembly (submit → poll → ffmpeg stitch + audio) is milestone 6"
    )
