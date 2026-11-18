"""Keeps monitored sites up to date. Design: docs/monitoring.md.

Every few minutes the scheduler picks the sites that are due (about once a day each) and:
1. Asks the site whether anything changed, using cheap conditional requests
   (If-None-Match / If-Modified-Since) for the homepage, sitemaps and pages.
2. If something changed, re-crawls the site.
3. Saves a new version only if the generated llms.txt is different.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .crawler import CrawlError, CrawlResult, crawl
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


def save_crawl(session: Session, site: Site, result: CrawlResult) -> tuple[bool, str | None]:
    """Save a crawl's results; add a new version if the llms.txt changed. Returns (changed, summary)."""
    old_rows = {row.url: row for row in site.pages}
    pages = result.pages
    text = render(result)
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
            md_url=p.md_url, section=p.section, score=p.score,
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


def store_site(session: Session, result: CrawlResult) -> Site:
    """Start monitoring a freshly crawled site (or refresh it if it's already stored)."""
    site = session.scalar(select(Site).where(Site.url == result.start_url))
    if site is None:
        site = Site(url=result.start_url)
        session.add(site)
        session.flush()
    save_crawl(session, site, result)
    _mark_success(site)
    session.commit()
    return site


# ---------- the ETag check ----------

@dataclass
class _Target:
    url: str
    etag: str | None
    last_modified: str | None


async def etag_check(targets: list[_Target], client: httpx.AsyncClient) -> tuple[str, str | None]:
    """Ask the server if each saved URL changed, without downloading it.

    Returns ("unchanged" | "changed" | "failed", reason). Stops at the first change or failure.
    """
    for t in targets:
        if t.etag:
            headers = {"If-None-Match": t.etag}
        elif t.last_modified:
            headers = {"If-Modified-Since": t.last_modified}
        else:
            return "changed", f"no ETag or Last-Modified: {t.url}"
        try:
            async with client.stream("GET", t.url, headers=headers) as r:  # headers only, no body
                status, etag = r.status_code, r.headers.get("etag")
        except httpx.HTTPError as e:
            return "failed", f"{type(e).__name__}: {t.url}"

        # 304 = not modified. A 200 with the same ETag also means unchanged (some CDNs ignore If-None-Match).
        if status == 304 or (status == 200 and t.etag and etag == t.etag):
            continue
        if status in (200, 404, 410):  # new content, or the page was removed
            return "changed", f"HTTP {status}: {t.url}"
        return "failed", f"HTTP {status}: {t.url}"
    return "unchanged", None


def _targets(site: Site) -> list[_Target]:
    targets = [_Target(site.homepage_url or site.url, site.homepage_etag, site.homepage_last_modified)]
    targets += [_Target(s["url"], s.get("etag"), s.get("last_modified")) for s in site.sitemaps or []]
    targets += [_Target(p.url, p.etag, p.last_modified) for p in site.pages
                if p.url != site.homepage_url]
    return targets


# ---------- processing one site ----------

def _mark_success(site: Site) -> None:
    now = utcnow()
    site.last_checked_at = now
    site.next_check_at = now + timedelta(hours=config.MONITOR_CHECK_INTERVAL_HOURS)
    site.fail_count = 0
    site.last_error = None
    if site.status == "failing":
        site.status = "active"


def _mark_failure(site: Site, error: str) -> None:
    now = utcnow()
    site.last_checked_at = now
    site.fail_count += 1
    site.last_error = error
    if site.fail_count >= config.MONITOR_MAX_RETRIES:
        site.status = "failing"
        site.next_check_at = now + timedelta(hours=config.MONITOR_CHECK_INTERVAL_HOURS)
    else:
        site.next_check_at = now + timedelta(hours=config.MONITOR_RETRY_HOURS)


async def process_site(site_id: str) -> str:
    """Check one site and re-crawl it if needed. Returns "unchanged", "changed", "failed" or "missing"."""
    # 1. Read what we need, then close the session while doing network work.
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        if site is None:
            return "missing"
        url = site.url
        targets = _targets(site)
        # Days since the last full crawl. A full crawl is forced once this reaches the TTL.
        days_since_crawl = (utcnow() - site.last_crawled_at).days
        full_crawl_due = days_since_crawl >= config.MONITOR_FULL_CRAWL_TTL_DAYS

    async with make_client() as client:
        # 2. Decide whether to re-crawl.
        if full_crawl_due:
            reason = f"last full crawl was {days_since_crawl} days ago"
        else:
            outcome, reason = await etag_check(targets, client)
            if outcome != "changed":
                with SessionLocal() as session:
                    site = session.get(Site, site_id)
                    if outcome == "unchanged":
                        _mark_success(site)
                    else:
                        _mark_failure(site, reason)
                    session.commit()
                return outcome

        # 3. Re-crawl.
        try:
            result = await crawl(url, client)
        except CrawlError as e:
            with SessionLocal() as session:
                _mark_failure(session.get(Site, site_id), str(e))
                session.commit()
            return "failed"

    # 4. Save.
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        changed, summary = save_crawl(session, site, result)
        _mark_success(site)
        session.commit()
    log.info("Re-crawled %s (%s): %s", url, reason, summary or "no change in llms.txt")
    return "changed" if changed else "unchanged"


# ---------- scheduler ----------

async def tick() -> None:
    """Process sites whose next check is due."""
    with SessionLocal() as session:
        due = session.scalars(
            select(Site.id)
            .where(Site.next_check_at <= utcnow())
            .order_by(Site.next_check_at)
            .limit(config.MONITOR_SITES_PER_TICK)
        ).all()
    for site_id in due:
        try:
            outcome = await process_site(site_id)
            log.info("Checked site %s: %s", site_id, outcome)
        except Exception:
            log.exception("Monitoring site %s failed", site_id)


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(
        tick, "interval", minutes=config.MONITOR_TICK_MINUTES, max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    scheduler.start()
    return scheduler
