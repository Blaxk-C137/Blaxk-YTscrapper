"""Application paths and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("CREATORSCOPE_DATA_DIR", ROOT_DIR / ".runtime"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("CREATORSCOPE_DB_PATH", DATA_DIR / "creatorscope.db"))
PROFILE_DIR = Path(os.getenv("CREATORSCOPE_PROFILE_DIR", ROOT_DIR / "browser_profile"))
STATIC_DIR = ROOT_DIR / "static"
EXPORT_DIR = ROOT_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

APP_NAME = "CreatorScope"
APP_VERSION = "1.3.1"
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

# One discovery run at a time protects the YouTube account and keeps request
# volume deliberately low.
MAX_ACTIVE_JOBS = 1
