# llms.txt Generator

Enter a website URL and get an [`llms.txt`](https://llmstxt.org) file for it. Turn on
monitoring and the app re-checks the site every day, regenerating the file when the
site changes and serving the latest version at a stable link.

**Live app:** https://web-production-905dcb88.up.railway.app

<!-- Screenshots: add docs/screenshots/*.png here -->

## How it works

1. **Crawl:** a best-first crawler with a priority queue finds the ~50 most important
   pages, using sitemaps, the homepage navigation and link scores. It respects
   `robots.txt`, stays on the site and skips login, cart, tag and pagination pages.
2. **Extract:** for each page it reads the title, description and markdown version (if
   the site publishes one).
3. **Generate:** builds a spec-compliant llms.txt (H1, summary, H2 link sections,
   `## Optional`) and validates it.
4. **Quality report:** scores the file out of 100 and flags weaknesses: pages without
   descriptions, missing menu pages, no `.md` versions, oversized files, partial crawls.
5. **Monitor:** stored sites get a daily ETag check (conditional requests). If anything
   changed, the site is re-crawled and a new version is saved if the file differs.
6. **Cache:** generating a stored site that was checked in the last 24 hours returns the
   saved version instantly. **Regenerate now** forces a fresh crawl (`"refresh": true` in the API).

Design docs: [PLAN.md](PLAN.md) · [crawler](docs/crawler.md) · [output format](docs/llms-txt-format.md) ·
[monitoring](docs/monitoring.md) · [database](docs/database.md) · [architecture](docs/architecture.md) ·
[testing against real sites](docs/testing.md)

## Tech stack

Python 3.12, FastAPI, httpx, BeautifulSoup, SQLAlchemy + SQLite, APScheduler ·
React + Vite + TypeScript · one Docker image · Railway.

## Run locally

### Option 1: Docker (closest to production)

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

Open http://localhost:8000. The SQLite database is stored in `./data`.

### Option 2: without Docker (for development)

Requires Python 3.12+ and Node 20+.

```bash
cp .env.example .env
```

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
cd frontend && npm install && npm run build
```

Then start the server from the repo root:

```bash
backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

Open http://localhost:8000 (FastAPI serves the built frontend). For frontend hot
reload, also run `npm run dev` in `frontend/` and open http://localhost:5173; Vite
proxies `/api` and `/s` to port 8000.

### Command line

Generate a file without the web app:

```bash
cd backend && .venv/bin/python -m app.cli https://fastht.ml/docs --out llms.txt
```

### Tests

```bash
cd backend && .venv/bin/python -m pytest
```

Compare our output with sites' published llms.txt files ([docs/testing.md](docs/testing.md)):

```bash
cd backend && .venv/bin/python scripts/compare.py https://fastht.ml/docs https://www.limecraft.com
```

## Configuration

All settings come from environment variables or `.env` (see [.env.example](.env.example)
for the full list, and [backend/app/config.py](backend/app/config.py) for defaults).
No API keys are needed: the app only fetches public pages.

| Setting | Default | What it does |
|---|---|---|
| `CRAWL_MAX_PAGES` | 50 | Pages per crawl |
| `CRAWL_MAX_DEPTH` | 3 | Max clicks from the start URL |
| `CRAWL_TIMEOUT_SECONDS` | 45 | Total time budget per crawl |
| `CRAWL_CONCURRENCY` | 5 | Parallel requests |
| `CRAWL_PROBE_MARKDOWN` | true | Look for `.md` versions of pages |
| `SCORE_*` | see docs | Priority-queue weights ([crawler.md](docs/crawler.md#5-scoring-priority-queue)) |
| `MONITOR_CHECK_INTERVAL_HOURS` | 24 | How often each stored site gets an ETag check |
| `CACHE_MAX_AGE_HOURS` | 24 | Serve a stored site instantly if it was checked this recently (0 = always crawl) |
| `MONITOR_FULL_CRAWL_TTL_DAYS` | 7 | Force a full re-crawl after this long (0 = off) |
| `DATABASE_URL` | `sqlite:///data/llms.db` | SQLite file location |
| `PUBLIC_BASE_URL` | (request host) | Base URL used in stable links |

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/generate` | `{"url": "...", "monitor": false, "refresh": false}` → llms.txt, crawl stats, quality report, `cached`, stable link if monitored |
| GET | `/s/{id}/llms.txt` | Latest llms.txt for a monitored site |
| GET | `/docs` | Interactive API docs |

## Deploy to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**. Railway builds the `Dockerfile`.
3. **Add a volume** to the service, mounted at `/data`, so the SQLite file survives
   redeploys. (`DATABASE_URL` already points to `/data/llms.db` in the image.)
4. Keep **one replica**: SQLite has one writer and the scheduler runs inside the app.
5. **Settings → Networking → Generate Domain** to get a public `*.up.railway.app` URL.
6. Optional: set `PUBLIC_BASE_URL` to that URL, plus any settings from `.env.example`.

Railway sets `PORT`; the container listens on it.

The live deployment uses:

```
PUBLIC_BASE_URL=https://web-production-905dcb88.up.railway.app
```

so stable links look like `https://web-production-905dcb88.up.railway.app/s/{site_id}/llms.txt`.

## Project layout

```
backend/app/       FastAPI app, crawler, generator, monitoring, SQLite models
backend/tests/     pytest suite (fake sites via httpx.MockTransport, no network)
backend/scripts/   compare.py: check output against published llms.txt files
frontend/          React + Vite single-page UI
docs/              design docs
Dockerfile         multi-stage: build frontend, then Python image serving both
```

## Known limitations

- JavaScript-rendered sites: the crawler reads server-rendered HTML only.
- Large sites get a ~50-page overview, not a full index (configurable).
- Sites that block bots (e.g. `429`/`403` from a CDN) can't be crawled.
- Descriptions with live values ("Join 10,234 developers") create a new version whenever they change.
