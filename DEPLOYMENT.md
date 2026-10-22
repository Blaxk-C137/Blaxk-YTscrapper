# Free deployment: Vercel + Render + Neon

This setup keeps the dependency-free frontend on Vercel, runs one conservative
Chromium worker on Render Free, and stores leads in persistent PostgreSQL on
Neon. It does **not** depend on Render's ephemeral filesystem.

## Architecture

```text
Browser → Vercel static frontend → Render FastAPI/Playwright → YouTube
                                  ↘ Neon PostgreSQL
```

- The browser sends a private workspace key with every protected API request.
- Vercel receives only the Render API URL. Never put the private key or YouTube
  session in a Vercel variable; build-time frontend values are public.
- Local development keeps using SQLite. A hosted deployment uses `DATABASE_URL`.
- Render runs one web process and one discovery job at a time. Review concurrency
  defaults to 2 to fit the Free plan's 512 MB memory limit.

## Before deploying

1. Revoke any old Google session that was ever committed to Git.
2. Force-push the cleaned repository history described in [SECURITY.md](SECURITY.md).
3. Make sure `.runtime/`, `browser_profile/`, storage-state files, and `.env` are
   not tracked.

## 1. Create persistent PostgreSQL

Create a free Neon project and copy its **pooled** PostgreSQL connection string.
It normally starts with `postgresql://` and should include TLS settings such as
`sslmode=require`. CreatorScope creates its tables on first boot.

Do not use Render's local SQLite file for production: Render Free can discard it
on any restart, spin-down, or deployment. Render's own Free Postgres database
also expires after 30 days, which makes it unsuitable for a lasting lead list.

A new hosted database starts empty by design. Only creators discovered by your
hosted workspace appear in it.

## 2. Export a hosted YouTube session

On a trusted computer with a visible browser:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install chromium
python scripts/connect_youtube.py
```

Sign in directly on YouTube and press Enter in the terminal. The helper writes:

- `.runtime/youtube-storage-state.json`
- `.runtime/youtube-storage-state.b64`

The second file is the value for Render's `YOUTUBE_STORAGE_STATE_B64` variable.
Both files are secrets that can grant access to the signed-in account. Never
commit, upload, paste into Vercel, or share them. Prefer a dedicated Google
account with appropriate security controls.

If YouTube later reports an expired session, rerun the helper, replace the
Render variable, and redeploy the service.

## 3. Deploy the backend to Render Free

Generate a strong workspace key locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

In Render, create a new **Blueprint** from this repository. Render reads
`render.yaml` and builds `Dockerfile`. Supply all four secret variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon pooled PostgreSQL connection string |
| `CREATORSCOPE_API_KEY` | Random key generated above |
| `YOUTUBE_STORAGE_STATE_B64` | Entire contents of `.runtime/youtube-storage-state.b64` |
| `CORS_ORIGINS` | Use `*` for the first deployment; replace it after Vercel is deployed |

Keep these non-secret Blueprint defaults unless the Render plan changes:

```text
CREATORSCOPE_MAX_ACTIVE_JOBS=1
CREATORSCOPE_REVIEW_CONCURRENCY=2
CREATORSCOPE_API_CONCURRENCY=4
CREATORSCOPE_DB_POOL_SIZE=4
```

Deploy and open:

```text
https://YOUR-RENDER-SERVICE.onrender.com/api/health
```

A healthy hosted response reports `database: "postgresql"`,
`deployment: "render-free"`, and version `1.4.0`. The first request after an idle
period can take about a minute while Render wakes the service.

CreatorScope intentionally refuses to start in Render mode without PostgreSQL
or a workspace key. This prevents accidental ephemeral or public deployments.

## 4. Deploy the frontend to Vercel

Import the same repository into Vercel. `vercel.json` runs the static build and
publishes `public/`. Add this project environment variable for Production (and
Preview if desired):

| Variable | Value |
|---|---|
| `CREATORSCOPE_API_URL` | `https://YOUR-RENDER-SERVICE.onrender.com` |

Do not add a trailing slash. Deploy, then copy the production Vercel URL.

Return to Render and replace `CORS_ORIGINS=*` with the exact frontend origin:

```text
https://YOUR-PROJECT.vercel.app
```

Multiple exact origins can be comma-separated. For example:

```text
https://YOUR-PROJECT.vercel.app,http://localhost:3000
```

Save the Render variables and allow the service to redeploy. In the Vercel app,
open **Settings → Private API key**, enter `CREATORSCOPE_API_KEY`, and save it.
The key stays in that browser's local storage and is sent only to the Render
backend.

## 5. Verify the complete path

1. The Vercel dashboard loads without a CORS error.
2. Saving the workspace key loads overview counters.
3. Settings reports **Hosted session ready**.
4. Start a small discovery (for example, 2 contacts in one market).
5. Confirm Fresh pool and Reviewed counts move live.
6. Stop or finish the run, then refresh both Vercel and Render; saved creators
   remain because they are in Neon.
7. Test CSV and JSON exports from the Vercel frontend.

## Getting the most leads from Render Free

- Keep the Vercel discovery tab open while a run is active. Its 1.2-second status
  polling provides inbound requests, which keeps the free Render service awake.
  If Render restarts anyway, partial leads remain saved and the interrupted run
  is marked failed on the next boot.
- Run one job at a time. Increasing browser page concurrency above 2 on a 512 MB
  instance can cause out-of-memory restarts and usually reduces, rather than
  improves, total lead throughput.
- Use broader creator preferences when contact volume matters most. Requiring a
  business email and a narrow subscriber range naturally needs many more channel
  reviews.
- Request multiple relevant countries and niches in one run. CreatorScope
  deduplicates channels and continues through fresh result pages until it reaches
  the contact target, genuine search exhaustion, cancellation, or the 2,000-review
  safety ceiling.
- Do not use external uptime-pinging services to defeat free-tier policies. The
  active dashboard already keeps a legitimate running job awake.

## Environment reference

### Backend (Render)

| Variable | Required | Purpose |
|---|---:|---|
| `DATABASE_URL` | Hosted | Persistent PostgreSQL; local mode falls back to SQLite |
| `CREATORSCOPE_API_KEY` | Hosted | Protects all API routes except health and docs |
| `CORS_ORIGINS` | Hosted | Comma-separated allowed frontend origins |
| `YOUTUBE_STORAGE_STATE_B64` | Discovery | Base64 Playwright storage-state JSON |
| `CREATORSCOPE_DEPLOYMENT_MODE` | Yes | `render-free` in the Blueprint |
| `CREATORSCOPE_REVIEW_CONCURRENCY` | No | Parallel channel pages; Free default is 2 |
| `CREATORSCOPE_API_CONCURRENCY` | No | YouTube internal API requests; Free default is 4 |
| `CREATORSCOPE_DB_POOL_SIZE` | No | PostgreSQL pool limit; default is 4 |
| `CREATORSCOPE_MAX_ACTIVE_JOBS` | No | Simultaneous discovery jobs; keep 1 on Free |

### Frontend (Vercel)

| Variable | Required | Purpose |
|---|---:|---|
| `CREATORSCOPE_API_URL` | Yes | Public HTTPS origin of the Render backend |

## Local development remains unchanged

Without `DATABASE_URL`, CreatorScope uses `.runtime/creatorscope.db`. Without
`CREATORSCOPE_API_KEY`, local API requests do not require a key. The backend
serves `static/` directly at <http://localhost:8000>.
