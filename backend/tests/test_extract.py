from app import extract

PAGE = """
<html><head>
  <title>Pricing | Acme</title>
  <meta name="description" content="  Plans for
     every team. ">
  <meta property="og:site_name" content="Acme">
  <link rel="canonical" href="/pricing">
  <link rel="alternate" type="text/markdown" href="/pricing.md">
</head><body>
  <header><nav><a href="/docs">Docs</a></nav></header>
  <main>
    <h1>Pricing</h1>
    <p>Short.</p>
    <p>This is the first paragraph that is long enough to be used as a description.</p>
    <a href="/blog/post">Post</a>
    <a href="#top">Top</a>
  </main>
  <footer><a href="/privacy">Privacy</a></footer>
</body></html>
"""


def test_parse_html_metadata():
    meta = extract.parse_html(PAGE)
    assert meta.title == "Pricing | Acme"
    assert meta.description == "Plans for every team."
    assert meta.site_name == "Acme"
    assert meta.canonical == "/pricing"
    assert meta.md_alternate == "/pricing.md"
    assert meta.h1 == "Pricing"
    assert meta.first_paragraph.startswith("This is the first paragraph")
    assert not meta.noindex


def test_links_are_classified_by_location():
    links = {link.url: link.source for link in extract.parse_html(PAGE).links}
    assert links == {"/docs": "nav", "/blog/post": "body", "/privacy": "footer"}


def test_noindex():
    meta = extract.parse_html('<html><head><meta name="robots" content="noindex, follow"></head></html>')
    assert meta.noindex


def test_description_candidates_skip_duplicates():
    meta = extract.PageMeta(description="A", og_description="A", first_paragraph="B")
    assert meta.descriptions == ["A", "B"]


def test_site_name_from_title():
    assert extract.site_name_from_title("Home | Acme") == "Acme"
    assert extract.site_name_from_title("Acme - Build APIs fast") == "Acme"
    assert extract.site_name_from_title("Acme") == "Acme"


def test_strip_site_name():
    assert extract.strip_site_name("Pricing | Acme", "Acme") == "Pricing"
    assert extract.strip_site_name("Acme — Docs: Intro", "Acme") == "Docs: Intro"
    assert extract.strip_site_name("Acme", "Acme") == "Acme"
    assert extract.strip_site_name("Pricing | Other", "Acme") == "Pricing | Other"


def test_parse_link_header():
    header = '</docs/page.md>; rel="alternate"; type="text/markdown", </docs/llms.txt>; rel="describedby"'
    assert extract.parse_link_header(header) == "/docs/page.md"
    assert extract.parse_link_header('</x>; rel="next"') is None
