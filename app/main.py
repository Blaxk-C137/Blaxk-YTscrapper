"""FastAPI application for the CreatorScope dashboard."""

from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from youtube_creator_finder import NICHES, REGIONS

from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    STATIC_DIR,
)
from .database import Database
from .schemas import BulkUpdate, CreatorUpdate, DiscoveryRequest
from .scraper_service import JobManager, session_status


db = Database()
jobs = JobManager(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Jobs interrupted by a server restart should not remain "running" forever.
    with db.connection() as conn:
        conn.execute(
            """UPDATE jobs SET status='failed', error='Server restarted',
               message='The server restarted before this discovery run completed.',
               finished_at=datetime('now') WHERE status IN ('queued','running')"""
        )
    yield
    for task in list(jobs.tasks.values()):
        task.cancel()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="A private creator discovery and outreach workspace.",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


@app.get("/api/config")
def config():
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "regions": [
            {"code": code, "name": name} for code, name in REGIONS.values()
        ],
        "niches": [
            {"query": query, "label": label} for query, label in NICHES.values()
        ],
        "session": session_status(),
        "facets": db.facet_values(),
    }


@app.get("/api/stats")
def dashboard_stats():
    return db.stats()


@app.get("/api/creators")
def creators(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    q: str = "",
    region: str = "",
    country: str = "",
    niche: str = "",
    contact: str = "",
    stage: str = "",
    favorite: bool | None = None,
    archived: bool = False,
    sort: str = "found_at",
    order: str = "desc",
):
    return db.list_creators(
        page=page,
        page_size=page_size,
        q=q,
        region=region,
        country=country,
        niche=niche,
        contact=contact,
        stage=stage,
        favorite=favorite,
        archived=archived,
        sort=sort,
        order=order,
    )


@app.get("/api/creators/{channel_id}")
def creator(channel_id: str):
    item = db.get_creator(channel_id)
    if not item:
        raise HTTPException(status_code=404, detail="Creator not found")
    return item


@app.patch("/api/creators/{channel_id}")
def update_creator(channel_id: str, payload: CreatorUpdate):
    if not db.get_creator(channel_id):
        raise HTTPException(status_code=404, detail="Creator not found")
    return db.update_creator(channel_id, payload.model_dump(exclude_none=True))


@app.post("/api/creators/bulk")
def bulk_update(payload: BulkUpdate):
    updated = db.bulk_update(payload.channel_ids, payload.action, payload.stage)
    return {"updated": updated}


@app.delete("/api/creators")
def clear_workspace():
    if db.active_job_count():
        raise HTTPException(
            status_code=409,
            detail="Stop the active discovery run before clearing the workspace",
        )
    return {"cleared": db.clear_workspace()}


CSV_FIELDS = [
    "channel_id", "handle", "title", "account_year", "joined", "country",
    "subscriber_count", "video_count", "total_views", "business_email", "emails",
    "contact_source", "contact_status", "instagram", "twitter", "tiktok", "linkedin",
    "region_searched", "niches", "pipeline_stage", "favorite", "found_at", "notes",
    "description", "youtube_url",
]


def _selected_ids(ids: str) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in ids.split(",") if value.strip()))


@app.get("/api/export.csv")
def export_csv(ids: str = ""):
    rows = db.export_rows(_selected_ids(ids) or None)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in rows:
        item = dict(item)
        for key in ("business_email", "emails", "niches"):
            item[key] = "; ".join(item.get(key) or [])
        writer.writerow(item)
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=creatorscope-export.csv"},
    )


@app.get("/api/export.json")
def export_json(ids: str = ""):
    rows = db.export_rows(_selected_ids(ids) or None)
    content = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=creatorscope-export.json"},
    )


@app.get("/api/jobs")
def list_jobs(limit: Annotated[int, Query(ge=1, le=50)] = 10):
    return {"items": jobs.db.list_jobs(limit)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return job


@app.post("/api/jobs", status_code=202)
async def start_job(payload: DiscoveryRequest):
    # This endpoint must stay async: JobManager schedules the long-running
    # discovery coroutine on FastAPI's active event loop.
    try:
        return jobs.create(payload.regions, payload.niches, payload.limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return job


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
