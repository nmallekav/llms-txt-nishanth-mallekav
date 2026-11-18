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
        "- [About](https://acme.com/about): The team.\n"
        "\n"
        "## Docs\n"
        "\n"
        "- [Docs](https://acme.com/docs): All the docs.\n"
        "- [Quickstart](https://acme.com/docs/quickstart.md): Install it.\n"
        "- [API Reference](https://acme.com/docs/api): Endpoints.\n"
        "\n"
        "## Optional\n"
        "\n"
        "- [Privacy Policy](https://acme.com/privacy): How we use data.\n"
    )
    assert validate(text) == []


def test_single_page_sections_go_to_other():
    pages = [
        entry("/blog/a", "A", score=10), entry("/blog/b", "B", score=9),
        entry("/careers-team/x", "Lonely", score=20),
    ]
    text = build("Acme", None, pages)
    assert "## Blog" in text and "## Other\n\n- [Lonely]" in text
    assert validate(text) == []


def test_boilerplate_descriptions_are_dropped():
    shared = "The best site ever."
    pages = [entry(f"/docs/p{i}", f"P{i}", shared) for i in range(3)]
    text = build("Acme", None, pages)
    assert shared not in text
    assert "- [P0](https://acme.com/docs/p0)\n" in text


def test_escaping_and_truncation():
    pages = [entry("/a b", "Use [brackets]", "word " * 100)]
    line = build("Acme", None, pages, description_max_chars=30).splitlines()[-1]
    assert line.startswith(r"- [Use \[brackets\]](https://acme.com/a%20b): word")
    assert line.endswith("…")
    assert len(line.split(": ", 1)[1]) <= 31


def test_note_equal_to_title_is_dropped():
    text = build("Acme", None, [entry("/faq", "FAQ", "FAQ")])
    assert "- [FAQ](https://acme.com/faq)\n" in text


def test_section_names():
    pages = [entry("/apis/a", "A"), entry("/apis/b", "B"), entry("/getting-started/a", "C"), entry("/getting-started/b", "D")]
    text = build("Acme", None, pages)
    assert "## APIs" in text and "## Getting Started" in text


def test_reserved_section_names_are_renamed():
    pages = [entry("/optional/a", "A"), entry("/optional/b", "B")]
    assert "## Optional Pages" in build("Acme", None, pages)


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
