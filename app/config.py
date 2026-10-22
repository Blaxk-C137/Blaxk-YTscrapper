"""Application paths and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

def clean_workspace_key(value: str) -> str:
    """Tolerate common dashboard copy/paste wrappers without weakening the key."""
    text = (value or "").strip()
    for _ in range(2):
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"}:
            text = text[1:-1].strip()
        if text.startswith("CREATORSCOPE_API_KEY="):
            text = text.split("=", 1)[1].strip()
    return text


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("CREATORSCOPE_DATA_DIR", ROOT_DIR / ".runtime"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("CREATORSCOPE_DB_PATH", DATA_DIR / "creatorscope.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_POOL_SIZE = max(1, int(os.getenv("CREATORSCOPE_DB_POOL_SIZE", "4")))
PROFILE_DIR = Path(os.getenv("CREATORSCOPE_PROFILE_DIR", ROOT_DIR / "browser_profile"))
YOUTUBE_STORAGE_STATE_B64 = os.getenv("YOUTUBE_STORAGE_STATE_B64", "").strip()
DEPLOYMENT_MODE = os.getenv("CREATORSCOPE_DEPLOYMENT_MODE", "local").strip().lower()
IS_RENDER_FREE = DEPLOYMENT_MODE == "render-free" or bool(os.getenv("RENDER"))
REVIEW_CONCURRENCY = max(
    1, int(os.getenv("CREATORSCOPE_REVIEW_CONCURRENCY", "2" if IS_RENDER_FREE else "6"))
)
API_CONCURRENCY_LIMIT = max(
    1, int(os.getenv("CREATORSCOPE_API_CONCURRENCY", "4" if IS_RENDER_FREE else "8"))
)
CORS_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:8000"
    ).split(",")
    if origin.strip()
]
API_KEY = clean_workspace_key(os.getenv("CREATORSCOPE_API_KEY", ""))
STATIC_DIR = ROOT_DIR / "static"
EXPORT_DIR = ROOT_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

APP_NAME = "CreatorScope"
APP_VERSION = "1.5.0"
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

# One discovery run at a time protects the YouTube account and Render's small
# free instance. Paid deployments can opt in to more workers explicitly.
MAX_ACTIVE_JOBS = max(1, int(os.getenv("CREATORSCOPE_MAX_ACTIVE_JOBS", "1")))
