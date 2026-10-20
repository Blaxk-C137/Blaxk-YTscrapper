"""SQLite persistence, cache migration, querying, and dashboard analytics."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from youtube_creator_finder import clean_country

from .config import DB_PATH, SEED_CACHE

JSON_FIELDS = {"emails", "business_email", "niches", "socials", "raw"}
STAGES = ("new", "shortlisted", "contacted", "replied", "partnered", "not_a_fit")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def metric_number(value: Any) -> int:
    """Turn YouTube metrics such as ``21.3M`` or ``1,852`` into integers."""
    if value in (None, "", "Unknown"):
        return 0
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([KMB])?", text, re.I)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(
        (match.group(2) or "").upper(), 1
    )
    return int(number * multiplier)


def _json(value: Any, fallback: Any) -> str:
    return json.dumps(value if value is not None else fallback, ensure_ascii=False)


class Database:
    def __init__(self, path: Path = DB_PATH, seed_cache: Path | None = SEED_CACHE) -> None:
        self.path = Path(path)
        self.seed_cache = Path(seed_cache) if seed_cache is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS creators (
                    channel_id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT 'Unknown',
                    account_year INTEGER,
                    joined TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    subscriber_count TEXT NOT NULL DEFAULT '',
                    subscribers_num INTEGER NOT NULL DEFAULT 0,
                    video_count TEXT NOT NULL DEFAULT '',
                    videos_num INTEGER NOT NULL DEFAULT 0,
                    total_views TEXT NOT NULL DEFAULT '',
                    views_num INTEGER NOT NULL DEFAULT 0,
                    business_email TEXT NOT NULL DEFAULT '[]',
                    emails TEXT NOT NULL DEFAULT '[]',
                    contact_source TEXT NOT NULL DEFAULT 'none',
                    contact_status TEXT NOT NULL DEFAULT '',
                    instagram TEXT NOT NULL DEFAULT '',
                    twitter TEXT NOT NULL DEFAULT '',
                    tiktok TEXT NOT NULL DEFAULT '',
                    linkedin TEXT NOT NULL DEFAULT '',
                    socials TEXT NOT NULL DEFAULT '{}',
                    region_searched TEXT NOT NULL DEFAULT '',
                    niches TEXT NOT NULL DEFAULT '[]',
                    found_at TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    pipeline_stage TEXT NOT NULL DEFAULT 'new',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0,
                    raw TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_channels (
                    channel_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL DEFAULT 'imported',
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    region_code TEXT NOT NULL,
                    region_name TEXT NOT NULL,
                    niches TEXT NOT NULL,
                    target INTEGER NOT NULL,
                    discovered INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    found INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_creators_found_at ON creators(found_at DESC);
                CREATE INDEX IF NOT EXISTS idx_creators_region ON creators(region_searched);
                CREATE INDEX IF NOT EXISTS idx_creators_country ON creators(country);
                CREATE INDEX IF NOT EXISTS idx_creators_stage ON creators(pipeline_stage);
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
                """
            )
        self.import_seed_once()

    def import_seed_once(self) -> None:
        with self.connection() as conn:
            migrated = conn.execute(
                "SELECT value FROM metadata WHERE key='seed_imported'"
            ).fetchone()
        if migrated:
            return

        imported = 0
        if self.seed_cache and self.seed_cache.exists():
            try:
                payload = json.loads(self.seed_cache.read_text(encoding="utf-8"))
                creators_by_id = {
                    item.get("channel_id"): item
                    for item in payload.get("creators", [])
                    if item.get("channel_id")
                }
                for channel_id in payload.get("processed_ids", []):
                    source = creators_by_id.get(channel_id, {})
                    self.mark_processed(channel_id, source.get("reason", "imported"))
                for creator in payload.get("creators", []):
                    if not creator.get("skipped") and creator.get("channel_id"):
                        self.upsert_creator(creator)
                        imported += 1
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                print(f"Seed migration skipped: {exc}")

        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('seed_imported', ?)",
                (json.dumps({"at": utcnow(), "creators": imported}),),
            )

    def mark_processed(self, channel_id: str, reason: str = "processed") -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO processed_channels(channel_id, reason, processed_at)
                   VALUES(?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     reason=excluded.reason, processed_at=excluded.processed_at""",
                (channel_id, reason, utcnow()),
            )

    def is_processed(self, channel_id: str) -> bool:
        with self.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_channels WHERE channel_id=?", (channel_id,)
            ).fetchone() is not None

    def upsert_creator(self, creator: dict[str, Any]) -> None:
        stats = creator.get("stats") or {}
        socials = creator.get("socials") or {}
        emails = creator.get("emails") or []
        business_email = creator.get("business_email") or []
        niches = creator.get("niches_matched") or creator.get("niches") or []
        now = utcnow()
        found_at = creator.get("found_at") or now
        values = {
            "channel_id": creator["channel_id"],
            "handle": creator.get("handle") or "",
            "title": creator.get("title") or "Unknown",
            "account_year": stats.get("account_year", creator.get("account_year")),
            "joined": stats.get("joined", creator.get("joined", "")) or "",
            "country": clean_country(
                stats.get("country", creator.get("country", "")) or ""
            ),
            "subscriber_count": stats.get(
                "subscribers", creator.get("subscriber_count", "")
            ) or "",
            "video_count": stats.get("videos", creator.get("video_count", "")) or "",
            "total_views": stats.get("views", creator.get("total_views", "")) or "",
            "business_email": _json(business_email, []),
            "emails": _json(emails, []),
            "contact_source": creator.get("contact_source") or "none",
            "contact_status": creator.get("contact_status") or "",
            "instagram": "; ".join(socials.get("instagram", [])),
            "twitter": "; ".join(socials.get("twitter", [])),
            "tiktok": "; ".join(socials.get("tiktok", [])),
            "linkedin": "; ".join(socials.get("linkedin", [])),
            "socials": _json(socials, {}),
            "region_searched": creator.get("region_searched") or "",
            "niches": _json(niches, []),
            "found_at": found_at,
            "description": creator.get("description") or "",
            "pipeline_stage": creator.get("pipeline_stage") or "new",
            "favorite": int(bool(creator.get("favorite"))),
            "notes": creator.get("notes") or "",
            "raw": _json(creator, {}),
            "created_at": creator.get("created_at") or now,
            "updated_at": now,
        }
        values["subscribers_num"] = metric_number(values["subscriber_count"])
        values["videos_num"] = metric_number(values["video_count"])
        values["views_num"] = metric_number(values["total_views"])

        columns = list(values)
        placeholders = ", ".join(f":{column}" for column in columns)
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {"channel_id", "created_at", "pipeline_stage", "favorite", "notes"}
        )
        with self.connection() as conn:
            conn.execute(
                f"""INSERT INTO creators({', '.join(columns)}) VALUES({placeholders})
                    ON CONFLICT(channel_id) DO UPDATE SET {updates}""",
                values,
            )
        self.mark_processed(creator["channel_id"], "creator_saved")

    @staticmethod
    def _decode_creator(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for field in JSON_FIELDS:
            if field in item:
                try:
                    item[field] = json.loads(item[field])
                except (TypeError, json.JSONDecodeError):
                    item[field] = [] if field in {"emails", "business_email", "niches"} else {}
        item["favorite"] = bool(item.get("favorite"))
        item["archived"] = bool(item.get("archived"))
        item["youtube_url"] = (
            f"https://www.youtube.com/{item['handle']}"
            if item.get("handle")
            else f"https://www.youtube.com/channel/{item['channel_id']}"
        )
        return item

    def get_creator(self, channel_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM creators WHERE channel_id=?", (channel_id,)
            ).fetchone()
        return self._decode_creator(row)

    def list_creators(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
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
    ) -> dict[str, Any]:
        clauses = ["archived = ?"]
        params: list[Any] = [int(archived)]
        if q:
            like = f"%{q.strip()}%"
            clauses.append(
                "(title LIKE ? OR handle LIKE ? OR description LIKE ? OR emails LIKE ? "
                "OR instagram LIKE ? OR twitter LIKE ? OR tiktok LIKE ?)"
            )
            params.extend([like] * 7)
        if region:
            clauses.append("region_searched = ?")
            params.append(region)
        if country:
            clauses.append("country = ?")
            params.append(country)
        if niche:
            clauses.append("niches LIKE ?")
            params.append(f'%"{niche}"%')
        if contact == "email":
            clauses.append("emails != '[]'")
        elif contact == "social":
            clauses.append("(instagram != '' OR twitter != '' OR tiktok != '' OR linkedin != '')")
        elif contact == "none":
            clauses.append("emails = '[]' AND instagram = '' AND twitter = '' AND tiktok = '' AND linkedin = ''")
        if stage:
            clauses.append("pipeline_stage = ?")
            params.append(stage)
        if favorite is not None:
            clauses.append("favorite = ?")
            params.append(int(favorite))

        sort_columns = {
            "found_at": "found_at",
            "title": "title COLLATE NOCASE",
            "subscribers": "subscribers_num",
            "views": "views_num",
            "country": "country COLLATE NOCASE",
            "stage": "pipeline_stage",
        }
        sort_sql = sort_columns.get(sort, "found_at")
        order_sql = "ASC" if order.lower() == "asc" else "DESC"
        where = " AND ".join(clauses)
        offset = (max(page, 1) - 1) * page_size

        with self.connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM creators WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM creators WHERE {where} ORDER BY {sort_sql} {order_sql}, title ASC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        return {
            "items": [self._decode_creator(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def facet_values(self) -> dict[str, list[str]]:
        with self.connection() as conn:
            regions = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT region_searched FROM creators WHERE archived=0 AND region_searched!='' ORDER BY 1"
                )
            ]
            countries = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT country FROM creators WHERE archived=0 AND country!='' ORDER BY 1"
                )
            ]
            niche_rows = conn.execute(
                "SELECT niches FROM creators WHERE archived=0"
            ).fetchall()
        niches = sorted(
            {
                niche
                for row in niche_rows
                for niche in json.loads(row[0] or "[]")
                if niche
            }
        )
        return {"regions": regions, "countries": countries, "niches": niches}

    def stats(self) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self.connection() as conn:
            totals = conn.execute(
                """SELECT
                    COUNT(*) total,
                    SUM(CASE WHEN emails != '[]' THEN 1 ELSE 0 END) with_email,
                    SUM(CASE WHEN instagram != '' OR twitter != '' OR tiktok != '' OR linkedin != '' THEN 1 ELSE 0 END) with_social,
                    SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END) favorites,
                    SUM(CASE WHEN found_at >= ? THEN 1 ELSE 0 END) added_30d,
                    COUNT(DISTINCT NULLIF(country, '')) countries,
                    COALESCE(SUM(subscribers_num), 0) audience
                   FROM creators WHERE archived=0""",
                (since,),
            ).fetchone()
            stages = conn.execute(
                "SELECT pipeline_stage, COUNT(*) count FROM creators WHERE archived=0 GROUP BY pipeline_stage"
            ).fetchall()
            regions = conn.execute(
                """SELECT region_searched label, COUNT(*) value FROM creators
                   WHERE archived=0 AND region_searched!=''
                   GROUP BY region_searched ORDER BY value DESC, label LIMIT 6"""
            ).fetchall()
            countries = conn.execute(
                """SELECT country label, COUNT(*) value FROM creators
                   WHERE archived=0 AND country!=''
                   GROUP BY country ORDER BY value DESC, label LIMIT 6"""
            ).fetchall()
            contacts = conn.execute(
                """SELECT contact_source label, COUNT(*) value FROM creators
                   WHERE archived=0 GROUP BY contact_source ORDER BY value DESC"""
            ).fetchall()

        total = totals["total"] or 0
        with_email = totals["with_email"] or 0
        return {
            "total": total,
            "with_email": with_email,
            "with_social": totals["with_social"] or 0,
            "favorites": totals["favorites"] or 0,
            "added_30d": totals["added_30d"] or 0,
            "countries": totals["countries"] or 0,
            "audience": totals["audience"] or 0,
            "contact_rate": round((with_email / total * 100) if total else 0, 1),
            "pipeline": {row["pipeline_stage"]: row["count"] for row in stages},
            "top_regions": [dict(row) for row in regions],
            "top_countries": [dict(row) for row in countries],
            "contact_sources": [dict(row) for row in contacts],
        }

    def update_creator(self, channel_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"pipeline_stage", "favorite", "notes"}
        updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
        if not updates:
            return self.get_creator(channel_id)
        if "pipeline_stage" in updates and updates["pipeline_stage"] not in STAGES:
            raise ValueError("Invalid pipeline stage")
        if "favorite" in updates:
            updates["favorite"] = int(bool(updates["favorite"]))
        updates["updated_at"] = utcnow()
        assignment = ", ".join(f"{key}=?" for key in updates)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE creators SET {assignment} WHERE channel_id=?",
                [*updates.values(), channel_id],
            )
        return self.get_creator(channel_id)

    def bulk_update(self, channel_ids: list[str], action: str, stage: str | None = None) -> int:
        if not channel_ids:
            return 0
        placeholders = ",".join("?" for _ in channel_ids)
        if action == "favorite":
            clause, values = "favorite=1", []
        elif action == "unfavorite":
            clause, values = "favorite=0", []
        elif action == "archive":
            clause, values = "archived=1", []
        elif action == "restore":
            clause, values = "archived=0", []
        elif action == "set_stage" and stage in STAGES:
            clause, values = "pipeline_stage=?", [stage]
        else:
            raise ValueError("Invalid bulk action")
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE creators SET {clause}, updated_at=? WHERE channel_id IN ({placeholders})",
                [*values, utcnow(), *channel_ids],
            )
            return cursor.rowcount

    def export_rows(self, channel_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        ids = list(channel_ids or [])
        with self.connection() as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT * FROM creators WHERE channel_id IN ({placeholders}) ORDER BY title",
                    ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM creators WHERE archived=0 ORDER BY found_at DESC"
                ).fetchall()
        return [self._decode_creator(row) for row in rows]

    def create_job(self, job: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO jobs(
                    id,status,region_code,region_name,niches,target,message,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    job["id"], "queued", job["region_code"], job["region_name"],
                    _json(job["niches"], []), job["target"], "Waiting to start", utcnow(),
                ),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "discovered", "processed", "found", "skipped", "message",
            "error", "cancel_requested", "started_at", "finished_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignment = ", ".join(f"{key}=?" for key in updates)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignment} WHERE id=?",
                [*updates.values(), job_id],
            )

    @staticmethod
    def _decode_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        job["niches"] = json.loads(job.get("niches") or "[]")
        job["cancel_requested"] = bool(job.get("cancel_requested"))
        target = max(job.get("target") or 0, 1)
        job["progress"] = min(100, round((job.get("found", 0) / target) * 100))
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode_job(row)

    def list_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def active_job_count(self) -> int:
        with self.connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
