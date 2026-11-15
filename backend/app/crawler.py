"""Best-first website crawler. Design: docs/crawler.md.

How a crawl works:
1. Read robots.txt and fetch the homepage.
2. Queue every link on the homepage and every URL in the sitemaps, each with a score (scoring.py).
3. Repeatedly fetch the highest-scoring URLs, save each page and queue its links,
   until we have enough pages, run out of time, hit too many errors or run out of URLs.
4. Look for markdown versions of the saved pages (markdown.py).
"""

import asyncio
import heapq
import itertools
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import urls
from .config import Settings, get_settings
from .extract import PageMeta, clean_text, parse_html, parse_link_header, site_name_from_title, strip_site_name
from .fetcher import FetchResult, fetch, make_client
from .markdown import find_markdown_versions
from .scoring import Candidate, score
from .sitemaps import parse_sitemap

log = logging.getLogger(__name__)

HOMEPAGE_SCORE = 1000.0


class CrawlError(Exception):
    """The site could not be crawled at all (bad URL, blocked, homepage failed)."""


@dataclass
class Page:
    url: str
    title: str
    description: str | None
    section: str
    score: float
    md_url: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    # Backup descriptions, used when `description` turns out to be site-wide boilerplate.
    alt_descriptions: list[str] = field(default_factory=list)


@dataclass
class SitemapInfo:
    url: str
    etag: str | None
    last_modified: str | None


@dataclass
class CrawlResult:
    start_url: str
    homepage_url: str
    scope_path: str
    site_name: str
    summary: str | None
    pages: list[Page]
    sitemaps: list[SitemapInfo]
    homepage_etag: str | None
    homepage_last_modified: str | None
    failed: dict[str, str]  # url -> "transient", "gone" or another reason
    stop_reason: str  # "max_pages", "queue_empty", "time" or "errors"
    duration_seconds: float

    @property
    def partial(self) -> bool:
        return self.stop_reason in ("time", "errors")


async def crawl(start_url: str, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> CrawlResult:
    """Crawl a site and return its most important pages."""
    settings = settings or get_settings()
    start = urls.normalize(urls.ensure_scheme(start_url))
    if not start:
        raise CrawlError("Please enter a valid http(s) URL.")
    host = urlsplit(start).hostname or ""
    if not settings.crawl_allow_private_hosts and not await asyncio.to_thread(urls.is_public_host, host):
        raise CrawlError(f"Can't reach {host}: it doesn't resolve to a public address.")

    if client:
        return await Crawler(start, settings, client).run()
    async with make_client(settings) as client:
        return await Crawler(start, settings, client).run()


class Crawler:
    def __init__(self, start: str, settings: Settings, client: httpx.AsyncClient):
        self.start = start
        self.cfg = settings
        self.client = client
        self.user_agent = settings.crawl_user_agent.split("/")[0]
        self.deadline = 0.0

        # The site being crawled. Updated if the homepage redirects somewhere else.
        self.scheme = "https"
        self.host = ""
        self.scope = "/"  # only pages at or under this path are crawled
        self.site_name = ""
        self.robots: RobotFileParser | None = None
        self.crawl_delay = 0.0  # seconds between requests, if robots.txt asks for it

        # The queue: a heap of (-score, tie-breaker, url), so the best URL comes out first.
        self.queue: list[tuple[float, int, str]] = []
        self.tie_breaker = itertools.count()
        self.candidates: dict[str, Candidate] = {}
        self.visited: set[str] = set()
        self.section_counts: Counter[str] = Counter()  # pages taken per section

        # Results.
        self.pages: dict[str, Page] = {}
        self.failed: dict[str, str] = {}
        self.sitemaps: list[SitemapInfo] = []

    async def run(self) -> CrawlResult:
        started = time.monotonic()
        self.deadline = started + self.cfg.crawl_timeout_seconds

        # 1. robots.txt and the homepage.
        parts = urlsplit(self.start)
        self.scheme, self.host = parts.scheme, parts.hostname or ""
        sitemap_urls = await self._load_robots()
        if not self._allowed(self.start):
            raise CrawlError("This site's robots.txt doesn't allow crawling this URL.")

        home = await self._fetch(self.start)
        if not home.ok or not home.is_html:
            reason = home.error or (f"HTTP {home.status}" if home.status != 200 else f"not an HTML page ({home.content_type})")
            raise CrawlError(f"Couldn't load {self.start}: {reason}.")
        homepage, sitemap_urls = await self._follow_redirect(home, sitemap_urls)

        # 2. Save the homepage, then queue its links and the sitemap URLs.
        meta = parse_html(home.body, home.charset)
        self.site_name = (site_name_from_title(meta.site_name) or site_name_from_title(meta.title)
                          or urls.bare_host(self.host))
        self.visited.add(homepage)
        self.section_counts[self._section(homepage)] += 1
        self._save_page(Candidate(homepage, depth=0, score=HOMEPAGE_SCORE), home, meta)
        self._queue_links(meta, homepage, depth=1, from_homepage=True)
        await self._queue_sitemaps(sitemap_urls)

        # 3. Crawl.
        stop_reason = await self._crawl_loop()

        # 4. Markdown versions.
        pages = list(self.pages.values())
        if self.cfg.crawl_probe_markdown:
            await find_markdown_versions(self.client, pages, self._allowed, self.cfg.crawl_concurrency,
                                         self.cfg.crawl_request_timeout_seconds, self.cfg.crawl_probe_timeout_seconds)

        return CrawlResult(
            start_url=self.start,
            homepage_url=homepage,
            scope_path=self.scope,
            site_name=self.site_name,
            summary=meta.best_description,
            pages=pages,
            sitemaps=self.sitemaps,
            homepage_etag=home.headers.get("etag"),
            homepage_last_modified=home.headers.get("last-modified"),
            failed=self.failed,
            stop_reason=stop_reason,
            duration_seconds=round(time.monotonic() - started, 2),
        )

    async def _follow_redirect(self, home: FetchResult, sitemap_urls: list[str]) -> tuple[str, list[str]]:
        """Switch to wherever the homepage redirected (another host or path). Returns the final homepage URL."""
        homepage = urls.normalize(home.url) or self.start
        parts = urlsplit(homepage)
        new_host = parts.hostname or self.host
        if urls.bare_host(new_host) != urls.bare_host(self.host):
            self.host = new_host
            sitemap_urls = await self._load_robots()  # a different site has its own robots.txt
        self.scheme, self.host = parts.scheme, new_host

        # Entering example.com/docs crawls only /docs. If /docs redirected elsewhere, use the new path.
        start_path = urlsplit(self.start).path
        self.scope = start_path if start_path != "/" else "/"
        if not urls.in_scope(homepage, self.scope):
            self.scope = parts.path or "/"
        return homepage, sitemap_urls

    # ---------- the crawl loop ----------

    async def _crawl_loop(self) -> str:
        """Fetch the best queued URLs in batches until a limit is hit. Returns why it stopped."""
        consecutive_errors = 0
        batch_size = 1 if self.crawl_delay else self.cfg.crawl_concurrency
        while True:
            if len(self.pages) >= self.cfg.crawl_max_pages:
                return "max_pages"
            if time.monotonic() >= self.deadline:
                return "time"
            batch = self._next_batch(min(batch_size, self.cfg.crawl_max_pages - len(self.pages)))
            if not batch:
                return "queue_empty"

            results = await asyncio.gather(*(self._fetch(c.url) for c in batch))
            for cand, res in zip(batch, results, strict=True):
                if res.error or res.status >= 400:
                    self._record_failure(cand, res)
                    if res.transient or res.status == 403:
                        consecutive_errors += 1
                    saved = False
                else:
                    consecutive_errors = 0
                    saved = self._handle_page(cand, res)
                if not saved:
                    # _next_batch counted this URL toward its section; give the slot back.
                    self.section_counts[self._section(cand.url)] -= 1

            if consecutive_errors >= self.cfg.crawl_max_consecutive_errors:
                log.warning("Stopping crawl of %s after %d consecutive errors", self.host, consecutive_errors)
                return "errors"
            if self.crawl_delay:
                await asyncio.sleep(self.crawl_delay)

    def _next_batch(self, size: int) -> list[Candidate]:
        """Take up to `size` of the best unvisited URLs off the queue.

        Scores drop as a section fills up, so a URL's score in the queue may be out of date.
        We re-score each URL when it comes out; if it's no longer the best, it goes back in.
        """
        batch: list[Candidate] = []
        while self.queue and len(batch) < size:
            _, _, url = heapq.heappop(self.queue)
            if url in self.visited:
                continue
            section = self._section(url)
            if self.section_counts[section] >= self.cfg.crawl_section_ceiling:
                continue  # we have enough pages from this section
            cand = self.candidates[url]
            current = self._score(cand)
            if self.queue and current < -self.queue[0][0]:
                self._push(url, current)
                continue
            cand.score = current
            self.visited.add(url)
            self.section_counts[section] += 1
            batch.append(cand)
        return batch

    def _handle_page(self, cand: Candidate, res: FetchResult) -> bool:
        """Save a fetched page and queue its links. Returns False if the page was skipped."""
        if not res.is_html:
            return False
        url = self._site_url(res.url)  # where it redirected to
        if not url or (url != cand.url and url in self.pages):
            return False
        self.visited.add(url)

        meta = parse_html(res.body, res.charset)
        canonical = self._site_url(meta.canonical, base=url) if meta.canonical else None
        if canonical and canonical != url:
            if canonical in self.pages:
                return False  # a duplicate of a page we already have
            url = canonical
            self.visited.add(url)

        if not meta.noindex:
            cand.url = url
            self._save_page(cand, res, meta)
        if cand.depth + 1 <= self.cfg.crawl_max_depth:
            self._queue_links(meta, url, depth=cand.depth + 1)
        return True

    def _record_failure(self, cand: Candidate, res: FetchResult) -> None:
        if res.transient:
            self.failed[cand.url] = "transient"
        elif res.status in (404, 410):
            self.failed[cand.url] = "gone"
        else:
            self.failed[cand.url] = res.error or f"http {res.status}"

    def _save_page(self, cand: Candidate, res: FetchResult, meta: PageMeta) -> None:
        md_url = meta.md_alternate or parse_link_header(res.headers.get("link"))
        self.pages[cand.url] = Page(
            url=cand.url,
            title=self._page_title(cand, meta),
            description=meta.best_description,
            alt_descriptions=meta.descriptions[1:],
            section=self._section(cand.url),
            score=round(cand.score, 2),
            md_url=urls.normalize(md_url, base=cand.url) if md_url else None,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified"),
        )

    def _page_title(self, cand: Candidate, meta: PageMeta) -> str:
        """The page's title without the site name ("Pricing | Acme" -> "Pricing")."""
        title = meta.best_title
        if title and cand.depth > 0:
            title = strip_site_name(title, self.site_name)
        if not title:
            # No title at all: make one from the URL (/getting-started -> "Getting Started").
            segments = urls.path_segments(cand.url)
            title = segments[-1].replace("-", " ").replace("_", " ").title() if segments else self.site_name
        return clean_text(title) or self.site_name

    # ---------- the queue ----------

    def _queue_links(self, meta: PageMeta, base: str, depth: int, from_homepage: bool = False) -> None:
        seen_on_this_page: set[str] = set()
        for link in meta.links:
            url = self._site_url(link.url, base=base, crawlable=True)
            if not url or url in seen_on_this_page:
                continue
            seen_on_this_page.add(url)
            # The nav is the same on every page, so only the homepage's nav earns the nav bonus.
            source = link.source if from_homepage or link.source != "nav" else None
            self._add_candidate(url, depth, source=source, inbound=True)

    def _add_candidate(self, url: str, depth: int, source: str | None = None,
                       inbound: bool = False, sitemap_priority: float | None = None) -> None:
        if url in self.visited:
            return
        cand = self.candidates.setdefault(url, Candidate(url, depth))
        cand.depth = min(cand.depth, depth)
        if source:
            cand.sources.add(source)
        if inbound:
            cand.inbound += 1
        if sitemap_priority is not None:
            cand.sitemap_priority = max(cand.sitemap_priority or 0, sitemap_priority)
        self._push(url, self._score(cand))

    def _push(self, url: str, value: float) -> None:
        heapq.heappush(self.queue, (-value, next(self.tie_breaker), url))

    def _score(self, cand: Candidate) -> float:
        return score(cand, self.scope, self.section_counts[self._section(cand.url)], self.cfg)

    # ---------- robots.txt and sitemaps ----------

    async def _load_robots(self) -> list[str]:
        """Fetch robots.txt and return the sitemap URLs it lists. No robots.txt means everything is allowed."""
        self.robots = None
        self.crawl_delay = 0.0
        res = await self._fetch(f"{self.scheme}://{self.host}/robots.txt")
        if not res.ok:
            return []
        self.robots = RobotFileParser()
        self.robots.parse(res.body.decode(res.charset or "utf-8", errors="replace").splitlines())
        self.crawl_delay = float(self.robots.crawl_delay(self.user_agent) or 0)
        return list(self.robots.site_maps() or [])

    async def _queue_sitemaps(self, listed: list[str]) -> None:
        """Queue the page URLs from the site's sitemaps, following sitemap indexes."""
        origin = f"{self.scheme}://{self.host}"
        to_fetch = [u for u in (urls.normalize(s) for s in listed) if u and urls.same_site(u, self.host)]
        if not to_fetch:
            # robots.txt didn't list any: try the usual locations.
            to_fetch = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
            if self.scope != "/":
                to_fetch.insert(0, f"{origin}{self.scope}/sitemap.xml")

        fetched: set[str] = set()
        queued = 0
        while to_fetch and len(fetched) < self.cfg.crawl_max_sitemaps and time.monotonic() < self.deadline:
            sitemap_url = to_fetch.pop(0)
            if sitemap_url in fetched:
                continue
            fetched.add(sitemap_url)
            res = await self._fetch(sitemap_url)
            parsed = parse_sitemap(res.body, self.cfg.crawl_max_body_bytes * 5) if res.ok else None
            if parsed is None:
                continue
            pages, child_sitemaps = parsed
            self.sitemaps.append(SitemapInfo(sitemap_url, res.headers.get("etag"), res.headers.get("last-modified")))
            to_fetch.extend(c for c in child_sitemaps if urls.same_site(c, self.host))
            for loc, priority in pages:
                if queued >= self.cfg.crawl_max_sitemap_urls:
                    return
                url = self._site_url(loc, crawlable=True)
                if url:
                    queued += 1
                    self._add_candidate(url, depth=1, source="sitemap", sitemap_priority=priority)

    # ---------- helpers ----------

    async def _fetch(self, url: str) -> FetchResult:
        """GET a URL, never running past the crawl's deadline."""
        timeout = min(self.cfg.crawl_request_timeout_seconds, self.deadline - time.monotonic())
        if timeout <= 0:
            return FetchResult(url, 0, httpx.Headers(), error="timeout")
        return await fetch(self.client, url, timeout, self.cfg.crawl_max_body_bytes)

    def _section(self, url: str) -> str:
        return urls.section_of(url, self.scope)

    def _site_url(self, raw: str, base: str | None = None, crawlable: bool = False) -> str | None:
        """Normalize a URL onto this site's scheme and host. None if it's off-site or out of scope.

        crawlable=True also rejects URLs we shouldn't fetch (files, login pages, robots.txt rules...).
        """
        url = urls.normalize(raw, base)
        if not url or not urls.same_site(url, self.host):
            return None
        parts = urlsplit(url)
        netloc = f"{self.host}:{parts.port}" if parts.port else self.host
        url = urlunsplit((self.scheme, netloc, parts.path, parts.query, ""))
        if not urls.in_scope(url, self.scope):
            return None
        if crawlable and (urls.skip_reason(url, self.scope) or not self._allowed(url)):
            return None
        return url

    def _allowed(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(self.user_agent, url)
