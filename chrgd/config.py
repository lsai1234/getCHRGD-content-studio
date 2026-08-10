"""Application configuration, loaded from environment / `.env`.

Centralises every tunable so the rest of the app never reads `os.environ`
directly. Milestone 1 only needs the core + cost-guard fields; the LLM,
image, and video settings are declared now so later milestones don't have
to touch this file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # --- Core ---
    db_path: Path = Field(default=Path("./data/chrgd.db"), alias="CHRGD_DB_PATH")
    output_dir: Path = Field(default=Path("./output"), alias="CHRGD_OUTPUT_DIR")
    id_prefix: str = Field(default="G", alias="CHRGD_ID_PREFIX")

    # --- LLM pipeline (milestone 2+) ---
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    # The CREATIVE model — writes the actual post copy that ships. Quality here
    # is the product, so it's the one place worth a top-tier model. gpt-4o is a
    # safe default; gpt-5 or gpt-4.1 are both cheaper AND stronger (see
    # .env.example) — switch here once you've confirmed the id on your account.
    openai_model: str = Field(default="gpt-4o", alias="CHRGD_OPENAI_MODEL")
    # The SCOUT model — trend/meta research + summarising into JSON. Mechanical
    # work that doesn't touch shipped copy, so a cheap fast model is the right
    # call (gpt-4o-mini ≈ 1/16th the price of gpt-4o).
    scout_model: str = Field(default="gpt-4o-mini", alias="CHRGD_SCOUT_MODEL")
    # The JUDGE model — the cold scroll test's vision verdict. A rubric-based
    # stop/scroll call on one image; a cheap vision model handles it fine.
    judge_model: str = Field(default="gpt-4o-mini", alias="CHRGD_JUDGE_MODEL")
    # Optional base-URL override (aggregators / Azure / compatible gateways).
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    # The official endpoint, used whenever no override is set. Passed to the
    # SDK explicitly: a BLANK OPENAI_BASE_URL= line in .env becomes a real,
    # empty env var under systemd's EnvironmentFile, and the SDK would treat
    # that as the URL itself ("Request URL is missing an 'http://'...").
    OPENAI_DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"

    def get_openai_base_url(self) -> str:
        return self.openai_base_url or self.OPENAI_DEFAULT_BASE_URL

    # --- Image generation (milestone 3+) ---
    # Defaults to OpenAI images so one key covers text + images.
    image_provider: str = Field(default="openai", alias="CHRGD_IMAGE_PROVIDER")
    image_model: str = Field(default="gpt-image-2", alias="CHRGD_IMAGE_MODEL")
    # Falls back to OPENAI_API_KEY when the provider is openai (see get_image_key).
    image_api_key: str | None = Field(default=None, alias="CHRGD_IMAGE_API_KEY")

    # --- Video generation (milestone 6 — infra present, feature OFF) ---
    # Master switch. Video plumbing (provider client, jobs table, CLI guards)
    # ships now, but nothing calls the paid video API until this is true.
    video_enabled: bool = Field(default=False, alias="CHRGD_VIDEO_ENABLED")
    video_provider: str = Field(default="higgsfield", alias="CHRGD_VIDEO_PROVIDER")
    higgsfield_api_key: str | None = Field(default=None, alias="HIGGSFIELD_API_KEY")
    # Base-URL override lets the provider swap cleanly (e.g. Segmind aggregator).
    higgsfield_base_url: str = Field(
        default="https://platform.higgsfield.ai",
        alias="CHRGD_HIGGSFIELD_BASE_URL",
    )
    # Default audio mode for video: "draft" (visuals only, add sound in-app) or
    # "auto" (render with a neutral bed). Trend-driven videos should stay draft.
    audio_mode: str = Field(default="draft", alias="CHRGD_AUDIO_MODE")
    # Assembly target (M6). The finished reel is normalised to this size/fps so
    # clips from any provider stitch into one consistent 9:16 file. 1080x1920 is
    # the TikTok/Reels native frame.
    video_size: str = Field(default="1080x1920", alias="CHRGD_VIDEO_SIZE")
    video_fps: int = Field(default=30, alias="CHRGD_VIDEO_FPS")
    # Target motion length per slide-clip, in seconds (passed to the provider and
    # used when a clip must be trimmed/padded during assembly).
    video_clip_seconds: float = Field(default=5.0, alias="CHRGD_VIDEO_CLIP_SECONDS")
    # Audio bed used only in "auto" mode: a local path (or URL the provider can
    # read) to a neutral music/ambience track muxed under the finished reel. In
    # "draft" mode the reel ships silent and sound is added in-app.
    video_audio_bed: str | None = Field(default=None, alias="CHRGD_VIDEO_AUDIO_BED")
    # Async poll cadence + ceiling for a single clip (submit → poll → download).
    video_poll_interval: float = Field(default=5.0, alias="CHRGD_VIDEO_POLL_INTERVAL")
    video_poll_timeout: float = Field(default=600.0, alias="CHRGD_VIDEO_POLL_TIMEOUT")

    def video_target_size(self) -> tuple[int, int]:
        """Parse `video_size` ("WxH") into an (width, height) int pair."""
        try:
            w, h = self.video_size.lower().split("x", 1)
            return int(w), int(h)
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"CHRGD_VIDEO_SIZE must look like '1080x1920', got {self.video_size!r}"
            ) from exc

    # --- Slide-1 concept gate (pre-image quality control) ---
    # Slide 1 is generated ONCE at high quality, so we validate its concept hard
    # BEFORE spending: an independent judge scores the slide-1 plan (headline +
    # visual brief); below the bar it's sharpened and re-judged, up to a few
    # rounds. Cheap text calls guarding one expensive image.
    concept_gate_enabled: bool = Field(default=True, alias="CHRGD_CONCEPT_GATE")
    # The bar the slide-1 concept must clear (0-10). Deliberately high — this is
    # the compromise for a single slide-1 image. A literal 10 every time isn't
    # something an LLM judge reliably emits, so 9 is "genuinely thumb-stopping";
    # raise to 10 to be stricter (it'll simply use all the rounds more often).
    concept_gate_min_score: int = Field(default=9, alias="CHRGD_CONCEPT_GATE_MIN")
    # Max judge rounds (each failing round sharpens slide 1 and re-judges).
    # Two, not three: the tournament above has already explored the space, so a
    # third pass is the same sharpener trying the same trick a third time — and
    # the loop now stops early anyway when a rewrite doesn't beat what it
    # replaced. Raise it if you want the extra rounds back.
    concept_gate_max_rounds: int = Field(default=2, alias="CHRGD_CONCEPT_GATE_ROUNDS")
    # The opener TOURNAMENT (stages A+B). Sharpening one hook only ever makes
    # the same idea louder — it never leaves the neighbourhood of whatever the
    # build happened to write first. So before scoring, the engine invents
    # several genuinely different openers for the same post (each forced onto a
    # different curiosity mechanic) and judges the whole field head-to-head, with
    # the built opener as the incumbent. This is where "a hundred other gym posts
    # could open with this" gets caught: in isolation a derivative opener scores
    # fine; next to three sharper rivals it loses.
    concept_tournament_enabled: bool = Field(
        default=True, alias="CHRGD_CONCEPT_TOURNAMENT"
    )
    # How many rivals to invent (they compete against the built opener, so the
    # field is this + 1). Three keeps a real field — four distinct openers to
    # choose between — for one shorter completion; the rivals arrive in a single
    # call, so this is output tokens rather than extra calls.
    concept_candidates: int = Field(default=3, alias="CHRGD_CONCEPT_CANDIDATES")
    # The GLANCE test (stage D). The judge is shown only what a stranger
    # perceives in the half-second before the thumb decides — the headline and
    # the shape of the image, with none of the reasoning — and has to say what it
    # actually took away. Catches concepts that are clever on paper but need a
    # second read, which in a feed means they don't exist. One bounded fix.
    concept_glance_test: bool = Field(default=True, alias="CHRGD_CONCEPT_GLANCE")

    # --- Cost guard ---
    max_spend_per_run: float = Field(default=5.0, alias="CHRGD_MAX_SPEND_PER_RUN")

    # --- Retention (auto-delete old content) ---
    # Content this many days past its last activity is deleted automatically:
    # the rendered images (which fill the disk) and the idea row (which slows
    # every screen that reads the backlog). 30 is the intended setting.
    #
    # Ships as 0 (off) DELIBERATELY. This deletes real work permanently, and a
    # studio that has been running for months would lose most of its library to
    # the first sweep after an upgrade — an irreversible surprise nobody asked
    # for at the moment they pulled a new version. Arm it once you have seen
    # what it would take: `chrgd prune --dry-run`, then set this to 30.
    retention_days: int = Field(default=0, alias="CHRGD_RETENTION_DAYS")
    # A post with logged results keeps its (tiny) row so the learning loop still
    # has a corpus to steer builds with; its images go either way. Set false to
    # delete rated posts outright as well.
    retention_keep_rated: bool = Field(
        default=True, alias="CHRGD_RETENTION_KEEP_RATED"
    )

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="CHRGD_LOG_LEVEL")

    # --- Web dashboard (Phase A) ---
    web_username: str = Field(default="admin", alias="CHRGD_WEB_USERNAME")
    # Set EITHER a pbkdf2 hash (preferred) or a plaintext password.
    web_password: str | None = Field(default=None, alias="CHRGD_WEB_PASSWORD")
    web_password_hash: str | None = Field(
        default=None, alias="CHRGD_WEB_PASSWORD_HASH"
    )
    # Signs session cookies. Set a long random value in prod so logins survive
    # restarts; a random one is generated per-process if unset.
    secret_key: str | None = Field(default=None, alias="CHRGD_SECRET_KEY")

    # --- Optional posting backends (milestone 7) ---
    unified_api_key: str | None = Field(default=None, alias="CHRGD_UNIFIED_API_KEY")

    def get_image_key(self) -> str | None:
        """Key for the image provider, falling back to the OpenAI key."""
        if self.image_api_key:
            return self.image_api_key
        if self.image_provider == "openai":
            return self.openai_api_key
        return None

    def ensure_dirs(self) -> None:
        """Create the directories the app writes to, if missing."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
