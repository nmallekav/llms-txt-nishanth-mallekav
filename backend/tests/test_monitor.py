import asyncio

import pytest
from sqlalchemy import select

from app.crawler import crawl
from app.db import CheckRun, SessionLocal, Site, init_db
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


def start_monitoring(fake: FakeSite, settings) -> str:
    async def go():
        async with fake.client() as client:
            return await crawl("https://acme.test/", settings, client)
    result = asyncio.run(go())
    with SessionLocal() as session:
        return store_site(session, result, settings).id


def check(site_id, fake, settings, force=False):
    async def go():
        async with fake.client() as client:
            return await process_site(site_id, force=force, settings=settings, client=client)
    return asyncio.run(go())


def latest_runs(site_id):
    with SessionLocal() as session:
        return session.scalars(select(CheckRun).where(CheckRun.site_id == site_id).order_by(CheckRun.id)).all()


def test_store_site_creates_first_version(settings):
    site_id = start_monitoring(make_site(), settings)
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.current_version.change_summary == "Initial version (3 pages)"
        assert "- [Docs](https://acme.test/docs): All docs." in site.current_version.llms_txt
        assert {p.url for p in site.pages} == {"https://acme.test/", "https://acme.test/docs", "https://acme.test/pricing"}


def test_unchanged_etags_skip_crawl(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    fake.requests.clear()

    assert check(site_id, fake, settings) == "unchanged"
    assert all(r.headers.get("if-none-match") for r in fake.requests)  # only conditional GETs
    assert latest_runs(site_id)[-1].kind == "check"


def test_changed_etag_triggers_recrawl_and_new_version(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    fake.routes["/pricing"] = html("Pricing", "New plans for everyone.")
    fake.headers["/pricing"] = {"etag": '"p2"'}

    assert check(site_id, fake, settings) == "changed"
    runs = latest_runs(site_id)
    assert (runs[-2].kind, runs[-2].result, runs[-2].reason) == ("check", "changed", "etag changed: /pricing")
    assert (runs[-1].kind, runs[-1].trigger, runs[-1].reason) == ("crawl", "change_detected", "1 page updated")
    with SessionLocal() as session:
        assert "New plans for everyone." in session.get(Site, site_id).current_version.llms_txt


def test_changed_etag_with_same_output_keeps_version(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    with SessionLocal() as session:
        first_version = session.get(Site, site_id).current_version_id
    fake.headers["/docs"] = {"etag": '"d2"'}  # bytes changed, title/description didn't

    assert check(site_id, fake, settings) == "unchanged"
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.current_version_id == first_version
        assert next(p for p in site.pages if p.url.endswith("/docs")).etag == '"d2"'


def test_same_etag_on_200_counts_as_unchanged(settings):
    """Some CDNs ignore If-None-Match and send 200 with the same ETag."""
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    original = fake.handler

    def ignore_conditional(request):
        request.headers.pop("if-none-match", None)
        return original(request)

    fake.handler = ignore_conditional
    assert check(site_id, fake, settings) == "unchanged"


def test_missing_validators_count_as_changed(settings):
    fake = make_site()
    fake.headers = {}
    site_id = start_monitoring(fake, settings)
    check(site_id, fake, settings)
    assert latest_runs(site_id)[-2].reason.startswith("no ETag or Last-Modified")


def test_failed_check_is_not_a_change(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    fake.routes["/docs"] = (503, "text/html", "down")
    fake.headers["/docs"] = {}

    assert check(site_id, fake, settings) == "failed"
    assert latest_runs(site_id)[-1].reason == "HTTP 503: /docs"


def test_failed_crawl_keeps_version_and_schedules_retry(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    fake.routes["/"] = (500, "text/html", "down")

    assert check(site_id, fake, settings, force=True) == "failed"
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        assert site.fail_count == 1
        assert "HTTP 500" in site.last_error
        assert site.current_version.change_summary.startswith("Initial version")


def test_ttl_forces_full_crawl(settings):
    fake = make_site()
    site_id = start_monitoring(fake, settings)
    with SessionLocal() as session:
        site = session.get(Site, site_id)
        site.last_crawled_at = site.last_crawled_at.replace(year=2000)
        session.commit()
    check(site_id, fake, settings)
    assert latest_runs(site_id)[-1].trigger == "ttl"


def test_change_summary():
    assert change_summary({"a": "1", "b": "1"}, {"a": "2", "c": "1", "d": "1"}) == "+2 pages, −1 page, 1 page updated"
    assert change_summary({"a": "1"}, {"a": "1"}) == "Site details or ordering changed"
