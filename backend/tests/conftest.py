import httpx
import pytest

from app.config import Settings


def html(title: str, description: str = "", body: str = "", head: str = "") -> str:
    desc = f'<meta name="description" content="{description}">' if description else ""
    return f"<html><head><title>{title}</title>{desc}{head}</head><body>{body}</body></html>"


class FakeSite:
    """An in-memory website served through httpx.MockTransport."""

    def __init__(self, routes: dict[str, tuple[int, str, str] | str], headers: dict[str, dict] | None = None):
        # routes: path -> html string, or (status, content_type, body)
        self.routes = routes
        self.headers = headers or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        status, ctype, body = route if isinstance(route, tuple) else (200, "text/html; charset=utf-8", route)
        extra = self.headers.get(request.url.path, {})
        etag = extra.get("etag")
        if etag and request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers=extra)
        return httpx.Response(status, headers={"content-type": ctype, **extra}, text=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=True)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        crawl_allow_private_hosts=True,
        crawl_probe_markdown=False,
        monitor_enabled=False,
    )
