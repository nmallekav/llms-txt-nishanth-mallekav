import asyncio

import pytest

from app.crawler import crawl
from app.db import SessionLocal, Site, init_db
from app.monitor import change_summary, process_site, store_site
from tests.conftest import FakeSite, html


@pytest.fixture(autouse=True)
def db():
    init_db("sqlite://")


def make_site() -> FakeSite:
    return FakeSite(
        {
            "/": html("Acme", "Acme makes APIs.", body='<nav><a href="/docs">Docs</a><a href="/pricing">Pricing</a></nav>'),
            "/docs": html("Docs", "All docs."),
            "/pricing": html("Pricing", "Plans."),
        },
        headers={"/": {"etag": '"h1"'}, "/docs": {"etag": '"d1"'}, "/pricing": {"etag": '"p1"'}},
    )


def start_monitoring(fake: FakeSite) -> str:
    async def go():
        async with fake.client() as client:
            return await crawl("https://acme.test/", client)
    result = asyncio.run(go())
    with SessionLocal() as session:
        return store_site(session, result).id


def check(site_id, fake, force=False):
    async def go():
        async with fake.client() as client:
            return await process_site(site_id, force=force, client=client)
    return asyncio.run(go())


def get_site(site_id) -> Site:
    with SessionLocal() as session:
        return session.get(Site, site_id)


def test_store_site_creates_first_version():
    site_id = start_monitoring(make_site())
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.current_version.change_summary == "Initial version (3 pages)"
        assert "- [Docs](https://acme.test/docs): All docs." in site.current_version.llms_txt
        assert {p.url for p in site.pages} == {"https://acme.test/", "https://acme.test/docs", "https://acme.test/pricing"}


def test_unchanged_etags_skip_crawl():
    fake = make_site()
    site_id = start_monitoring(fake)
    fake.requests.clear()

    assert check(site_id, fake) == "unchanged"
    assert all(r.headers.get("if-none-match") for r in fake.requests)  # only conditional GETs


def test_changed_etag_triggers_recrawl_and_new_version():
    fake = make_site()
    site_id = start_monitoring(fake)
    fake.routes["/pricing"] = html("Pricing", "New plans for everyone.")
    fake.headers["/pricing"] = {"etag": '"p2"'}

    assert check(site_id, fake) == "changed"
    with SessionLocal() as session:
        version = session.get(Site, site_id).current_version
        assert version.change_summary == "1 page updated"
        assert "New plans for everyone." in version.llms_txt


def test_changed_etag_with_same_output_keeps_version():
    fake = make_site()
    site_id = start_monitoring(fake)
    with SessionLocal() as session:
        first_version = session.get(Site, site_id).current_version_id
    fake.headers["/docs"] = {"etag": '"d2"'}  # bytes changed, title/description didn't

    assert check(site_id, fake) == "unchanged"
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.current_version_id == first_version
        assert next(p for p in site.pages if p.url.endswith("/docs")).etag == '"d2"'


def test_same_etag_on_200_counts_as_unchanged():
    """Some CDNs ignore If-None-Match and send 200 with the same ETag."""
    fake = make_site()
    site_id = start_monitoring(fake)
    original = fake.handler

    def ignore_conditional(request):
        request.headers.pop("if-none-match", None)
        return original(request)

    fake.handler = ignore_conditional
    assert check(site_id, fake) == "unchanged"


def test_missing_validators_count_as_changed():
    fake = make_site()
    fake.headers = {}
    site_id = start_monitoring(fake)
    crawled_at = get_site(site_id).last_crawled_at
    check(site_id, fake)
    assert get_site(site_id).last_crawled_at > crawled_at  # re-crawled


def test_failed_check_is_not_a_change():
    fake = make_site()
    site_id = start_monitoring(fake)
    fake.routes["/docs"] = (503, "text/html", "down")
    fake.headers["/docs"] = {}

    assert check(site_id, fake) == "failed"
    assert get_site(site_id).last_error == "HTTP 503: /docs"


def test_sitemaps_are_stored_and_checked():
    fake = make_site()
    fake.routes["/sitemap.xml"] = (200, "application/xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://acme.test/docs</loc></url></urlset>')
    fake.headers["/sitemap.xml"] = {"etag": '"s1"'}
    site_id = start_monitoring(fake)
    assert get_site(site_id).sitemaps == [{"url": "https://acme.test/sitemap.xml", "etag": '"s1"', "last_modified": None}]

    fake.headers["/sitemap.xml"] = {"etag": '"s2"'}
    fake.requests.clear()
    check(site_id, fake)
    assert any(not r.headers.get("if-none-match") for r in fake.requests)  # sitemap change triggered a crawl


def test_failed_crawl_keeps_version_and_schedules_retry():
    fake = make_site()
    site_id = start_monitoring(fake)
    fake.routes["/"] = (500, "text/html", "down")

    assert check(site_id, fake, force=True) == "failed"
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.fail_count == 1
        assert "HTTP 500" in site.last_error
        assert site.current_version.change_summary.startswith("Initial version")


def test_ttl_forces_full_crawl():
    fake = make_site()
    site_id = start_monitoring(fake)
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        site.last_crawled_at = site.last_crawled_at.replace(year=2000)
        session.commit()
    fake.requests.clear()
    check(site_id, fake)
    assert get_site(site_id).last_crawled_at.year != 2000
    assert not any(r.headers.get("if-none-match") for r in fake.requests)  # crawled without an ETag check


def test_change_summary():
    assert change_summary({"a": "1", "b": "1"}, {"a": "2", "c": "1", "d": "1"}) == "+2 pages, −1 page, 1 page updated"
    assert change_summary({"a": "1"}, {"a": "1"}) == "Site details or ordering changed"
