from fastapi.testclient import TestClient

from app import main
from app.crawler import CrawlError, CrawlResult, Page
from app.db import init_db
from app.generate import Generated


def fake_result() -> CrawlResult:
    return CrawlResult(
        start_url="https://acme.test/", homepage_url="https://acme.test/", scope_path="/",
        site_name="Acme", summary="Acme makes APIs.",
        pages=[Page(url="https://acme.test/docs/a", title="A", description="Doc A.", section="docs", score=10),
               Page(url="https://acme.test/docs/b", title="B", description="Doc B.", section="docs", score=9)],
        sitemaps=[], homepage_etag='"h"', homepage_last_modified=None, failed={},
        stop_reason="queue_empty", duration_seconds=1.0,
    )


def client(monkeypatch, fail=False):
    async def fake_generate(url, settings):
        if fail:
            raise CrawlError("Couldn't load https://bad.test/: HTTP 500.")
        from app.generate import render
        result = fake_result()
        return Generated(llms_txt=render(result, settings=settings), crawl=result, problems=[])

    monkeypatch.setattr(main, "generate", fake_generate)
    monkeypatch.setattr(main, "init_db", lambda url: init_db("sqlite://"))
    monkeypatch.setattr(main.get_settings(), "monitor_enabled", False)
    return TestClient(main.app)


def test_generate_without_monitoring(monkeypatch):
    with client(monkeypatch) as c:
        r = c.post("/api/generate", json={"url": "acme.test"})
    assert r.status_code == 200
    body = r.json()
    assert body["llms_txt"].startswith("# Acme\n\n> Acme makes APIs.")
    assert body["site_id"] is None and body["stats"]["pages"] == 2


def test_generate_with_monitoring_gives_stable_link(monkeypatch):
    with client(monkeypatch) as c:
        body = c.post("/api/generate", json={"url": "acme.test", "monitor": True}).json()
        assert body["stable_url"].endswith(f"/s/{body['site_id']}/llms.txt")

        txt = c.get(f"/s/{body['site_id']}/llms.txt")
        assert txt.status_code == 200 and txt.text == body["llms_txt"]
        assert txt.headers["content-type"].startswith("text/plain")


def test_crawl_error_returns_422(monkeypatch):
    with client(monkeypatch, fail=True) as c:
        r = c.post("/api/generate", json={"url": "bad.test"})
    assert r.status_code == 422
    assert "HTTP 500" in r.json()["detail"]


def test_unknown_site_404(monkeypatch):
    with client(monkeypatch) as c:
        assert c.get("/s/nope/llms.txt").status_code == 404
