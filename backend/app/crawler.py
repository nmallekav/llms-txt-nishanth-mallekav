"""Best-first website crawler. Design: docs/crawler.md.

How a crawl works:
1. Read robots.txt and fetch the homepage.
2. Collect every link on the homepage and every URL in the sitemap, each with a score (scoring.py).
3. Repeatedly fetch the highest-scoring URLs, save each page and collect its links,
   until we have enough pages, run out of time or run out of URLs.
"""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import config, urls
from .extract import PageMeta, clean_text, parse_html, parse_link_header, site_name_from_title, strip_site_name
from .fetcher import FetchResult, fetch, make_client
from .scoring import Candidate, score
from .sitemaps import parse_sitemap

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
    md_url: str | None = None  # markdown version of the page, if the page links to one
    etag: str | None = None
    last_modified: str | None = None


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
    stop_reason: str  # "max_pages", "queue_empty" or "time"
    duration_seconds: float
    nav_urls: list[str] = field(default_factory=list)  # pages linked from the homepage menu
    sitemap_url_count: int = 0  # page URLs found in the sitemaps (a rough site size)

    @property
    def partial(self) -> bool:
        return self.stop_reason == "time"


async def crawl(start_url: str, client: httpx.AsyncClient | None = None) -> CrawlResult:
    """Crawl a site and return its most important pages."""
    start = urls.normalize(urls.ensure_scheme(start_url))
    if not start:
        raise CrawlError("Please enter a valid http(s) URL.")
    host = urlsplit(start).hostname or ""
    if not config.CRAWL_ALLOW_PRIVATE_HOSTS:
        addresses = await asyncio.to_thread(urls.resolve_host, host)
        if not addresses:
            raise CrawlError(f"Couldn't find {host}. Check the address and try again.")
        if not all(urls.is_public_ip(a) for a in addresses):
            raise CrawlError(f"Can't reach {host}: it doesn't resolve to a public address.")

    if client:
        return await Crawler(start, client).run()
    async with make_client() as client:
        return await Crawler(start, client).run()


class Crawler:
    def __init__(self, start: str, client: httpx.AsyncClient):
        self.start = start
        self.client = client
        self.user_agent = config.CRAWL_USER_AGENT.split("/")[0]
        self.deadline = 0.0

        # The site being crawled. Updated if the homepage redirects somewhere else.
        self.scheme = "https"
        self.host = ""
        self.scope = "/"  # only pages at or under this path are crawled
        self.site_name = ""
        self.robots = RobotFileParser()

        # URLs we found but haven't fetched yet, and URLs we already took.
        self.candidates: dict[str, Candidate] = {}
        self.visited: set[str] = set()
        self.section_counts: Counter[str] = Counter()  # pages saved per section

        # Results.
        self.pages: dict[str, Page] = {}
        self.sitemaps: list[SitemapInfo] = []
        self.sitemap_url_count = 0

    async def run(self) -> CrawlResult:
        started = time.monotonic()
        self.deadline = started + config.CRAWL_TIMEOUT_SECONDS

        # 1. robots.txt. No robots.txt (or an error) means everything is allowed.
        parts = urlsplit(self.start)
        self.scheme, self.host = parts.scheme, parts.hostname or ""
        res = await fetch(self.client, f"{self.scheme}://{self.host}/robots.txt")
        robots_lines = res.body.decode(res.charset or "utf-8", errors="replace").splitlines() if res.ok else []
        self.robots.parse(robots_lines)
        if not self.robots.can_fetch(self.user_agent, self.start):
            raise CrawlError("This site's robots.txt doesn't allow crawling this URL.")

        # 2. The homepage.
        home = await fetch(self.client, self.start)
        if not home.ok or not home.is_html:
            reason = home.error or (f"HTTP {home.status}" if home.status != 200 else f"not an HTML page ({home.content_type})")
            raise CrawlError(f"Couldn't load {self.start}: {reason}.")

        # If the homepage redirected, continue from where it ended up.
        homepage = urls.normalize(home.url) or self.start
        home_parts = urlsplit(homepage)
        self.scheme, self.host = home_parts.scheme, home_parts.hostname or self.host
        # Entering example.com/docs crawls only /docs. If /docs redirected elsewhere, use the new path.
        self.scope = parts.path or "/"
        if not urls.in_scope(homepage, self.scope):
            self.scope = home_parts.path or "/"

        # 3. Save the homepage, then collect its links and the sitemap URLs.
        meta = parse_html(home.body, home.charset)
        self.site_name = (site_name_from_title(meta.site_name) or site_name_from_title(meta.title)
                          or urls.bare_host(self.host))
        self.visited.add(homepage)
        self._save_page(Candidate(homepage, depth=0, score=HOMEPAGE_SCORE), home, meta)
        self._queue_links(meta, homepage, depth=1, from_homepage=True)
        nav_urls = [url for url, cand in self.candidates.items() if "nav" in cand.sources]
        await self._queue_sitemaps(self.robots.site_maps() or [])

        # 4. Crawl.     
        stop_reason = await self._crawl_loop()

        return CrawlResult(
            start_url=self.start,
            homepage_url=homepage,
            scope_path=self.scope,
            site_name=self.site_name,
            summary=meta.best_description,
            pages=list(self.pages.values()),
            sitemaps=self.sitemaps,
            homepage_etag=home.headers.get("etag"),
            homepage_last_modified=home.headers.get("last-modified"),
            stop_reason=stop_reason,
            duration_seconds=round(time.monotonic() - started, 2),
            nav_urls=nav_urls,
            sitemap_url_count=self.sitemap_url_count,
        )

    async def _crawl_loop(self) -> str:
        """Fetch the best URLs a few at a time until a limit is hit. Returns why it stopped."""
        while True:
            if len(self.pages) >= config.CRAWL_MAX_PAGES:
                return "max_pages"
            if time.monotonic() >= self.deadline:
                return "time"

            # Take the highest-scoring URLs we haven't fetched yet.
            size = min(config.CRAWL_CONCURRENCY, config.CRAWL_MAX_PAGES - len(self.pages))
            batch = sorted(self.candidates.values(), key=lambda c: c.score, reverse=True)[:size]
            if not batch:
                return "queue_empty"
            for cand in batch:
                del self.candidates[cand.url]
                self.visited.add(cand.url)

            results = await asyncio.gather(*(fetch(self.client, c.url) for c in batch))
            for cand, res in zip(batch, results, strict=True):
                if not res.ok or not res.is_html:
                    continue  # failed, or not a web page
                url = self._site_url(res.url)  # where it redirected to
                if not url or url in self.pages:
                    continue  # redirected off-site, or to a page we already have
                self.visited.add(url)

                meta = parse_html(res.body, res.charset)
                if not meta.noindex:
                    cand.url = url
                    self._save_page(cand, res, meta)
                if cand.depth + 1 <= config.CRAWL_MAX_DEPTH:
                    self._queue_links(meta, url, depth=cand.depth + 1)

    def _save_page(self, cand: Candidate, res: FetchResult, meta: PageMeta) -> None:
        # Title without the site name ("Pricing | Acme" -> "Pricing").
        title = meta.best_title
        if title and cand.depth > 0:
            title = strip_site_name(title, self.site_name)
        if not title:
            # No title at all: make one from the URL (/getting-started -> "Getting Started").
            segments = urls.path_segments(cand.url)
            title = segments[-1].replace("-", " ").replace("_", " ").title() if segments else self.site_name

        md_url = meta.md_alternate or parse_link_header(res.headers.get("link"))
        section = urls.section_of(cand.url, self.scope)
        self.section_counts[section] += 1
        self.pages[cand.url] = Page(
            url=cand.url,
            title=clean_text(title) or self.site_name,
            description=meta.best_description,
            section=section,
            score=round(cand.score, 2),
            md_url=urls.normalize(md_url, base=cand.url) if md_url else None,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified"),
        )

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
        """Remember a URL to fetch, or update what we know about it, and (re)score it."""
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
        cand.score = score(cand, self.scope, self.section_counts[urls.section_of(url, self.scope)])

    async def _queue_sitemaps(self, listed: list[str]) -> None:
        """Collect the page URLs from the site's sitemaps, following sitemap indexes."""
        to_fetch = [u for u in (urls.normalize(s) for s in listed) if u and urls.same_site(u, self.host)]
        if not to_fetch:
            to_fetch = [f"{self.scheme}://{self.host}/sitemap.xml"]  # robots.txt didn't list any

        fetched: set[str] = set()
        while to_fetch and len(fetched) < config.CRAWL_MAX_SITEMAPS and time.monotonic() < self.deadline:
            sitemap_url = to_fetch.pop(0)
            if sitemap_url in fetched:
                continue
            fetched.add(sitemap_url)
            res = await fetch(self.client, sitemap_url)
            parsed = parse_sitemap(res.body, config.CRAWL_MAX_BODY_BYTES * 5) if res.ok else None
            if parsed is None:
                continue
            pages, child_sitemaps = parsed
            self.sitemaps.append(SitemapInfo(sitemap_url, res.headers.get("etag"), res.headers.get("last-modified")))
            to_fetch.extend(c for c in child_sitemaps if urls.same_site(c, self.host))
            for loc, priority in pages:
                if self.sitemap_url_count >= config.CRAWL_MAX_SITEMAP_URLS:
                    return
                url = self._site_url(loc, crawlable=True)
                if url:
                    self.sitemap_url_count += 1
                    self._add_candidate(url, depth=1, source="sitemap", sitemap_priority=priority)

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
        if crawlable and (urls.skip_reason(url, self.scope) or not self.robots.can_fetch(self.user_agent, url)):
            return None
        return url
