from app import config
from app.llmstxt import Entry, build, validate


def entry(path, title, desc=None, score=10.0, section=None, md=None):
    url = f"https://acme.com{path}"
    segments = [s for s in path.split("/") if s]
    if section is None:
        section = segments[0] if len(segments) >= 2 else ""
    return Entry(url=url, title=title, description=desc, section=section, score=score, md_url=md)


def test_build_matches_spec_layout():
    pages = [
        entry("/", "Acme", "Home"),
        entry("/pricing", "Pricing", "Plans for teams.", score=40),
        entry("/about", "About", "The team.", score=30),
        entry("/docs", "Docs", "All the docs.", score=35),
        entry("/docs/quickstart", "Quickstart", "Install it.", score=50, md="https://acme.com/docs/quickstart.md"),
        entry("/docs/api", "API Reference", "Endpoints.", score=45),
        entry("/privacy", "Privacy Policy", "How we use data.", score=5),
    ]
    text = build("Acme", "Acme makes APIs.", pages, homepage_url="https://acme.com/")
    assert text == (
        "# Acme\n"
        "\n"
        "> Acme makes APIs.\n"
        "\n"
        "## Main\n"
        "\n"
        "- [Pricing](https://acme.com/pricing): Plans for teams.\n"
        "- [Docs](https://acme.com/docs): All the docs.\n"
        "- [About](https://acme.com/about): The team.\n"
        "\n"
        "## Docs\n"
        "\n"
        "- [Quickstart](https://acme.com/docs/quickstart.md): Install it.\n"
        "- [API Reference](https://acme.com/docs/api): Endpoints.\n"
        "\n"
        "## Optional\n"
        "\n"
        "- [Privacy Policy](https://acme.com/privacy): How we use data.\n"
    )
    assert validate(text) == []


def test_boilerplate_descriptions_are_dropped():
    shared = "The best site ever."
    pages = [entry(f"/docs/p{i}", f"P{i}", shared) for i in range(3)]
    text = build("Acme", None, pages)
    assert shared not in text
    assert "- [P0](https://acme.com/docs/p0)\n" in text


def test_escaping_and_truncation(monkeypatch):
    monkeypatch.setattr(config, "DESCRIPTION_MAX_CHARS", 30)
    pages = [entry("/a b", "Use [brackets]", "word " * 100)]
    line = build("Acme", None, pages).splitlines()[-1]
    assert line.startswith(r"- [Use \[brackets\]](https://acme.com/a%20b): word")
    assert line.endswith("…")
    assert len(line.split(": ", 1)[1]) <= 31


def test_section_names():
    pages = [entry("/getting-started/a", "C"), entry("/user_guide/b", "D")]
    text = build("Acme", None, pages)
    assert "## Getting Started" in text and "## User Guide" in text


def test_optional_pages_go_last():
    pages = [entry("/legal/privacy.html", "Privacy", score=99), entry("/docs/a", "A", score=1)]
    text = build("Acme", None, pages)
    assert text.index("## Docs") < text.index("## Optional")
    assert validate(text) == []


def test_validate_catches_problems():
    assert validate("no heading") == ["The first line must be an H1 (# Name)."]
    problems = validate("# A\n\n## S\n\nnot a link\n\n### Deep\n\n## Optional\n\n- [x](http://x)\n\n## Late\n\n- [y](http://y)\n")
    assert any("list items" in p for p in problems)
    assert any("H3" in p for p in problems)
    assert "The Optional section must be last." in problems
    assert any("'S' has no links" in p for p in problems)


def test_validate_accepts_details_and_bom():
    text = "﻿# A\n\n> Summary\n\nSome details.\n\n- a bullet\n\n## Docs\n\n- [x](https://x.com): note\n"
    assert validate(text) == []
