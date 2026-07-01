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
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-sonnet-4-6", alias="CHRGD_ANTHROPIC_MODEL"
    )

    # --- Image generation (milestone 3+) ---
    image_provider: str | None = Field(default=None, alias="CHRGD_IMAGE_PROVIDER")
    image_model: str | None = Field(default=None, alias="CHRGD_IMAGE_MODEL")
    image_api_key: str | None = Field(default=None, alias="CHRGD_IMAGE_API_KEY")

    # --- Video generation (milestone 6+) ---
    higgsfield_api_key: str | None = Field(default=None, alias="HIGGSFIELD_API_KEY")
    higgsfield_base_url: str | None = Field(
        default=None, alias="CHRGD_HIGGSFIELD_BASE_URL"
    )

    # --- Cost guard ---
    max_spend_per_run: float = Field(default=5.0, alias="CHRGD_MAX_SPEND_PER_RUN")

    # --- Optional posting backends (milestone 7) ---
    unified_api_key: str | None = Field(default=None, alias="CHRGD_UNIFIED_API_KEY")

    def ensure_dirs(self) -> None:
        """Create the directories the app writes to, if missing."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
