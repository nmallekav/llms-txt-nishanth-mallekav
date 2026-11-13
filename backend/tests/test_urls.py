import pytest

from app import urls


@pytest.mark.parametrize("raw, expected", [
    ("HTTPS://Example.COM/Docs/", "https://example.com/Docs"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com:443/a#section", "https://example.com/a"),
    ("https://example.com/a?utm_source=x&b=2&a=1&gclid=z", "https://example.com/a?a=1&b=2"),
    ("https://example.com//a//b", "https://example.com/a/b"),
    ("https://example.com/docs/index.html", "https://example.com/docs"),
    ("mailto:hi@example.com", None),
    ("javascript:void(0)", None),
])
def test_normalize(raw, expected):
    assert urls.normalize(raw) == expected


def test_normalize_resolves_relative():
    assert urls.normalize("../b", "https://example.com/docs/a/") == "https://example.com/docs/b"


def test_same_site_ignores_www():
    assert urls.same_site("https://www.example.com/a", "example.com")
    assert not urls.same_site("https://blog.example.com/a", "example.com")


def test_in_scope():
    assert urls.in_scope("https://x.com/docs/a", "/docs")
    assert urls.in_scope("https://x.com/docs", "/docs")
    assert not urls.in_scope("https://x.com/docsearch", "/docs")
    assert urls.in_scope("https://x.com/anything", "/")


@pytest.mark.parametrize("url, reason", [
    ("https://x.com/file.pdf", "file"),
    ("https://x.com/login", "utility"),
    ("https://x.com/blog/tag/python", "archive"),
    ("https://x.com/blog/page/3", "pagination"),
    ("https://x.com/blog?page=2", "pagination"),
    ("https://x.com/2024/05", "archive"),
    ("https://x.com/fr/docs", "language"),
    ("https://x.com/pt-br/docs", "language"),
    ("https://x.com/docs/getting-started", None),
    ("https://x.com/blog?page=1", None),
    ("https://x.com/2024/05/my-post", None),
])
def test_skip_reason(url, reason):
    assert urls.skip_reason(url) == reason


def test_language_allowed_when_scope_is_language():
    assert urls.skip_reason("https://x.com/fr/docs", "/fr") is None


def test_section_of():
    assert urls.section_of("https://x.com/about") == ""
    assert urls.section_of("https://x.com/docs/intro") == "docs"
    assert urls.section_of("https://x.com/docs/api/auth", "/docs") == "api"
    assert urls.section_of("https://x.com/2024/05/post") == "posts"
