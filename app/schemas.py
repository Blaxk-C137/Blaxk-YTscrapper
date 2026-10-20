"""Pydantic request models for the public API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DiscoveryRequest(BaseModel):
    region: str = Field(min_length=2, max_length=2)
    niches: list[str] = Field(min_length=1, max_length=7)
    limit: int = Field(default=25, ge=1, le=200)

    @field_validator("region")
    @classmethod
    def uppercase_region(cls, value: str) -> str:
        return value.upper().strip()

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
