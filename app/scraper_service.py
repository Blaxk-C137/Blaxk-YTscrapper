"""Background discovery jobs backed by the original scraper engine.

The service intentionally runs one conservative job at a time, stops at
CAPTCHAs, and never attempts to defeat YouTube access controls.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
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
from .database import Database, utcnow

REGIONS_BY_CODE = {code: name for code, name in REGIONS.values()}
NICHES_BY_QUERY = {query: label for query, label in NICHES.values()}


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

    def create(self, region_code: str, niche_queries: list[str], target: int) -> dict[str, Any]:
        if self.db.active_job_count() >= MAX_ACTIVE_JOBS:
            raise RuntimeError("A discovery run is already active")
        if region_code not in REGIONS_BY_CODE:
            raise ValueError("Unsupported region")
        invalid = [query for query in niche_queries if query not in NICHES_BY_QUERY]
        if invalid:
            raise ValueError(f"Unsupported niche: {invalid[0]}")

        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "region_code": region_code,
            "region_name": REGIONS_BY_CODE[region_code],
            "niches": [
                {"query": query, "label": NICHES_BY_QUERY[query]}
                for query in niche_queries
            ],
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

    async def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        counts = {"discovered": 0, "processed": 0, "found": 0, "skipped": 0}
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

        try:
            async with async_playwright() as pw:
                context = await self._launch_context(pw)
                try:
                    cookie_names = {cookie["name"] for cookie in await context.cookies(BASE_URL)}
                    if not (cookie_names & AUTH_COOKIES):
                        raise RuntimeError(
                            "The saved YouTube session has expired. Reconnect it locally."
                        )

                    api_sem = asyncio.Semaphore(min(API_CONCURRENCY, 6))
                    page_sem = asyncio.Semaphore(min(PAGE_CONCURRENCY, 4))
                    all_ids: list[str] = []
                    for niche in job["niches"]:
                        if self._cancelled(job_id):
                            break
                        self.db.update_job(
                            job_id,
                            message=f"Searching {niche['label']} channels in {job['region_name']}…",
                        )
                        ids = await search_channels(
                            context.request,
                            niche["query"],
                            job["region_code"],
                            max(job["target"], 20),
                            api_sem,
                        )
                        for channel_id in ids:
                            if channel_id not in all_ids:
                                all_ids.append(channel_id)
                        counts["discovered"] = len(all_ids)
                        self.db.update_job(job_id, discovered=counts["discovered"])

                    candidates = [
                        channel_id
                        for channel_id in all_ids
                        if not self.db.is_processed(channel_id)
                    ]
                    batch_size = min(4, PAGE_CONCURRENCY)
                    for offset in range(0, len(candidates), batch_size):
                        if counts["found"] >= job["target"] or self._cancelled(job_id):
                            break
                        batch = candidates[offset : offset + batch_size]
                        self.db.update_job(
                            job_id,
                            message=(
                                f"Reviewing {counts['processed'] + 1}–"
                                f"{counts['processed'] + len(batch)} of {len(candidates)} new channels…"
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
                                self.db.mark_processed(channel_id, "load_failed")
                                self.db.update_job(job_id, **counts)
                                continue

                            real_id = details["channel_id"]
                            if self.db.is_processed(real_id):
                                counts["skipped"] += 1
                                self.db.update_job(job_id, **counts)
                                continue

                            business_emails = details["revealed_emails"]
                            public_emails = extract_emails(details["page_text"])
                            socials = extract_socials(details["page_text"])
                            all_emails = sorted(set(business_emails) | set(public_emails))
                            if business_emails:
                                contact_source = "revealed"
                            elif public_emails:
                                contact_source = "description"
                            else:
                                contact_source = "none"

                            if not all_emails and not socials:
                                counts["skipped"] += 1
                                self.db.mark_processed(real_id, "no_contact_info")
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
                                "region_searched": job["region_name"],
                                "niches_matched": [n["label"] for n in job["niches"]],
                                "found_at": utcnow(),
                            }
                            self.db.upsert_creator(creator)
                            counts["found"] += 1
                            self.db.update_job(job_id, **counts)
                            await asyncio.sleep(random.uniform(0.25, 0.7))
                finally:
                    await context.close()

            if self._cancelled(job_id):
                self.db.update_job(
                    job_id,
                    status="cancelled",
                    message=f"Stopped safely. Saved {counts['found']} creator(s).",
                    finished_at=utcnow(),
                    **counts,
                )
            else:
                self.db.update_job(
                    job_id,
                    status="completed",
                    message=f"Discovery complete — {counts['found']} creator(s) saved.",
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
