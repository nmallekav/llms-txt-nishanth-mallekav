"""Best-first website crawler. Design: docs/crawler.md.

Discovers pages from robots.txt sitemaps, the homepage navigation and page links,
scores every link, and always fetches the highest-scoring one next until a page,
depth, time or error limit is hit.
"""

import asyncio
import heapq
import itertools
import logging
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import urls
from .config import Settings, get_settings
from .extract import PageMeta, clean_text, parse_html, parse_link_header, site_name_from_title, strip_site_name

log = logging.getLogger(__name__)

HOMEPAGE_SCORE = 1000.0
HTML_TYPES = ("text/html", "application/xhtml+xml")
MARKDOWN_TYPES = ("text/markdown", "text/x-markdown", "text/plain")


class CrawlError(Exception):
    """The site could not be crawled at all (bad URL, blocked, homepage failed)."""


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    headers: httpx.Headers
    body: bytes = b""
    error: str | None = None  # "timeout", "network", "redirects", "too_large"

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def charset(self) -> str | None:
        for part in self.headers.get("content-type", "").split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                return value.strip("\"' ")
        return None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200

    @property
    def is_html(self) -> bool:
        return self.content_type in HTML_TYPES

    @property
    def transient(self) -> bool:
        """Errors that may go away on retry (vs. a page that is really gone)."""
        return self.error in ("timeout", "network") or self.status >= 500 or self.status == 429


@dataclass
class Page:
    url: str
    title: str
    description: str | None
    section: str
    score: float
    depth: int
    md_url: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    status: int = 200
    # Fallback descriptions, used when `description` turns out to be site-wide boilerplate.
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
    failed: dict[str, str]  # url -> "transient" | "gone" | "http <status>"
    stop_reason: str  # "max_pages", "queue_empty", "time", "errors"
    duration_seconds: float

    @property
    def partial(self) -> bool:
        return self.stop_reason in ("time", "errors")


@dataclass
class _Candidate:
    url: str
    depth: int
    sources: set[str] = field(default_factory=set)
    inbound: int = 0
    sitemap_priority: float | None = None
    score: float = 0.0


def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=settings.crawl_max_redirects,
        timeout=settings.crawl_request_timeout_seconds,
        headers={"User-Agent": settings.crawl_user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
    )


async def crawl(start_url: str, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> CrawlResult:
    """Crawl a site and return its most important pages."""
    return await Crawler(start_url, settings, client).run()


class Crawler:
    def __init__(self, start_url: str, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.start_url = start_url
        self.cfg = settings or get_settings()
        self.client = client
        self.user_agent_token = self.cfg.crawl_user_agent.split("/")[0]

        self.scheme = "https"
        self.host = ""
        self.scope = "/"
        self.site_name = ""
        self.robots: RobotFileParser | None = None
        self.crawl_delay: float = 0

        self.heap: list[tuple[float, int, str]] = []
        self.counter = itertools.count()
        self.candidates: dict[str, _Candidate] = {}
        self.visited: set[str] = set()
        self.section_counts: Counter[str] = Counter()
        self.pages: dict[str, Page] = {}
        self.failed: dict[str, str] = {}
        self.sitemaps: list[SitemapInfo] = []
        self.deadline = 0.0

    # ---------- entry point ----------

    async def run(self) -> CrawlResult:
        start = urls.normalize(urls.ensure_scheme(self.start_url))
        if not start:
            raise CrawlError("Please enter a valid http(s) URL.")
        host = urlsplit(start).hostname or ""
        if not self.cfg.crawl_allow_private_hosts and not await asyncio.to_thread(urls.is_public_host, host):
            raise CrawlError(f"Can't reach {host}: it doesn't resolve to a public address.")

        owns_client = self.client is None
        if owns_client:
            self.client = make_client(self.cfg)
        try:
            return await self._run(start)
        finally:
            if owns_client:
                await self.client.aclose()

    async def _run(self, start: str) -> CrawlResult:
        started = time.monotonic()
        self.deadline = started + self.cfg.crawl_timeout_seconds

        start_parts = urlsplit(start)
        self.scheme, self.host = start_parts.scheme, start_parts.hostname or ""
        robots_sitemaps = await self._load_robots()
        if not self._allowed(start):
            raise CrawlError("This site's robots.txt doesn't allow crawling this URL.")

        home = await self._fetch(start)
        if not home.ok or not home.is_html:
            reason = home.error or (f"HTTP {home.status}" if home.status != 200 else f"not an HTML page ({home.content_type})")
            raise CrawlError(f"Couldn't load {start}: {reason}.")

        # Follow the homepage redirect: the site may live on another host or path.
        final = urls.normalize(home.url) or start
        final_parts = urlsplit(final)
        if urls.bare_host(final_parts.hostname or "") != urls.bare_host(self.host):
            self.host = final_parts.hostname or self.host
            robots_sitemaps = await self._load_robots()
        self.scheme, self.host = final_parts.scheme, final_parts.hostname or self.host
        self.scope = start_parts.path if start_parts.path != "/" else "/"
        if not urls.in_scope(final, self.scope):
            self.scope = final_parts.path or "/"

        meta = parse_html(home.body, home.charset)
        # og:site_name sometimes carries a tagline too ("Acme - Build fast"), so split it the same way.
        self.site_name = (site_name_from_title(meta.site_name) or site_name_from_title(meta.title)
                          or urls.bare_host(self.host))
        summary = meta.best_description
        self.visited.add(final)
        self.section_counts[urls.section_of(final, self.scope)] += 1
        self._add_page(_Candidate(final, 0, score=HOMEPAGE_SCORE), home, meta)
        self._enqueue_links(meta, final, depth=1, from_homepage=True)

        await self._load_sitemaps(robots_sitemaps)
        stop_reason = await self._crawl_loop()

        pages = list(self.pages.values())
        if self.cfg.crawl_probe_markdown:
            await self._find_markdown_versions(pages)

        return CrawlResult(
            start_url=start,
            homepage_url=final,
            scope_path=self.scope,
            site_name=self.site_name,
            summary=summary,
            pages=pages,
            sitemaps=self.sitemaps,
            homepage_etag=home.headers.get("etag"),
            homepage_last_modified=home.headers.get("last-modified"),
            failed=self.failed,
            stop_reason=stop_reason,
            duration_seconds=round(time.monotonic() - started, 2),
        )

    # ---------- main loop ----------

    async def _crawl_loop(self) -> str:
        consecutive_errors = 0
        concurrency = 1 if self.crawl_delay else self.cfg.crawl_concurrency
        while True:
            if len(self.pages) >= self.cfg.crawl_max_pages:
                return "max_pages"
            if time.monotonic() >= self.deadline:
                return "time"
            batch = self._pop_batch(min(concurrency, self.cfg.crawl_max_pages - len(self.pages)))
            if not batch:
                return "queue_empty"

            results = await asyncio.gather(*(self._fetch(c.url) for c in batch))
            for cand, res in zip(batch, results):
                if res.error or res.status >= 400:
                    self._record_failure(cand, res)
                    if res.transient or res.status == 403:
                        consecutive_errors += 1
                    continue
                consecutive_errors = 0
                self._handle_page(cand, res)

            if consecutive_errors >= self.cfg.crawl_max_consecutive_errors:
                log.warning("Stopping crawl of %s after %d consecutive errors", self.host, consecutive_errors)
                return "errors"
            if self.crawl_delay:
                await asyncio.sleep(self.crawl_delay)

    def _pop_batch(self, size: int) -> list[_Candidate]:
        """Pop up to `size` best candidates, re-scoring lazily (docs/crawler.md §5)."""
        batch: list[_Candidate] = []
        while self.heap and len(batch) < size:
            _, _, url = heapq.heappop(self.heap)
            if url in self.visited:
                continue
            cand = self.candidates[url]
            section = urls.section_of(url, self.scope)
            if self.section_counts[section] >= self.cfg.crawl_section_ceiling:
                continue
            score = self._score(cand)
            if self.heap and score < -self.heap[0][0]:
                heapq.heappush(self.heap, (-score, next(self.counter), url))
                continue
            cand.score = score
            self.visited.add(url)
            self.section_counts[section] += 1
            batch.append(cand)
        return batch

    def _handle_page(self, cand: _Candidate, res: FetchResult) -> None:
        section = urls.section_of(cand.url, self.scope)
        if not res.is_html:
            self.section_counts[section] -= 1
            return
        final = self._local_url(res.url)
        if not final or (final != cand.url and final in self.pages):
            self.section_counts[section] -= 1
            return
        self.visited.add(final)

        meta = parse_html(res.body, res.charset)
        url = final
        if meta.canonical:
            canonical = self._local_url(meta.canonical, base=final)
            if canonical and canonical != final:
                if canonical in self.pages:
                    self.section_counts[section] -= 1
                    return
                url = canonical
                self.visited.add(canonical)

        if not meta.noindex:
            cand.url = url
            self._add_page(cand, res, meta)
        if cand.depth + 1 <= self.cfg.crawl_max_depth:
            self._enqueue_links(meta, url, depth=cand.depth + 1)

    def _record_failure(self, cand: _Candidate, res: FetchResult) -> None:
        self.section_counts[urls.section_of(cand.url, self.scope)] -= 1
        if res.transient:
            self.failed[cand.url] = "transient"
        elif res.status in (404, 410):
            self.failed[cand.url] = "gone"
        else:
            self.failed[cand.url] = res.error or f"http {res.status}"

    def _add_page(self, cand: _Candidate, res: FetchResult, meta: PageMeta) -> None:
        title = meta.best_title
        if title and cand.depth > 0:
            title = strip_site_name(title, self.site_name)
        if not title:
            segments = urls.path_segments(cand.url)
            title = segments[-1].replace("-", " ").replace("_", " ").title() if segments else self.site_name

        md_url = meta.md_alternate or parse_link_header(res.headers.get("link"))
        if md_url:
            md_url = urls.normalize(md_url, base=cand.url)

        self.pages[cand.url] = Page(
            url=cand.url,
            title=clean_text(title) or self.site_name,
            description=meta.best_description,
            alt_descriptions=meta.descriptions[1:],
            section=urls.section_of(cand.url, self.scope),
            score=round(cand.score, 2),
            depth=cand.depth,
            md_url=md_url,
            etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified"),
            status=res.status,
        )

    # ---------- queue ----------

    def _enqueue_links(self, meta: PageMeta, base: str, depth: int, from_homepage: bool = False) -> None:
        seen_here: set[str] = set()
        for link in meta.links:
            url = self._local_url(link.url, base=base, crawlable=True)
            if not url or url in seen_here:
                continue
            seen_here.add(url)
            # The nav is the same on every page, so only the homepage's nav earns the nav bonus.
            source = link.source if (from_homepage or link.source != "nav") else None
            self._add_candidate(url, depth, source=source, inbound=True)

    def _add_candidate(self, url: str, depth: int, source: str | None = None,
                       inbound: bool = False, sitemap_priority: float | None = None) -> None:
        if url in self.visited:
            return
        cand = self.candidates.get(url)
        if cand is None:
            cand = self.candidates[url] = _Candidate(url, depth)
        else:
            cand.depth = min(cand.depth, depth)
        if source:
            cand.sources.add(source)
        if inbound:
            cand.inbound += 1
        if sitemap_priority is not None:
            cand.sitemap_priority = max(cand.sitemap_priority or 0, sitemap_priority)
        heapq.heappush(self.heap, (-self._score(cand), next(self.counter), url))

    def _score(self, c: _Candidate) -> float:
        cfg = self.cfg
        score = 0.0
        if "nav" in c.sources:
            score += cfg.score_nav
        if "sitemap" in c.sources:
            score += cfg.score_sitemap + (c.sitemap_priority or 0) * cfg.score_sitemap_priority
        if "body" in c.sources:
            score += cfg.score_body
        if "footer" in c.sources:
            score += cfg.score_footer
        score += cfg.score_inbound * max(0, c.inbound - 1)

        segments = [s.lower() for s in urls.path_segments(c.url, self.scope)]
        if any(s in cfg.score_keywords for s in segments):
            score += cfg.score_keyword
        score -= cfg.score_depth_penalty * len(segments)
        score -= cfg.score_section_penalty * self.section_counts[urls.section_of(c.url, self.scope)]
        return score

    # ---------- URL helpers ----------

    def _local_url(self, raw: str, base: str | None = None, crawlable: bool = False) -> str | None:
        """Normalize a URL onto this site's scheme/host; None if off-site or out of scope."""
        url = urls.normalize(raw, base)
        if not url or not urls.same_site(url, self.host):
            return None
        parts = urlsplit(url)
        netloc = self.host if not parts.port else f"{self.host}:{parts.port}"
        url = urlunsplit((self.scheme, netloc, parts.path, parts.query, ""))
        if not urls.in_scope(url, self.scope):
            return None
        if crawlable and (urls.skip_reason(url, self.scope) or not self._allowed(url)):
            return None
        return url

    def _allowed(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(self.user_agent_token, url)

    # ---------- robots.txt and sitemaps ----------

    async def _load_robots(self) -> list[str]:
        """Fetch robots.txt; returns the sitemap URLs it lists. Missing robots.txt allows everything."""
        self.robots = None
        self.crawl_delay = 0
        res = await self._fetch(f"{self.scheme}://{self.host}/robots.txt")
        if not res.ok:
            return []
        parser = RobotFileParser()
        parser.parse(res.body.decode(res.charset or "utf-8", errors="replace").splitlines())
        self.robots = parser
        self.crawl_delay = float(parser.crawl_delay(self.user_agent_token) or 0)
        return list(parser.site_maps() or [])

    async def _load_sitemaps(self, listed: list[str]) -> None:
        origin = f"{self.scheme}://{self.host}"
        queue = [u for u in (urls.normalize(s) for s in listed) if u and urls.same_site(u, self.host)]
        if not queue:
            queue = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
            if self.scope != "/":
                queue.insert(0, f"{origin}{self.scope}/sitemap.xml")

        fetched: set[str] = set()
        url_count = 0
        while queue and len(fetched) < self.cfg.crawl_max_sitemaps and time.monotonic() < self.deadline:
            sitemap_url = queue.pop(0)
            if sitemap_url in fetched:
                continue
            fetched.add(sitemap_url)
            res = await self._fetch(sitemap_url)
            if not res.ok:
                continue
            parsed = parse_sitemap(res.body, self.cfg.crawl_max_body_bytes * 5)
            if parsed is None:
                continue
            entries, children = parsed
            self.sitemaps.append(SitemapInfo(sitemap_url, res.headers.get("etag"), res.headers.get("last-modified")))
            queue.extend(c for c in children if urls.same_site(c, self.host))
            for loc, priority in entries:
                if url_count >= self.cfg.crawl_max_sitemap_urls:
                    return
                url = self._local_url(loc, crawlable=True)
                if url:
                    url_count += 1
                    self._add_candidate(url, depth=1, source="sitemap", sitemap_priority=priority)

    # ---------- markdown versions ----------

    async def _find_markdown_versions(self, pages: list[Page]) -> None:
        """For pages with no declared markdown version, try the spec's conventional .md URLs."""
        semaphore = asyncio.Semaphore(self.cfg.crawl_concurrency)

        async def probe(page: Page) -> None:
            async with semaphore:
                for candidate in markdown_candidates(page.url):
                    if not self._allowed(candidate):
                        continue
                    try:
                        res = await self.client.head(candidate, timeout=self.cfg.crawl_request_timeout_seconds)
                    except httpx.HTTPError:
                        return
                    content_type = res.headers.get("content-type", "").split(";")[0].strip().lower()
                    if res.status_code == 200 and content_type in MARKDOWN_TYPES:
                        page.md_url = urls.normalize(str(res.url))
                        return

        targets = [p for p in pages if not p.md_url]
        try:
            await asyncio.wait_for(asyncio.gather(*(probe(p) for p in targets)), self.cfg.crawl_probe_timeout_seconds)
        except asyncio.TimeoutError:
            log.info("Markdown probing for %s timed out", self.host)

    # ---------- HTTP ----------

    async def _fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return FetchResult(url, 0, httpx.Headers(), error="timeout")
        timeout = min(self.cfg.crawl_request_timeout_seconds, remaining)
        return await fetch(self.client, url, self.cfg.crawl_max_body_bytes, timeout, headers)


async def fetch(client: httpx.AsyncClient, url: str, max_bytes: int, timeout: float,
                headers: dict[str, str] | None = None) -> FetchResult:
    """GET a URL with a size cap. Only reads the body for HTML, XML, text and gzip responses."""
    try:
        async with client.stream("GET", url, headers=headers, timeout=timeout) as r:
            result = FetchResult(str(r.url), r.status_code, r.headers)
            if r.status_code != 200:
                return result
            if int(r.headers.get("content-length") or 0) > max_bytes:
                result.error = "too_large"
                return result
            ctype = result.content_type
            if not (ctype in HTML_TYPES or "xml" in ctype or ctype.startswith("text/") or "gzip" in ctype or not ctype):
                return result
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    result.error = "too_large"
                    return result
            result.body = bytes(body)
            return result
    except httpx.TooManyRedirects:
        return FetchResult(url, 0, httpx.Headers(), error="redirects")
    except httpx.TimeoutException:
        return FetchResult(url, 0, httpx.Headers(), error="timeout")
    except httpx.HTTPError:
        return FetchResult(url, 0, httpx.Headers(), error="network")


def parse_sitemap(body: bytes, max_decompressed: int) -> tuple[list[tuple[str, float | None]], list[str]] | None:
    """Parse a sitemap or sitemap index. Returns (page entries, child sitemap URLs), or None if invalid."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, max_decompressed)
        except zlib.error:
            return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    entries: list[tuple[str, float | None]] = []
    children: list[str] = []
    is_index = root.tag.split("}")[-1] == "sitemapindex"
    for item in root:
        loc = priority = None
        for child in item:
            tag = child.tag.split("}")[-1]
            if tag == "loc" and child.text:
                loc = child.text.strip()
            elif tag == "priority" and child.text:
                try:
                    priority = min(1.0, max(0.0, float(child.text)))
                except ValueError:
                    pass
        if not loc:
            continue
        if is_index:
            children.append(loc)
        else:
            entries.append((loc, priority))
    return entries, children


def markdown_candidates(url: str) -> list[str]:
    """Conventional markdown URLs per the spec.

    page.html -> page.html.md;  /docs -> /docs.md or /docs/index.html.md;  / -> /index.html.md or /index.md
    """
    parts = urlsplit(url)
    path = parts.path or "/"
    if path == "/":
        paths = ["/index.html.md", "/index.md"]
    elif "." in path.rsplit("/", 1)[-1]:
        paths = [path + ".md"]
    else:
        paths = [path + ".md", path + "/index.html.md"]
    return [urlunsplit((parts.scheme, parts.netloc, p, "", "")) for p in paths]
