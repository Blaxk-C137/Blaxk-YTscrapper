#!/usr/bin/env python3
"""Create or refresh CreatorScope's local YouTube browser session.

Credentials are entered directly into Chromium. CreatorScope never receives or
stores the password; Chromium stores only the resulting local browser profile.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

# Allow direct execution from the repository root or the scripts directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

from app.config import PROFILE_DIR
from youtube_creator_finder import AUTH_COOKIES, BASE_URL, dismiss_consent

STATE_PATH = ROOT / ".runtime" / "youtube-storage-state.json"
STATE_B64_PATH = ROOT / ".runtime" / "youtube-storage-state.b64"


async def connect() -> int:
    print("\nCreatorScope · YouTube session setup")
    print("-" * 42)
    print("A Chromium window will open. Sign in to the YouTube account you want")
    print("to use for discovery, then return here and press Enter.")
    print(f"\nLocal profile: {PROFILE_DIR}\n")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await pw.chromium.launch_persistent_context(channel="chromium", **kwargs)
        except Exception:
            context = await pw.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        await dismiss_consent(page)
        await asyncio.get_running_loop().run_in_executor(
            None, input, "Press Enter after YouTube shows you as signed in… "
        )
        names = {cookie["name"] for cookie in await context.cookies(BASE_URL)}
        connected = bool(names & AUTH_COOKIES)
        if connected:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=STATE_PATH)
            STATE_B64_PATH.write_text(
                base64.b64encode(STATE_PATH.read_bytes()).decode("ascii"),
                encoding="utf-8",
            )
        await context.close()

    if connected:
        print("\n✓ Session connected. You can now start discovery from the web app.")
        print(f"✓ Hosted-session value written to: {STATE_B64_PATH}")
        print("  Copy that file's contents into Render as YOUTUBE_STORAGE_STATE_B64.")
        print("  Keep both exported session files private; they grant account access.")
        return 0
    print("\n! No authenticated YouTube session was detected. Please run this again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(connect()))
