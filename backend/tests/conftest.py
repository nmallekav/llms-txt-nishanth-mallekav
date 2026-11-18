import httpx
import pytest

from app import config


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


@pytest.fixture(autouse=True)
def test_config(monkeypatch):
    """Settings for every test: in-memory database, local test sites allowed, no scheduler.

    A test can change another value with monkeypatch.setattr(config, "CRAWL_MAX_PAGES", 3).
    monkeypatch puts everything back after the test.
    """
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(config, "CRAWL_ALLOW_PRIVATE_HOSTS", True)
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
