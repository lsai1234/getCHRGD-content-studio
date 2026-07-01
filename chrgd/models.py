"""Pydantic models for the backlog and finished posts.

Two logical record types share one `ideas` table:
  * **Seed** fields are set when an idea is captured.
  * **Build** fields are set when a post is generated (milestone 2+).

The `Idea` model represents a full row. `Slide` / `Post` describe the
generated build payload and are used by the pipeline runner later; they're
defined here so the schema lives in one place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Status(str, Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    void = "void"


class PostType(str, Enum):
    carousel = "carousel"
    video = "video"


class DecaySpeed(str, Enum):
    """How fast a topical idea goes stale. Evergreen items have no decay."""

    days = "days"
    weeks = "weeks"
    evergreen = "evergreen"


# --- Build payload (milestone 2+) -------------------------------------------


class Slide(BaseModel):
    """One carousel slide: approved copy plus its visual brief."""

    headline: str
    supporting: str = ""
    image_prompt: str = ""
    visual_intent: str = ""


class Post(BaseModel):
    """The finished post produced by the 6-stage pipeline."""

    post_type: PostType = PostType.carousel
    hook: str
    slides: list[Slide] = Field(default_factory=list)
    caption: str = ""
    comment_trigger: str = ""
    hashtags: list[str] = Field(default_factory=list)
    # Routing/QA bookkeeping: mechanic, visual engine, QA scores, etc.
    route: dict = Field(default_factory=dict)


# --- Backlog row ------------------------------------------------------------


class Idea(BaseModel):
    """A full backlog row — seed fields plus optional build fields."""

    # Seed fields
    idea_id: str
    status: Status = Status.queued
    priority: int = 3
    content_category: str = ""
    target_viewer: str = ""
    pain_point: str = ""
    core_tension: str = ""
    concept_note: str = ""
    learning_tag: str = ""
    decay_speed: DecaySpeed | None = None
    created_at: datetime = Field(default_factory=_now)

    # Build fields (populated in later milestones)
    post_type: PostType | None = None
    hook: str | None = None
    slides_json: str | None = None
    caption: str | None = None
    comment_trigger: str | None = None
    hashtags: str | None = None
    route_json: str | None = None
    asset_paths_json: str | None = None
    scheduled_for: datetime | None = None
    processed_at: datetime | None = None
    exported_at: datetime | None = None
    platform_urls_json: str | None = None
