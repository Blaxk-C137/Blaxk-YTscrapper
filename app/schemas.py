"""Pydantic request models for the public API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DiscoveryPreferences(BaseModel):
    min_subscribers: int = Field(default=0, ge=0, le=10_000_000_000)
    max_subscribers: int | None = Field(default=None, ge=1, le=10_000_000_000)
    min_videos: int = Field(default=0, ge=0, le=10_000_000)
    max_videos: int | None = Field(default=None, ge=1, le=10_000_000)
    min_channel_age: int = Field(default=0, ge=0, le=30)
    contact_requirement: Literal["any", "email", "business_email", "social"] = "any"

    @model_validator(mode="after")
    def valid_ranges(self):
        if self.max_subscribers is not None and self.max_subscribers < self.min_subscribers:
            raise ValueError("Maximum subscribers must be greater than the minimum")
        if self.max_videos is not None and self.max_videos < self.min_videos:
            raise ValueError("Maximum videos must be greater than the minimum")
        return self


class DiscoveryRequest(BaseModel):
    regions: list[str] = Field(min_length=1, max_length=10)
    niches: list[str] = Field(min_length=1, max_length=7)
    limit: int = Field(default=25, ge=1, le=200)
    preferences: DiscoveryPreferences = Field(default_factory=DiscoveryPreferences)

    @field_validator("regions")
    @classmethod
    def uppercase_regions(cls, values: list[str]) -> list[str]:
        cleaned = [value.upper().strip() for value in values if value.strip()]
        return list(dict.fromkeys(cleaned))

    @field_validator("niches")
    @classmethod
    def unique_niches(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lower() for value in values if value.strip()]
        return list(dict.fromkeys(cleaned))


class CreatorUpdate(BaseModel):
    pipeline_stage: Literal[
        "new", "shortlisted", "contacted", "replied", "partnered", "not_a_fit"
    ] | None = None
    favorite: bool | None = None
    notes: str | None = Field(default=None, max_length=5000)


class BulkUpdate(BaseModel):
    channel_ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["favorite", "unfavorite", "archive", "restore", "set_stage"]
    stage: Literal[
        "new", "shortlisted", "contacted", "replied", "partnered", "not_a_fit"
    ] | None = None

    @field_validator("channel_ids")
    @classmethod
    def unique_channel_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
