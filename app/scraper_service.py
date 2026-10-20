"""Background discovery jobs backed by the original scraper engine.

Discovery is target-driven: the service keeps paginating and reviewing fresh
channels until it saves the requested number of matching contacts, exhausts the
search results, reaches a documented safety ceiling, or the user stops the run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from youtube_creator_finder import (
    API_CONCURRENCY,
    AUTH_COOKIES,
    BASE_URL,
    BLOCKED_RESOURCE_TYPES,
    NICHES,
    PAGE_CONCURRENCY,
    REGIONS,
    extract_emails,
    extract_socials,
    scrape_channel,
    search_channels,
)

from .config import MAX_ACTIVE_JOBS, PROFILE_DIR
from .database import Database, metric_number, utcnow

REGIONS_BY_CODE = {code: name for code, name in REGIONS.values()}
NICHES_BY_QUERY = {query: label for query, label in NICHES.values()}
QUERY_VARIANTS = ("base", "creator", "region")

DEFAULT_PREFERENCES: dict[str, Any] = {
    "min_subscribers": 0,
    "max_subscribers": None,
    "min_videos": 0,
    "max_videos": None,
    "min_channel_age": 0,
    "contact_requirement": "any",
}


def normalized_preferences(value: dict[str, Any] | None) -> dict[str, Any]:
    preferences = dict(DEFAULT_PREFERENCES)
    preferences.update(value or {})
    return preferences


def expanded_query(query: str, region_name: str, variant: str) -> str:
    if variant == "creator":
        return f"{query} creator"
    if variant == "region":
        return f"{query} {region_name}"
    return query


def matches_preferences(
    stats: dict[str, Any],
    business_emails: list[str],
    emails: list[str],
    socials: dict[str, list[str]],
    preferences: dict[str, Any],
    *,
    current_year: int | None = None,
) -> tuple[bool, str]:
    """Return whether a reviewed channel matches the user's creator brief."""
    preferences = normalized_preferences(preferences)
    subscriber_text = stats.get("subscribers")
    video_text = stats.get("videos")
    subscribers = metric_number(subscriber_text)
    videos = metric_number(video_text)

    min_subscribers = int(preferences.get("min_subscribers") or 0)
    max_subscribers = preferences.get("max_subscribers")
    if (min_subscribers or max_subscribers is not None) and not subscriber_text:
        return False, "subscriber_count_unknown"
    if subscribers < min_subscribers:
        return False, "below_min_subscribers"
    if max_subscribers is not None and subscribers > int(max_subscribers):
        return False, "above_max_subscribers"

    min_videos = int(preferences.get("min_videos") or 0)
    max_videos = preferences.get("max_videos")
    if (min_videos or max_videos is not None) and not video_text:
        return False, "video_count_unknown"
    if videos < min_videos:
        return False, "below_min_videos"
    if max_videos is not None and videos > int(max_videos):
        return False, "above_max_videos"

    min_age = int(preferences.get("min_channel_age") or 0)
    if min_age:
        account_year = stats.get("account_year")
        current_year = current_year or datetime.now(timezone.utc).year
        if not account_year or current_year - int(account_year) < min_age:
            return False, "channel_too_new_or_age_unknown"

    requirement = preferences.get("contact_requirement", "any")
    social_count = sum(len(values or []) for values in socials.values())
    if requirement == "email" and not emails:
        return False, "email_required"
    if requirement == "business_email" and not business_emails:
        return False, "revealed_business_email_required"
    if requirement == "social" and not social_count:
        return False, "social_required"
    return True, "matched"


def session_status(profile_dir: Path = PROFILE_DIR) -> dict[str, Any]:
    cookies = profile_dir / "Default" / "Cookies"
    configured = profile_dir.exists() and cookies.exists()
    updated_at = None
    if configured:
        updated_at = datetime.fromtimestamp(
            cookies.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    return {
        "configured": configured,
        "updated_at": updated_at,
        "profile_path": str(profile_dir),
        "message": (
            "Browser session found"
            if configured
            else "Connect a YouTube account locally before starting discovery"
        ),
    }


async def _block_assets(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class JobManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.tasks: dict[str, asyncio.Task] = {}

    def create(
        self,
        region_codes: list[str],
        niche_queries: list[str],
        target: int,
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.db.active_job_count() >= MAX_ACTIVE_JOBS:
            raise RuntimeError("A discovery run is already active")
        if not region_codes:
            raise ValueError("Select at least one region")
        invalid_regions = [code for code in region_codes if code not in REGIONS_BY_CODE]
        if invalid_regions:
            raise ValueError(f"Unsupported region: {invalid_regions[0]}")
        invalid = [query for query in niche_queries if query not in NICHES_BY_QUERY]
        if invalid:
            raise ValueError(f"Unsupported niche: {invalid[0]}")

        regions = [
            {"code": code, "name": REGIONS_BY_CODE[code]} for code in region_codes
        ]
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            # Keep singular fields for old job rows and API clients.
            "region_code": regions[0]["code"],
            "region_name": ", ".join(region["name"] for region in regions),
            "regions": regions,
            "niches": [
                {"query": query, "label": NICHES_BY_QUERY[query]}
                for query in niche_queries
            ],
            "preferences": normalized_preferences(preferences),
            "target": target,
        }
        self.db.create_job(job)
        task = asyncio.create_task(self._run(job), name=f"discovery-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job_id, None))
        return self.db.get_job(job_id)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        if job["status"] in {"queued", "running"}:
            self.db.update_job(
                job_id,
                cancel_requested=1,
                message="Stopping safely after the current channel…",
            )
        return self.db.get_job(job_id)

    def _cancelled(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        return bool(job and job["cancel_requested"])

    async def _launch_context(self, pw):
        kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await pw.chromium.launch_persistent_context(
                channel="chromium", **kwargs
            )
        except Exception:
            context = await pw.chromium.launch_persistent_context(**kwargs)
        await context.route("**/*", _block_assets)
        return context

    @staticmethod
    def _candidate_multiplier(preferences: dict[str, Any]) -> int:
        multiplier = 8
        if preferences.get("contact_requirement") in {"email", "business_email"}:
            multiplier += 4
        if any(
            preferences.get(key)
            for key in ("min_subscribers", "max_subscribers", "min_videos", "max_videos", "min_channel_age")
        ):
            multiplier += 4
        return multiplier

    async def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        preferences = normalized_preferences(job.get("preferences"))
        counts = {
            "discovered": 0,
            "processed": 0,
            "found": 0,
            "skipped": 0,
            "filtered": 0,
        }
        self.db.update_job(
            job_id,
            status="running",
            started_at=utcnow(),
            message="Opening the secure browser session…",
        )

        if not session_status()["configured"]:
            self.db.update_job(
                job_id,
                status="failed",
                error="YouTube session is not connected",
                message="Run `python scripts/connect_youtube.py` on this machine, then try again.",
                finished_at=utcnow(),
            )
            return

        search_exhausted = False
        review_limit_reached = False
        try:
            async with async_playwright() as pw:
                context = await self._launch_context(pw)
                try:
                    cookie_names = {
                        cookie["name"] for cookie in await context.cookies(BASE_URL)
                    }
                    if not (cookie_names & AUTH_COOKIES):
                        raise RuntimeError(
                            "The saved YouTube session has expired. Reconnect it locally."
                        )

                    api_sem = asyncio.Semaphore(min(API_CONCURRENCY, 8))
                    page_concurrency = min(PAGE_CONCURRENCY, 6)
                    page_sem = asyncio.Semaphore(page_concurrency)
                    combinations = max(
                        1,
                        len(job["regions"]) * len(job["niches"]) * len(QUERY_VARIANTS),
                    )
                    multiplier = self._candidate_multiplier(preferences)
                    max_reviews = max(300, min(2_000, job["target"] * 20))
                    seen_this_job: set[str] = set()
                    globally_processed = self.db.processed_ids()
                    wave = 0

                    while (
                        counts["found"] < job["target"]
                        and counts["processed"] < max_reviews
                        and not self._cancelled(job_id)
                        and not search_exhausted
                    ):
                        wave += 1
                        remaining = job["target"] - counts["found"]
                        review_budget = min(
                            max_reviews - counts["processed"],
                            max(80, remaining * multiplier),
                        )
                        per_search = max(
                            10,
                            min(300, (review_budget + combinations - 1) // combinations),
                        )
                        wave_ids: list[str] = []
                        matched_regions: dict[str, list[str]] = {}
                        matched_niches: dict[str, list[str]] = {}
                        every_search_exhausted = True

                        for region in job["regions"]:
                            if self._cancelled(job_id):
                                break
                            for niche in job["niches"]:
                                if self._cancelled(job_id):
                                    break
                                for variant in QUERY_VARIANTS:
                                    if self._cancelled(job_id):
                                        break
                                    query = expanded_query(
                                        niche["query"], region["name"], variant
                                    )
                                    self.db.update_job(
                                        job_id,
                                        message=(
                                            f"Expanding {niche['label']} search in "
                                            f"{region['name']} — need {remaining} more…"
                                        ),
                                    )
                                    excluded = (
                                        globally_processed | seen_this_job | set(wave_ids)
                                    )

                                    def report_pool(query_ids):
                                        counts["discovered"] = len(
                                            seen_this_job
                                            | set(wave_ids)
                                            | set(query_ids)
                                        )
                                        self.db.update_job(
                                            job_id,
                                            discovered=counts["discovered"],
                                            message=(
                                                f"Fresh pool: {counts['discovered']} · "
                                                f"expanding {niche['label']} in "
                                                f"{region['name']}…"
                                            ),
                                        )

                                    ids = await search_channels(
                                        context.request,
                                        query,
                                        region["code"],
                                        per_search,
                                        api_sem,
                                        exclude_ids=excluded,
                                        on_progress=report_pool,
                                    )
                                    if len(ids) >= per_search:
                                        every_search_exhausted = False
                                    for channel_id in ids:
                                        if channel_id not in wave_ids:
                                            wave_ids.append(channel_id)
                                        matched_regions.setdefault(
                                            channel_id, []
                                        ).append(region["name"])
                                        matched_niches.setdefault(
                                            channel_id, []
                                        ).append(niche["label"])

                                    # Publish the growing pool after every query
                                    # variation instead of waiting for the entire
                                    # market/niche search phase to finish.
                                    counts["discovered"] = len(
                                        seen_this_job | set(wave_ids)
                                    )
                                    self.db.update_job(
                                        job_id,
                                        discovered=counts["discovered"],
                                        message=(
                                            f"Fresh pool: {counts['discovered']} · "
                                            f"expanding {niche['label']} in "
                                            f"{region['name']}…"
                                        ),
                                    )

                        if not wave_ids:
                            search_exhausted = True
                            break

                        seen_this_job.update(wave_ids)
                        counts["discovered"] = len(seen_this_job)
                        self.db.update_job(job_id, **counts)

                        remaining_review_budget = max_reviews - counts["processed"]
                        candidates = wave_ids[:remaining_review_budget]
                        for offset in range(0, len(candidates), page_concurrency):
                            if (
                                counts["found"] >= job["target"]
                                or self._cancelled(job_id)
                                or counts["processed"] >= max_reviews
                            ):
                                break
                            batch = candidates[offset : offset + page_concurrency]
                            self.db.update_job(
                                job_id,
                                message=(
                                    f"Reviewing channels {counts['processed'] + 1}–"
                                    f"{counts['processed'] + len(batch)} · "
                                    f"{counts['found']}/{job['target']} contacts saved"
                                ),
                            )
                            results = await asyncio.gather(
                                *(scrape_channel(context, ref, page_sem) for ref in batch),
                                return_exceptions=True,
                            )
                            for channel_id, details in zip(batch, results):
                                if counts["found"] >= job["target"] or self._cancelled(job_id):
                                    break
                                counts["processed"] += 1
                                if isinstance(details, Exception) or not details:
                                    counts["skipped"] += 1
                                    self.db.update_job(job_id, **counts)
                                    continue

                                real_id = details["channel_id"]
                                if self.db.is_processed(real_id):
                                    self.db.add_creator_regions(
                                        real_id, matched_regions.get(channel_id, [])
                                    )
                                    counts["skipped"] += 1
                                    self.db.update_job(job_id, **counts)
                                    continue

                                business_emails = details["revealed_emails"]
                                public_emails = extract_emails(details["page_text"])
                                socials = extract_socials(details["page_text"])
                                all_emails = sorted(
                                    set(business_emails) | set(public_emails)
                                )
                                if business_emails:
                                    contact_source = "revealed"
                                elif public_emails:
                                    contact_source = "description"
                                else:
                                    contact_source = "none"

                                if not all_emails and not socials:
                                    counts["skipped"] += 1
                                    self.db.mark_processed(real_id, "no_contact_info")
                                    globally_processed.add(real_id)
                                    self.db.update_job(job_id, **counts)
                                    continue

                                matched, _reason = matches_preferences(
                                    details["stats"],
                                    business_emails,
                                    all_emails,
                                    socials,
                                    preferences,
                                )
                                if not matched:
                                    counts["filtered"] += 1
                                    self.db.update_job(job_id, **counts)
                                    continue

                                creator = {
                                    "channel_id": real_id,
                                    "handle": details["handle"],
                                    "title": details["title"],
                                    "description": details["description"],
                                    "stats": details["stats"],
                                    "business_email": business_emails,
                                    "emails": all_emails,
                                    "contact_source": contact_source,
                                    "contact_status": details["reveal_status"],
                                    "socials": socials,
                                    "regions_searched": matched_regions.get(
                                        channel_id,
                                        [region["name"] for region in job["regions"]],
                                    ),
                                    "niches_matched": matched_niches.get(
                                        channel_id,
                                        [niche["label"] for niche in job["niches"]],
                                    ),
                                    "found_at": utcnow(),
                                }
                                self.db.upsert_creator(creator)
                                globally_processed.add(real_id)
                                counts["found"] += 1
                                self.db.update_job(job_id, **counts)

                        if every_search_exhausted:
                            search_exhausted = True

                    review_limit_reached = (
                        counts["processed"] >= max_reviews
                        and counts["found"] < job["target"]
                    )
                finally:
                    await context.close()

            if self._cancelled(job_id):
                status = "cancelled"
                message = f"Stopped safely. Saved {counts['found']} creator(s)."
            elif counts["found"] >= job["target"]:
                status = "completed"
                message = (
                    f"Target reached — {counts['found']} matching contacts saved "
                    f"after reviewing {counts['processed']} channels."
                )
            elif search_exhausted:
                status = "completed"
                message = (
                    f"Search results exhausted after {counts['processed']} reviews. "
                    f"Saved {counts['found']} of {job['target']} requested contacts."
                )
            elif review_limit_reached:
                status = "completed"
                message = (
                    f"Safety review limit reached after {counts['processed']} channels. "
                    f"Saved {counts['found']} of {job['target']}. Widen the preferences "
                    "or start another run to continue."
                )
            else:
                status = "completed"
                message = f"Discovery finished with {counts['found']} saved contacts."

            self.db.update_job(
                job_id,
                status=status,
                message=message,
                finished_at=utcnow(),
                **counts,
            )
        except asyncio.CancelledError:
            self.db.update_job(
                job_id,
                status="cancelled",
                message="Discovery was stopped.",
                finished_at=utcnow(),
                **counts,
            )
            raise
        except Exception as exc:
            self.db.update_job(
                job_id,
                status="failed",
                error=str(exc),
                message="Discovery stopped before completion. Existing results were kept.",
                finished_at=utcnow(),
                **counts,
            )
