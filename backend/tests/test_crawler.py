import asyncio

import pytest

from app import config
from app.crawler import CrawlError, crawl
from app.sitemaps import parse_sitemap
from tests.conftest import FakeSite, html

HOME = html(
    "Acme",
    "Acme makes APIs.",
    body="""
    <nav><a href="/docs">Docs</a><a href="/pricing">Pricing</a><a href="/login">Log in</a></nav>
    <main><a href="/blog/hello?utm_source=x">Hello</a><a href="https://other.com/x">Other</a>
          <a href="/files/report.pdf">PDF</a></main>
    <footer><a href="/privacy">Privacy</a></footer>
    """,
    head='<meta property="og:site_name" content="Acme">',
)

SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acme.test/docs/quickstart</loc><priority>0.9</priority></url>
  <url><loc>https://acme.test/secret/page</loc></url>
</urlset>"""


def site() -> FakeSite:
    return FakeSite({
        "/": HOME,
        "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /secret\nSitemap: https://acme.test/sitemap.xml\n"),
        "/sitemap.xml": (200, "application/xml", SITEMAP),
        "/docs": html("Docs | Acme", "All docs.", body='<a href="/docs/api">API</a>'),
        "/docs/quickstart": html("Quickstart | Acme", "Install it."),
        "/docs/api": html("API | Acme", "Endpoints.", body='<a href="/docs/api/deep">Deep</a>'),
        "/docs/api/deep": html("Deep | Acme", "Deep page.", body='<a href="/docs/api/deep/deeper">Deeper</a>'),
        "/docs/api/deep/deeper": html("Deeper", "Too deep."),
        "/pricing": html("Pricing | Acme", "Plans."),
        "/blog/hello": html("Hello", "First post.", head='<meta name="robots" content="noindex">'),
        "/privacy": html("Privacy", "Data."),
        "/secret/page": html("Secret", "Hidden."),
    })


def run(fake: FakeSite, url="https://acme.test/"):
    async def go():
        async with fake.client() as client:
            return await crawl(url, client)
    return asyncio.run(go())


def test_crawl_finds_pages_and_respects_rules():
    fake = site()
    result = run(fake)
    found = {p.url for p in result.pages}

    assert result.site_name == "Acme"
    assert result.summary == "Acme makes APIs."
    assert {"https://acme.test/", "https://acme.test/docs", "https://acme.test/docs/quickstart",
            "https://acme.test/pricing", "https://acme.test/privacy"} <= found
    assert "https://acme.test/secret/page" not in found  # robots.txt
    assert "https://acme.test/blog/hello" not in found  # noindex
    requested = {r.url.path for r in fake.requests}
    assert "/login" not in requested and "/files/report.pdf" not in requested
    assert result.stop_reason == "queue_empty"
    assert [s.url for s in result.sitemaps] == ["https://acme.test/sitemap.xml"]
    assert sorted(result.nav_urls) == ["https://acme.test/docs", "https://acme.test/pricing"]  # /login skipped
    assert result.sitemap_url_count == 1  # /secret/page is blocked by robots.txt


def test_titles_have_site_name_stripped():
    pages = {p.url: p for p in run(site()).pages}
    assert pages["https://acme.test/pricing"].title == "Pricing"


def test_depth_limit(monkeypatch):
    monkeypatch.setattr(config, "CRAWL_MAX_DEPTH", 2)
    found = {p.url for p in run(site()).pages}
    assert "https://acme.test/docs/api" in found
    assert "https://acme.test/docs/api/deep" not in found


def test_max_pages_keeps_highest_scoring(monkeypatch):
    monkeypatch.setattr(config, "CRAWL_MAX_PAGES", 3)
    result = run(site())
    assert result.stop_reason == "max_pages"
    found = {p.url for p in result.pages}
    # Homepage, then nav links (Docs, Pricing) beat body, footer and sitemap links.
    assert found == {"https://acme.test/", "https://acme.test/docs", "https://acme.test/pricing"}


def test_scope_limits_to_subpath():
    found = {p.url for p in run(site(), url="https://acme.test/docs").pages}
    assert found and all(u.startswith("https://acme.test/docs") for u in found)


def test_homepage_failure_raises():
    fake = FakeSite({"/": (500, "text/html", "boom")})
    with pytest.raises(CrawlError, match="HTTP 500"):
        run(fake)


def test_robots_disallowing_start_raises():
    fake = FakeSite({"/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"), "/": HOME})
    with pytest.raises(CrawlError, match="robots.txt"):
        run(fake)


def test_unknown_domain_has_a_clear_error(monkeypatch):
    monkeypatch.setattr(config, "CRAWL_ALLOW_PRIVATE_HOSTS", False)
    with pytest.raises(CrawlError, match="Couldn't find"):
        run(site(), url="https://no-such-host.invalid/")


def test_403_pages_do_not_stop_the_crawl():
    fake = site()
    for i in range(8):
        fake.routes[f"/members/{i}"] = (403, "text/html", "login required")
    fake.routes["/"] = HOME.replace("</main>", "".join(f'<a href="/members/{i}">m</a>' for i in range(8)) + "</main>")
    result = run(fake)
    assert result.stop_reason == "queue_empty"
    assert "https://acme.test/pricing" in {p.url for p in result.pages}


def test_private_hosts_blocked_by_default(monkeypatch):
    monkeypatch.setattr(config, "CRAWL_ALLOW_PRIVATE_HOSTS", False)
    with pytest.raises(CrawlError, match="public address"):
        run(site(), url="http://localhost/")


def test_parse_sitemap_index():
    body = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>"""
    assert parse_sitemap(body, 10_000) == ([], ["https://a.com/s1.xml"])
    assert parse_sitemap(b"not xml", 10_000) is None

