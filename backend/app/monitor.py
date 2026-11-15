"""Keeps monitored sites up to date. Design: docs/monitoring.md.

Every few minutes the scheduler picks the sites that are due (about once a day each) and:
1. Asks the site whether anything changed, using cheap conditional requests
   (If-None-Match / If-Modified-Since) for the homepage, sitemaps and pages.
2. If something changed, re-crawls the site.
3. Saves a new version only if the generated llms.txt is different.
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
from .crawler import CrawlError, CrawlResult, Page, crawl
from .fetcher import make_client
from .db import PageRow, SessionLocal, Site, SiteVersion, utcnow
from .generate import render

log = logging.getLogger(__name__)


# ---------- storing crawls ----------

def fingerprint(page) -> str:
    """Identifies a page's llms.txt entry, for change summaries. Works on a Page or a PageRow."""
    raw = "\x1f".join([page.url, page.title or "", page.description or "", page.md_url or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _page_from_row(row: PageRow) -> Page:
    return Page(url=row.url, title=row.title, description=row.description, section=row.section,
                score=row.score, md_url=row.md_url, etag=row.etag, last_modified=row.last_modified,
                alt_descriptions=list(row.alt_descriptions or []))


def change_summary(old: dict[str, str], new: dict[str, str]) -> str:
    """Describe the difference between two {url: fingerprint} maps, e.g. "+2 pages, 1 page updated"."""
    def pages(n: int) -> str:
        return f"{n} page" if n == 1 else f"{n} pages"

    added = len(new.keys() - old.keys())
    removed = len(old.keys() - new.keys())
    updated = sum(1 for url in new.keys() & old.keys() if new[url] != old[url])
    parts = []
    if added:
        parts.append(f"+{pages(added)}")
    if removed:
        parts.append(f"−{pages(removed)}")
    if updated:
        parts.append(f"{pages(updated)} updated")
    return ", ".join(parts) or "Site details or ordering changed"


def save_crawl(session: Session, site: Site, result: CrawlResult, settings: Settings) -> tuple[bool, str | None]:
    """Save a crawl's results; add a new version if the llms.txt changed. Returns (changed, summary)."""
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

    # Site details and validators for the next check.
    site.name = result.site_name
    site.summary = result.summary
    site.homepage_url = result.homepage_url
    site.homepage_etag = result.homepage_etag
    site.homepage_last_modified = result.homepage_last_modified
    site.scope_path = result.scope_path
    site.last_crawled_at = now
    site.sitemaps = [{"url": s.url, "etag": s.etag, "last_modified": s.last_modified} for s in result.sitemaps]

    # Replace pages (delete first so the (site_id, url) unique constraint holds).
    for row in list(site.pages):
        session.delete(row)
    session.flush()
    session.expire(site, ["pages"])
    for p in pages:
        session.add(PageRow(
            site_id=site.id, url=p.url, title=p.title, description=p.description,
            alt_descriptions=p.alt_descriptions, md_url=p.md_url, section=p.section, score=p.score,
            etag=p.etag, last_modified=p.last_modified,
        ))

    # New version, unless the file came out exactly the same.
    current = site.current_version
    if current is not None and current.content_hash == digest:
        return False, None

    if current is None:
        summary = f"Initial version ({len(pages)} pages)"
    else:
        summary = change_summary({u: fingerprint(r) for u, r in old_rows.items()}, {p.url: fingerprint(p) for p in pages})
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
    if site is None:
        site = Site(url=result.start_url)
        session.add(site)
        session.flush()
    save_crawl(session, site, result, settings)
    _mark_success(site, settings)
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
    targets += [_Target(s["url"], s.get("etag"), s.get("last_modified")) for s in site.sitemaps or []]
    targets += [_Target(p.url, p.etag, p.last_modified) for p in site.pages
                if p.url != site.homepage_url]
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


async def process_site(site_id: str, force: bool = False,
                       settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> str:
    """Check one site and re-crawl it if needed. Returns "unchanged", "changed", "failed" or "missing".

    force: skip the ETag check and always re-crawl.
    """
    settings = settings or get_settings()
    if client:
        return await _process(site_id, force, settings, client)
    async with make_client(settings) as client:
        return await _process(site_id, force, settings, client)


async def _process(site_id: str, force: bool, settings: Settings, client: httpx.AsyncClient) -> str:
    # 1. Read what we need, then close the session while doing network work.
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        if site is None:
            return "missing"
        url = site.url
        targets = _targets(site)
        ttl = settings.monitor_full_crawl_ttl_days
        full_crawl_due = ttl > 0 and (site.last_crawled_at is None
                                      or utcnow() - site.last_crawled_at >= timedelta(days=ttl))

    # 2. Decide whether to re-crawl.
    if force:
        reason = "forced"
    elif full_crawl_due:
        reason = f"full re-crawl every {ttl:g} days"
    else:
        outcome, reason = await etag_check(targets, settings, client)
        if outcome != "changed":
            with SessionLocal() as session:
                site = session.get(Site, site_id)
                if outcome == "unchanged":
                    _mark_success(site, settings)
                else:
                    _mark_failure(site, reason or "check failed", settings)
                session.commit()
            return outcome

    # 3. Re-crawl and save.
    try:
        result = await crawl(url, settings, client)
        if result.stop_reason == "errors":
            raise CrawlError("too many errors during the crawl")
    except CrawlError as e:
        with SessionLocal() as session:
            _mark_failure(session.get(Site, site_id), str(e), settings)
            session.commit()
        return "failed"

    with SessionLocal() as session:
        site = session.get(Site, site_id)
        changed, summary = save_crawl(session, site, result, settings)
        _mark_success(site, settings)
        session.commit()
    log.info("Re-crawled %s (%s): %s", url, reason, summary or "no change in llms.txt")
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
