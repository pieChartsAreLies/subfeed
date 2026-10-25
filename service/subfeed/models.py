"""Request bodies."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelPatch(BaseModel):
    category_id: int | None = None
    hidden: bool | None = None


class CategoryIn(BaseModel):
    name: str


class ProgressIn(BaseModel):
    position_s: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    sponsor_s: float = Field(default=0, ge=0)
    ended: bool = False  # true when the player reached STATE_ENDED
    content_position_s: float | None = Field(default=None, ge=0)  # position with sponsor segments removed


class WatchedIn(BaseModel):
    watched: bool
