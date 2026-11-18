"""FastAPI app: the generate API, stable llms.txt links, and the React frontend."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import config, llmstxt, urls
from .crawler import CrawlError
from .db import SessionLocal, Site, init_db, utcnow
from .generate import generate
from .monitor import start_scheduler, store_site

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db(config.DATABASE_URL)
    scheduler = start_scheduler() if config.MONITOR_ENABLED else None
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="llms.txt Generator", lifespan=lifespan)


# ---------- API models ----------

class GenerateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    monitor: bool = False
    refresh: bool = False  # True = crawl again even if a recent saved copy exists


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
    stats: CrawlStats | None = None  # None for a saved copy
    problems: list[str]
    quality: Quality | None = None  # None for a saved copy
    site_id: str | None = None
    stable_url: str | None = None
    cached_at: datetime | None = None  # set when this is a saved copy: when it was crawled


def stable_url(request: Request, site_id: str) -> str:
    base = config.PUBLIC_BASE_URL.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/s/{site_id}/llms.txt"


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_llms_txt(body: GenerateRequest, request: Request):
    # 1. If this site is stored and was crawled recently, return the saved file (unless asked to regenerate).
    if not body.refresh:
        with SessionLocal() as session:
            url = urls.normalize(urls.ensure_scheme(body.url))
            site = session.scalar(select(Site).where(Site.url == url))
            if site and utcnow() - site.last_crawled_at < timedelta(hours=config.CACHE_HOURS):
                text = site.current_version.llms_txt
                return GenerateResponse(
                    llms_txt=text, url=site.url, problems=llmstxt.validate(text),
                    site_id=site.id, stable_url=stable_url(request, site.id),
                    cached_at=site.last_crawled_at.replace(tzinfo=timezone.utc),
                )

    # 2. Crawl the site and build the llms.txt.
    try:
        gen = await generate(body.url)
    except CrawlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 3. Store it if the user asked to monitor it, or update it if it's already monitored.
    site_id = None
    with SessionLocal() as session:
        already_stored = session.scalar(select(Site.id).where(Site.url == gen.crawl.start_url))
        if body.monitor or already_stored:
            site_id = store_site(session, gen.crawl).id

    crawl = gen.crawl
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
    )


@app.get("/s/{site_id}/llms.txt", response_class=PlainTextResponse)
def stable_llms_txt(site_id: str):
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        if site is None or site.current_version is None:
            raise HTTPException(status_code=404, detail="Site not found")
        return PlainTextResponse(site.current_version.llms_txt, media_type="text/plain; charset=utf-8")


# ---------- frontend ----------

dist = Path(config.FRONTEND_DIST)
if (dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist.resolve()):
            return FileResponse(file)
        return FileResponse(dist / "index.html")
