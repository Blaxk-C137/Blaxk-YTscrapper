# CreatorScope

A private, polished creator-discovery workspace built from the original
YouTube Creator Finder script.

CreatorScope searches YouTube by market and niche, reviews channel About pages,
collects creator-published business contact points, and turns the results into a
usable relationship pipeline—not another raw CSV.

## What changed

- **Complete web interface** — responsive overview, creator directory,
  discovery studio, settings, and creator detail drawer.
- **Real backend** — FastAPI API with local SQLite or hosted PostgreSQL,
  pagination, filters, sorting, exports, job history, and background runs.
- **Mini CRM** — favorites, private notes, and stages from New to Partnered.
- **Useful analytics** — contact coverage, audience footprint, top markets, and
  partnership-pipeline health.
- **Clean by default** — every fresh install starts with an empty directory;
  only creators found by your own discovery runs are stored.
- **Target-driven discovery** — pagination continues past processed, skipped,
  and filtered channels until the requested number of matching contacts is
  saved, search results are exhausted, or the documented safety ceiling is hit.
- **Creator preferences** — qualify by subscriber range, upload count, channel
  age, and required contact type before a result counts toward the target.
- **Live progress** — the fresh-pool count updates after every search page and
  the dashboard refreshes active-run counters about once per second.
- **Conservative automation** — one discovery run at a time, bounded
  concurrency, no CAPTCHA bypass, and partial results survive interruption.
- **Deployable for free** — Vercel static frontend, Render Free backend, Neon
  PostgreSQL persistence, environment-backed YouTube session, CORS, and a
  browser-entered workspace key.
- **Security cleanup** — browser auth is ignored by Git and all local runtime
  state lives outside source control.

## Quick start

Requirements: Python 3.10+ and a machine capable of running Chromium.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install chromium

# Start the dashboard
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open <http://localhost:8000>.

The dashboard starts empty. Connect a local YouTube session, then run your own
discoveries to build the directory:

```bash
python scripts/connect_youtube.py
```

A Chromium window opens. Sign in directly on YouTube, return to the terminal,
and press Enter. Credentials are never entered into CreatorScope. Chromium
stores the local profile in `browser_profile/`; the helper also writes ignored
storage-state exports under `.runtime/` for an optional hosted deployment. Treat
all of those files as account-access secrets.

> **Security notice:** the original repository committed a browser profile.
> Deleting it from the current tree is not enough. Follow [SECURITY.md](SECURITY.md)
> to revoke the old session and purge it from Git history.

## Using the app

### Overview

Track the size and reach of the creator directory, contact coverage, leading
markets, and partnership stages. The recent-creator list opens full profiles.

### Creators

Search names, handles, emails, and descriptions. Filter by market, niche,
contact type, or stage. Use bulk actions to shortlist, favorite, or export a
selection. Each creator profile includes contacts, stats, description, notes,
and the direct YouTube link.

### Discovery

Choose up to 10 markets, select one or more niches, then define the creator fit:
subscriber range, minimum uploads, channel age, and required contact type. The
save target counts only creators that match those preferences.

CreatorScope searches every market/niche combination, skips IDs already handled
in earlier runs while continuing into deeper result pages, and reviews fresh
batches until it reaches the target. If YouTube runs out of results—or the
2,000-review safety ceiling is reached—the final message says exactly why the
run ended and how many matching contacts were saved. Runs keep partial results
and can be stopped safely.

### Export

Download all creators—or just a selection—as UTF-8 CSV. A structured JSON
export is available in Settings.

## Architecture

```text
app/
  main.py              FastAPI routes, CORS/auth, and static app host
  database.py          SQLite/PostgreSQL schema, filters, CRM, analytics
  scraper_service.py   background jobs and local/hosted browser sessions
  schemas.py           validated API request models
static/
  index.html            dependency-free responsive SPA
  styles.css            design system and layouts
  app.js                remote/local API and dashboard state
  build_frontend.mjs    isolated Vercel static build
  vercel.json           Vercel config (use `static` as Root Directory)
scripts/
  connect_youtube.py    interactive session setup and hosted export

render.yaml             Render Free Blueprint
vercel.json             repository-root compatibility configuration
youtube_creator_finder.py   scraper engine and CLI
```

Runtime data is stored in `.runtime/creatorscope.db` by default. The path can be
changed with `CREATORSCOPE_DATA_DIR` or `CREATORSCOPE_DB_PATH`. Supplying a
PostgreSQL `DATABASE_URL` switches persistence to PostgreSQL with a small
connection pool.

## Free hosted deployment

Use [DEPLOYMENT.md](DEPLOYMENT.md) for the complete Vercel + Render Free + Neon
walkthrough, including the private workspace key, exact CORS setup, hosted
YouTube session export, verification, and free-tier throughput guidance.

## API

Interactive documentation is available at `/api/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | Dashboard analytics |
| `GET /api/creators` | Paginated/searchable creator directory |
| `PATCH /api/creators/{id}` | Update stage, favorite, or private notes |
| `POST /api/creators/bulk` | Bulk pipeline/favorite/archive actions |
| `DELETE /api/creators` | Clear creators, processed IDs, and run history |
| `POST /api/jobs` | Start a multi-market discovery run (`regions`, `niches`, `limit`) |
| `GET /api/jobs` | Job history and progress |
| `POST /api/jobs/{id}/cancel` | Stop after the current channel |
| `GET /api/export.csv` | Spreadsheet-ready export |
| `GET /api/export.json` | Structured backup |

Example discovery request:

```json
{
  "regions": ["NG", "CA"],
  "niches": ["vlog", "lifestyle"],
  "limit": 50,
  "preferences": {
    "min_subscribers": 10000,
    "max_subscribers": 100000,
    "min_videos": 50,
    "min_channel_age": 3,
    "contact_requirement": "email"
  }
}
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The original parser tests remain intact. The suite covers metric normalization,
persistence/querying, analytics, CRM updates, API auth/CORS, portable SQL,
creator preferences, search pagination, and the job lifecycle.

## Docker

```bash
docker compose up --build
```

The dashboard is available on port 8000 and database state persists in a named
volume. Interactive session setup is easiest on the host; mount the generated
`browser_profile/` only into a trusted local container.

## Responsible use

Use creator-provided business details only for relevant, personalized outreach.
Honor opt-outs, avoid bulk unsolicited messages, follow applicable privacy and
anti-spam law, and comply with YouTube's terms. CreatorScope does not solve or
bypass CAPTCHAs.
