import asyncio
from pathlib import Path

import httpx

import app.main as main_module
from app.database import Database
from app.scraper_service import JobManager


def test_job_endpoint_schedules_on_running_event_loop(tmp_path: Path, monkeypatch):
    """Regression: a sync FastAPI route ran in a worker thread and create_task failed."""
    database = Database(tmp_path / "api.db", seed_cache=None)
    manager = JobManager(database)
    monkeypatch.setattr(main_module, "db", database)
    monkeypatch.setattr(main_module, "jobs", manager)

    async def scenario():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/jobs",
                json={"regions": ["NG", "CA"], "niches": ["vlog"], "limit": 10},
            )
            assert response.status_code == 202
            payload = response.json()
            assert [region["code"] for region in payload["regions"]] == ["NG", "CA"]
            # The no-session path can finish before we retrieve the task because
            # the manager removes completed tasks immediately.
            await asyncio.sleep(0.05)
            finished = database.get_job(payload["id"])
            # No test login exists, but scheduling succeeded and failed cleanly for
            # the expected reason—not with "no running event loop".
            assert finished["status"] == "failed"
            assert finished["error"] == "YouTube session is not connected"

    asyncio.run(scenario())
