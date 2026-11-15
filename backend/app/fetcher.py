"""Downloading pages: one GET with a size cap and friendly error codes instead of exceptions."""

from dataclasses import dataclass

import httpx

from .config import Settings

HTML_TYPES = ("text/html", "application/xhtml+xml")


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int  # HTTP status, or 0 if the request failed
    headers: httpx.Headers
    body: bytes = b""
    error: str | None = None  # "timeout", "network", "redirects" or "too_large"

    @property
    def content_type(self) -> str:
        return content_type(self.headers)

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
        """An error that may go away on retry (as opposed to a page that is really gone)."""
        return self.error in ("timeout", "network") or self.status >= 500 or self.status == 429


def content_type(headers: httpx.Headers) -> str:
    """'text/html; charset=utf-8' -> 'text/html'."""
    return headers.get("content-type", "").split(";")[0].strip().lower()


def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=settings.crawl_max_redirects,
        timeout=settings.crawl_request_timeout_seconds,
        headers={"User-Agent": settings.crawl_user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
    )


async def fetch(client: httpx.AsyncClient, url: str, timeout: float, max_bytes: int) -> FetchResult:
    """GET a URL. Only downloads the body for HTML, XML, text and gzip responses, up to max_bytes."""
    try:
        async with client.stream("GET", url, timeout=timeout) as r:
            result = FetchResult(str(r.url), r.status_code, r.headers)
            if r.status_code != 200:
                return result
            if int(r.headers.get("content-length") or 0) > max_bytes:
                result.error = "too_large"
                return result
            if not _worth_reading(result.content_type):
                return result  # an image, PDF, etc.
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


def _worth_reading(ctype: str) -> bool:
    return not ctype or ctype in HTML_TYPES or "xml" in ctype or ctype.startswith("text/") or "gzip" in ctype
