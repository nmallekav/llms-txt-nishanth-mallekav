"""FastAPI app: the generate API, stable llms.txt links, and the React frontend."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import urls
from .config import get_settings
from .crawler import CrawlError
from .db import SessionLocal, Site, init_db, utcnow
from .generate import generate
from .monitor import cached_site, start_scheduler, store_site

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db(settings.database_url)
    scheduler = start_scheduler(settings) if settings.monitor_enabled else None
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="llms.txt Generator", lifespan=lifespan)


# ---------- API models ----------

class GenerateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    monitor: bool = False
    refresh: bool = False  # skip the cache and crawl now ("Regenerate now")


class CrawlStats(BaseModel):
    pages: int
    sitemaps: int
    duration_seconds: float
    stop_reason: str
    partial: bool


class QualityCheck(BaseModel):
    name: str
    status: str  # "ok" or "warn"
    message: str


class Quality(BaseModel):
    score: int
    checks: list[QualityCheck]


class GenerateResponse(BaseModel):
    llms_txt: str
    url: str
    stats: CrawlStats
    problems: list[str]
    quality: Quality
    site_id: str | None = None
    stable_url: str | None = None
    cached: bool = False  # true if served from the database instead of a fresh crawl
    crawled_at: datetime  # when this llms.txt was crawled
    checked_at: datetime | None = None  # when the monitor last confirmed it was current


def stable_url(request: Request, site_id: str) -> str:
    base = get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/s/{site_id}/llms.txt"


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True}


def utc(dt: datetime | None) -> datetime | None:
    """DB times are naive UTC; mark them as UTC so the browser shows the right local time."""
    return dt.replace(tzinfo=timezone.utc) if dt else None


def cached_response(site: Site, request: Request) -> GenerateResponse:
    return GenerateResponse(
        llms_txt=site.current_version.llms_txt,
        url=site.url,
        stats=CrawlStats(**site.crawl_stats),
        problems=[],
        quality=Quality(**site.quality),
        site_id=site.id,
        stable_url=stable_url(request, site.id),
        cached=True,
        crawled_at=utc(site.last_crawled_at),
        checked_at=utc(site.last_checked_at),
    )


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_llms_txt(body: GenerateRequest, request: Request):
    settings = get_settings()

    # 1. A stored site that was confirmed current recently: return it instantly.
    key = urls.normalize(urls.ensure_scheme(body.url))
    if key and not body.refresh:
        with SessionLocal() as session:
            site = cached_site(session, key, settings)
            if site:
                return cached_response(site, request)

    # 2. Otherwise crawl now.
    try:
        gen = await generate(body.url, settings)
    except CrawlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 3. Store it if the user asked to monitor it, or refresh it if it's already monitored.
    site_id = None
    with SessionLocal() as session:
        already_stored = session.scalar(select(Site.id).where(Site.url == gen.crawl.start_url))
        if body.monitor or already_stored:
            site_id = store_site(session, gen.crawl, settings).id

    crawl = gen.crawl
    now = utc(utcnow())
    return GenerateResponse(
        llms_txt=gen.llms_txt,
        url=crawl.start_url,
        stats=CrawlStats(pages=len(crawl.pages), sitemaps=len(crawl.sitemaps),
                         duration_seconds=crawl.duration_seconds, stop_reason=crawl.stop_reason,
                         partial=crawl.partial),
        problems=gen.problems,
        quality=Quality(score=gen.quality.score,
                        checks=[QualityCheck(**vars(c)) for c in gen.quality.checks]),
        site_id=site_id,
        stable_url=stable_url(request, site_id) if site_id else None,
        crawled_at=now,
        checked_at=now if site_id else None,
    )


@app.get("/s/{site_id}/llms.txt", response_class=PlainTextResponse)
def stable_llms_txt(site_id: str):
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        if site is None or site.current_version is None:
            raise HTTPException(status_code=404, detail="Site not found")
        return PlainTextResponse(site.current_version.llms_txt, media_type="text/plain; charset=utf-8")


# ---------- frontend ----------

dist = Path(get_settings().frontend_dist)
if (dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist.resolve()):
            return FileResponse(file)
        return FileResponse(dist / "index.html")
