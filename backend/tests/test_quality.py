from app.crawler import CrawlResult, Page
from app.generate import render
from app.quality import assess


def page(path, desc="A page.", md=False, section=None):
    segments = [s for s in path.split("/") if s]
    return Page(url=f"https://acme.test{path}", title=path.strip("/").title() or "Home", description=desc,
                section=section if section is not None else (segments[0] if len(segments) >= 2 else ""),
                score=10, md_url=f"https://acme.test{path}.md" if md else None)


def result(pages, nav=(), stop="queue_empty", sitemap_count=0):
    return CrawlResult(
        start_url="https://acme.test/", homepage_url="https://acme.test/", scope_path="/",
        site_name="Acme", summary="Acme makes APIs.", pages=[page("/")] + pages, sitemaps=[],
        homepage_etag=None, homepage_last_modified=None, failed={}, stop_reason=stop,
        duration_seconds=1.0, nav_urls=list(nav), sitemap_url_count=sitemap_count,
    )


def report(res, problems=None):
    text = render(res)
    return assess(res, text, problems or [])


def checks(rep):
    return {c.name: c for c in rep.checks}


def test_good_file_scores_high():
    res = result([page("/docs/a", "Doc A.", md=True), page("/docs/b", "Doc B.", md=True), page("/pricing", "Plans.")],
                 nav=["https://acme.test/pricing"])
    rep = report(res)
    assert rep.score == 100
    assert all(c.status == "ok" for c in rep.checks)
    assert checks(rep)["Key pages"].message == "1 of 1 homepage menu pages included"


def test_missing_descriptions_and_markdown_lower_the_score():
    res = result([page("/docs/a", None), page("/docs/b", None), page("/pricing", "Plans.")])
    rep = report(res)
    assert checks(rep)["Descriptions"].status == "warn"
    assert checks(rep)["Descriptions"].message == "1 of 3 links have a description"
    assert checks(rep)["Markdown links"].status == "warn"
    assert rep.score == 100 - 20 - 5


def test_spec_problems_and_missing_nav_pages():
    res = result([page("/docs/a"), page("/docs/b")], nav=["https://acme.test/docs/a", "https://acme.test/about"])
    rep = report(res, problems=["The first line must be an H1 (# Name)."])
    assert checks(rep)["Spec"].status == "warn"
    assert checks(rep)["Key pages"].message == "1 of 2 homepage menu pages included"


def test_boilerplate_and_partial_crawl():
    res = result([page(f"/docs/p{i}", "Same text.") for i in range(3)], stop="time")
    rep = report(res)
    assert checks(rep)["Unique descriptions"].status == "warn"
    assert checks(rep)["Coverage"].status == "warn"


def test_large_menus_are_not_penalized():
    nav = [f"https://acme.test/docs/p{i}" for i in range(100)]
    res = result([page("/docs/p0"), page("/docs/p1")], nav=nav)
    check = checks(report(res))["Key pages"]
    assert check.status == "ok" and "large menu" in check.message


def test_large_site_coverage_note():
    res = result([page("/docs/a"), page("/docs/b")], stop="max_pages", sitemap_count=2400)
    assert checks(report(res))["Coverage"].message == "Top 3 pages of ~2,400 in the sitemap"
