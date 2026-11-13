"""Scheduled ETag checks and automatic re-crawls. Design: docs/monitoring.md.

Every site in the database is checked on a configurable interval. The check sends
conditional requests (If-None-Match / If-Modified-Since) for the homepage, sitemaps and
listed pages. Any change triggers a full re-crawl, and a new version is stored only if
the generated llms.txt differs from the current one.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .crawler import CrawlError, CrawlResult, Page, crawl, make_client
from .db import CheckRun, PageRow, SessionLocal, Site, SitemapRow, SiteVersion, utcnow
from .generate import render

log = logging.getLogger(__name__)

_in_progress: set[str] = set()


# ---------- storing crawls ----------

def fingerprint(page) -> str:
    raw = "\x1f".join([page.url, page.title or "", page.description or "", page.md_url or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _page_from_row(row: PageRow) -> Page:
    return Page(url=row.url, title=row.title, description=row.description, section=row.section,
                score=row.score, depth=0, md_url=row.md_url, etag=row.etag,
                last_modified=row.last_modified, status=row.http_status or 200,
                alt_descriptions=list(row.alt_descriptions or []))


def change_summary(old: dict[str, str], new: dict[str, str]) -> str:
    """Describe the difference between two {url: fingerprint} maps."""
    added = len(new.keys() - old.keys())
    removed = len(old.keys() - new.keys())
    updated = sum(1 for url in new.keys() & old.keys() if new[url] != old[url])
    parts = []
    if added:
        parts.append(f"+{added} page{'s' if added != 1 else ''}")
    if removed:
        parts.append(f"−{removed} page{'s' if removed != 1 else ''}")
    if updated:
        parts.append(f"{updated} page{'s' if updated != 1 else ''} updated")
    return ", ".join(parts) or "Site details or ordering changed"


def save_crawl(session: Session, site: Site, result: CrawlResult, settings: Settings) -> tuple[bool, str | None]:
    """Store a crawl's pages and sitemaps; add a new version if the llms.txt changed.

    Returns (changed, change_summary).
    """
    old_rows = {row.url: row for row in site.pages}
    pages = list(result.pages)

    # A page that only failed transiently (timeout, 5xx) keeps its previous entry.
    new_urls = {p.url for p in pages}
    for url, why in result.failed.items():
        if why == "transient" and url in old_rows and url not in new_urls:
            pages.append(_page_from_row(old_rows[url]))

    text = render(result, pages, settings)
    digest = content_hash(text)
    now = utcnow()

    site.name = result.site_name
    site.summary = result.summary
    site.homepage_url = result.homepage_url
    site.homepage_etag = result.homepage_etag
    site.homepage_last_modified = result.homepage_last_modified
    site.scope_path = result.scope_path
    site.last_crawled_at = now

    # Replace pages and sitemaps (delete first so the (site_id, url) unique constraints hold).
    for row in list(site.pages) + list(site.sitemaps):
        session.delete(row)
    session.flush()
    session.expire(site, ["pages", "sitemaps"])
    for p in pages:
        session.add(PageRow(
            site_id=site.id, url=p.url, title=p.title, description=p.description,
            alt_descriptions=p.alt_descriptions, md_url=p.md_url, section=p.section, score=p.score,
            fingerprint=fingerprint(p), etag=p.etag, last_modified=p.last_modified,
            http_status=p.status, last_fetched_at=now,
        ))
    for s in result.sitemaps:
        session.add(SitemapRow(site_id=site.id, url=s.url, etag=s.etag, last_modified=s.last_modified))

    current = site.current_version
    if current is not None and current.content_hash == digest:
        return False, None

    if current is None:
        summary = f"Initial version ({len(pages)} pages)"
    else:
        summary = change_summary({u: r.fingerprint for u, r in old_rows.items()}, {p.url: fingerprint(p) for p in pages})
    version = SiteVersion(site_id=site.id, llms_txt=text, content_hash=digest, page_count=len(pages),
                          change_summary=summary, created_at=now)
    session.add(version)
    session.flush()
    site.current_version = version
    site.last_changed_at = now
    return True, summary


def store_site(session: Session, result: CrawlResult, settings: Settings | None = None) -> Site:
    """Start monitoring a freshly crawled site (or refresh it if it's already stored)."""
    settings = settings or get_settings()
    site = session.scalar(select(Site).where(Site.url == result.start_url))
    trigger = "manual" if site else "initial"
    if site is None:
        site = Site(url=result.start_url)
        session.add(site)
        session.flush()
    changed, summary = save_crawl(session, site, result, settings)
    _mark_success(site, settings)
    session.add(CheckRun(site_id=site.id, kind="crawl", trigger=trigger,
                         result="changed" if changed else "unchanged", reason=summary,
                         pages_fetched=len(result.pages), finished_at=utcnow()))
    session.commit()
    return site


# ---------- the ETag check ----------

@dataclass
class _Target:
    url: str
    etag: str | None
    last_modified: str | None


async def etag_check(targets: list[_Target], settings: Settings, client: httpx.AsyncClient) -> tuple[str, str | None]:
    """Conditional GETs for each target. Returns ("unchanged" | "changed" | "failed", reason).

    Stops at the first changed URL. A URL with no ETag or Last-Modified counts as changed.
    """
    first_failure: str | None = None
    size = max(1, settings.crawl_concurrency)
    for i in range(0, len(targets), size):
        batch = targets[i:i + size]
        outcomes = await asyncio.gather(*(_check_one(t, settings, client) for t in batch))
        for outcome, reason in outcomes:
            if outcome == "changed":
                return "changed", reason
            if outcome == "failed" and first_failure is None:
                first_failure = reason
    if first_failure:
        return "failed", first_failure
    return "unchanged", None


async def _check_one(t: _Target, settings: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    path = urlsplit(t.url).path or "/"
    if t.etag:
        headers = {"If-None-Match": t.etag}
    elif t.last_modified:
        headers = {"If-Modified-Since": t.last_modified}
    else:
        return "changed", f"no ETag or Last-Modified: {path}"
    try:
        async with client.stream("GET", t.url, headers=headers, timeout=settings.crawl_request_timeout_seconds) as r:
            status = r.status_code
            etag, last_modified = r.headers.get("etag"), r.headers.get("last-modified")
    except httpx.HTTPError as e:
        return "failed", f"{type(e).__name__}: {path}"
    if status == 304:
        return "unchanged", path
    if status == 200:
        # Some servers/CDNs ignore conditional headers and always send 200; the same
        # validator in the response still means the same content.
        if (t.etag and etag == t.etag) or (not t.etag and last_modified and last_modified == t.last_modified):
            return "unchanged", path
        return "changed", f"etag changed: {path}"
    if status in (404, 410):
        return "changed", f"removed ({status}): {path}"
    return "failed", f"HTTP {status}: {path}"


def _targets(site: Site) -> list[_Target]:
    targets = [_Target(site.homepage_url or site.url, site.homepage_etag, site.homepage_last_modified)]
    targets += [_Target(s.url, s.etag, s.last_modified) for s in site.sitemaps]
    targets += [_Target(p.url, p.etag, p.last_modified) for p in site.pages
                if p.selected and p.url != site.homepage_url]
    return targets


# ---------- processing one site ----------

def _mark_success(site: Site, settings: Settings) -> None:
    now = utcnow()
    site.last_checked_at = now
    site.next_check_at = now + timedelta(hours=settings.monitor_check_interval_hours)
    site.fail_count = 0
    site.last_error = None
    if site.status == "failing":
        site.status = "active"


def _mark_failure(site: Site, error: str, settings: Settings) -> None:
    now = utcnow()
    site.last_checked_at = now
    site.fail_count += 1
    site.last_error = error
    if site.fail_count >= settings.monitor_max_retries:
        site.status = "failing"
        site.next_check_at = now + timedelta(hours=settings.monitor_check_interval_hours)
    else:
        site.next_check_at = now + timedelta(hours=settings.monitor_retry_hours)


async def process_site(site_id: str, force: bool = False, manual: bool = False,
                       settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> str:
    """Check one site and re-crawl it if needed. Returns the outcome for logging/API.

    force: skip the ETag check and re-crawl. manual: triggered by a user, not the scheduler.
    """
    settings = settings or get_settings()
    if site_id in _in_progress:
        return "busy"
    _in_progress.add(site_id)
    owns_client = client is None
    client = client or make_client(settings)
    try:
        return await _process(site_id, force, manual, settings, client)
    finally:
        _in_progress.discard(site_id)
        if owns_client:
            await client.aclose()


async def _process(site_id: str, force: bool, manual: bool, settings: Settings, client: httpx.AsyncClient) -> str:
    # 1. Read what we need, then release the session while doing network work.
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        if site is None:
            return "missing"
        url = site.url
        ttl = settings.monitor_full_crawl_ttl_days
        ttl_due = ttl > 0 and (site.last_crawled_at is None or utcnow() - site.last_crawled_at >= timedelta(days=ttl))
        targets = _targets(site)

    # 2. Decide whether to crawl.
    reason = None
    if force:
        trigger = "manual"
    elif ttl_due:
        trigger = "ttl"
    else:
        outcome, reason = await etag_check(targets, settings, client)
        with SessionLocal() as session:
            site = session.get(Site, site_id)
            session.add(CheckRun(site_id=site_id, kind="check", trigger="manual" if manual else "scheduled", result=outcome,
                                 reason=reason, pages_fetched=len(targets), finished_at=utcnow()))
            if outcome == "unchanged":
                _mark_success(site, settings)
            elif outcome == "failed":
                _mark_failure(site, reason or "check failed", settings)
            session.commit()
        if outcome != "changed":
            return outcome
        trigger = "change_detected"

    # 3. Re-crawl and store.
    started = utcnow()
    try:
        result = await crawl(url, settings, client)
        if result.stop_reason == "errors":
            raise CrawlError("too many errors during the crawl")
    except CrawlError as e:
        with SessionLocal() as session:
            site = session.get(Site, site_id)
            _mark_failure(site, str(e), settings)
            session.add(CheckRun(site_id=site_id, kind="crawl", trigger=trigger, result="failed",
                                 reason=str(e), started_at=started, finished_at=utcnow()))
            session.commit()
        return "failed"

    with SessionLocal() as session:
        site = session.get(Site, site_id)
        changed, summary = save_crawl(session, site, result, settings)
        _mark_success(site, settings)
        session.add(CheckRun(site_id=site_id, kind="crawl", trigger=trigger,
                             result="changed" if changed else "unchanged",
                             reason=summary or reason, pages_fetched=len(result.pages),
                             started_at=started, finished_at=utcnow()))
        session.commit()
    log.info("Re-crawled %s (%s): %s", url, trigger, summary or "no change in llms.txt")
    return "changed" if changed else "unchanged"


# ---------- scheduler ----------

async def tick(settings: Settings | None = None) -> None:
    """Process sites whose next check is due."""
    settings = settings or get_settings()
    with SessionLocal() as session:
        due = session.scalars(
            select(Site.id)
            .where(Site.status != "paused", Site.next_check_at <= utcnow())
            .order_by(Site.next_check_at)
            .limit(settings.monitor_sites_per_tick)
        ).all()
    for site_id in due:
        try:
            outcome = await process_site(site_id, settings=settings)
            log.info("Checked site %s: %s", site_id, outcome)
        except Exception:
            log.exception("Monitoring site %s failed", site_id)


def start_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(
        tick, "interval", minutes=settings.monitor_tick_minutes, max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    scheduler.start()
    return scheduler
