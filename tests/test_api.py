import asyncio
from pathlib import Path

import httpx

import app.main as main_module
from app.database import Database
from app.scraper_service import JobManager


def test_job_endpoint_schedules_on_running_event_loop(tmp_path: Path, monkeypatch):
    """Regression: a sync FastAPI route ran in a worker thread and create_task failed."""
    database = Database(tmp_path / "api.db")
    manager = JobManager(database)
    monkeypatch.setattr(main_module, "db", database)
    monkeypatch.setattr(main_module, "jobs", manager)

    async def scenario():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/jobs",
                json={
                    "regions": ["NG", "CA"],
                    "niches": ["vlog"],
                    "limit": 10,
                    "preferences": {
                        "min_subscribers": 10_000,
                        "max_subscribers": 100_000,
                        "min_videos": 25,
                        "min_channel_age": 3,
                        "contact_requirement": "email",
                    },
                },
            )
            assert response.status_code == 202
            payload = response.json()
            assert [region["code"] for region in payload["regions"]] == ["NG", "CA"]
            assert payload["preferences"]["min_subscribers"] == 10_000
            assert payload["preferences"]["contact_requirement"] == "email"
            # The no-session path can finish before we retrieve the task because
            # the manager removes completed tasks immediately.
            await asyncio.sleep(0.05)
            finished = database.get_job(payload["id"])
            # No test login exists, but scheduling succeeded and failed cleanly for
            # the expected reason—not with "no running event loop".
            assert finished["status"] == "failed"
            assert finished["error"] == "YouTube session is not connected"

            clear_response = await client.delete("/api/creators")
            assert clear_response.status_code == 200
            assert clear_response.json()["cleared"]["jobs"] == 1
            assert database.list_creators()["total"] == 0

    asyncio.run(scenario())


def test_workspace_key_protects_api_but_not_health(tmp_path: Path, monkeypatch):
    database = Database(tmp_path / "protected.db")
    monkeypatch.setattr(main_module, "db", database)
    monkeypatch.setattr(main_module, "API_KEY", "correct-horse-battery-staple")

    async def scenario():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200

            denied = await client.get(
                "/api/stats", headers={"Origin": "http://localhost:3000"}
            )
            assert denied.status_code == 401
            assert denied.headers["access-control-allow-origin"] == "http://localhost:3000"

            allowed = await client.get(
                "/api/stats",
                headers={"X-Creator-Key": "correct-horse-battery-staple"},
            )
            assert allowed.status_code == 200

            wrong_login = await client.post(
                "/api/auth/login", json={"key": "wrong-key"}
            )
            assert wrong_login.status_code == 401
            assert "does not match" in wrong_login.json()["detail"]

            login = await client.post(
                "/api/auth/login",
                json={"key": "CREATORSCOPE_API_KEY=correct-horse-battery-staple"},
            )
            assert login.status_code == 200
            assert "HttpOnly" in login.headers["set-cookie"]
            cookie_access = await client.get("/api/stats")
            assert cookie_access.status_code == 200

            preflight = await client.options(
                "/api/stats",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "X-Creator-Key",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"

    asyncio.run(scenario())
