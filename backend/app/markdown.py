"""Finding markdown versions of pages.

Some sites publish a clean markdown copy of each page (e.g. /docs/intro.md) for AI tools.
llms.txt links to those copies when they exist.
"""

import asyncio
import logging
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import urls
from .fetcher import content_type

log = logging.getLogger(__name__)

MARKDOWN_TYPES = ("text/markdown", "text/x-markdown", "text/plain")


def markdown_candidates(url: str) -> list[str]:
    """Conventional markdown URLs for a page, per the llms.txt spec.

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


async def find_markdown_versions(client: httpx.AsyncClient, pages: list, allowed: Callable[[str], bool],
                                 concurrency: int, request_timeout: float, total_timeout: float) -> None:
    """For pages without an md_url, try the conventional .md URLs and set md_url when one exists."""
    limit = asyncio.Semaphore(concurrency)

    async def probe(page) -> None:
        async with limit:
            for md_url in markdown_candidates(page.url):
                if not allowed(md_url):
                    continue
                try:
                    res = await client.head(md_url, timeout=request_timeout)
                except httpx.HTTPError:
                    return
                if res.status_code == 200 and content_type(res.headers) in MARKDOWN_TYPES:
                    page.md_url = urls.normalize(str(res.url))
                    return

    try:
        await asyncio.wait_for(asyncio.gather(*(probe(p) for p in pages if not p.md_url)), total_timeout)
    except asyncio.TimeoutError:
        log.info("Markdown probing timed out")
