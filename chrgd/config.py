"""Application configuration, loaded from environment / `.env`.

Centralises every tunable so the rest of the app never reads `os.environ`
directly. Milestone 1 only needs the core + cost-guard fields; the LLM,
image, and video settings are declared now so later milestones don't have
to touch this file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    openai_model: str = Field(default="gpt-4o", alias="CHRGD_OPENAI_MODEL")
    # Optional base-URL override (aggregators / Azure / compatible gateways).
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

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

    # --- Cost guard ---
    max_spend_per_run: float = Field(default=5.0, alias="CHRGD_MAX_SPEND_PER_RUN")

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
