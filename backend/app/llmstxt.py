"""Build and validate llms.txt files. Format rules: docs/llms-txt-format.md."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from . import config

# Pages whose URL contains one of these go in the "Optional" section (an LLM can skip them).
OPTIONAL_KEYWORDS = {
    "privacy", "privacy-policy", "terms", "terms-of-service", "terms-of-use", "tos",
    "cookies", "cookie-policy", "legal", "security", "accessibility", "careers", "jobs",
    "press", "changelog", "imprint", "disclaimer", "gdpr", "dmca", "status",
}
MAIN_SECTION = "Main"
OPTIONAL_SECTION = "Optional"
SUMMARY_MAX_CHARS = 300
# A description shared by this many pages is site-wide boilerplate, not a page description.
BOILERPLATE_MIN_PAGES = 3


@dataclass
class Entry:
    """One link in the file. Mirrors the fields of crawler.Page that the generator needs."""
    url: str
    title: str
    description: str | None
    section: str
    score: float
    md_url: str | None = None


def build(site_name: str, summary: str | None, pages: list, homepage_url: str | None = None) -> str:
    """Render an llms.txt file from crawled pages (anything with Entry's attributes)."""
    # The homepage is the "# title" and "> summary", so it isn't listed as a link.
    pages = _drop_boilerplate([p for p in pages if p.url != homepage_url])
    pages = sorted(pages, key=lambda p: (-p.score, p.url))  # best pages first

    # Group pages into sections. Because pages are sorted, sections are added best-first too.
    groups: dict[str, list] = defaultdict(list)
    for p in pages:
        if _is_optional(p.url):
            groups[OPTIONAL_SECTION].append(p)
        else:
            groups[_section_name(p.section)].append(p)
    # Main (top-level pages) first, Optional last (the spec requires it), the rest best-first.
    middle = [name for name in groups if name not in (MAIN_SECTION, OPTIONAL_SECTION)]
    order = [name for name in [MAIN_SECTION, *middle, OPTIONAL_SECTION] if name in groups]

    lines = [f"# {site_name}", ""]
    if summary:
        lines += [f"> {_truncate(summary, SUMMARY_MAX_CHARS)}", ""]
    for name in order:
        lines += [f"## {name}", ""] + [_format_entry(p) for p in groups[name]] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _section_name(section: str) -> str:
    """'' -> Main, 'getting-started' -> Getting Started."""
    return section.replace("-", " ").replace("_", " ").title() or MAIN_SECTION


def _is_optional(url: str) -> bool:
    # /legal/privacy.html -> ["legal", "privacy"]
    segments = [s.lower().rsplit(".", 1)[0] for s in urlsplit(url).path.split("/")]
    return any(s in OPTIONAL_KEYWORDS for s in segments)


def _drop_boilerplate(pages: list) -> list:
    """Remove descriptions repeated across many pages: a site-wide default, not a page description."""
    counts = Counter(p.description for p in pages if p.description)
    boilerplate = {d for d, n in counts.items() if n >= BOILERPLATE_MIN_PAGES}
    return [replace(p, description=None) if p.description in boilerplate else p for p in pages]


def _format_entry(p) -> str:
    """- [Title](url): description"""
    title = p.title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    url = (p.md_url or p.url).replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    if p.description:
        return f"- [{title}]({url}): {_truncate(p.description, config.DESCRIPTION_MAX_CHARS)}"
    return f"- [{title}]({url})"


def _truncate(text: str, max_chars: int) -> str:
    """Cut at a word boundary and add an ellipsis."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-–— ") + "…"


# ---------- validation ----------

H1_RE = re.compile(r"^# \S")
H2_RE = re.compile(r"^## \S")
DEEP_HEADING_RE = re.compile(r"^#{3,}\s")
ENTRY_RE = re.compile(r"^- \[(?:[^\]\\]|\\.)+\]\([^)\s]+\)(?:: .+)?$")


def validate(text: str) -> list[str]:
    """Check a file against the spec. Returns a list of problems (empty = valid)."""
    problems: list[str] = []
    lines = text.lstrip("﻿").split("\n")
    content = [(i + 1, line) for i, line in enumerate(lines) if line.strip()]
    if not content or not H1_RE.match(content[0][1]):
        return ["The first line must be an H1 (# Name)."]

    phase = "h1"  # h1 -> summary -> details -> sections
    sections: list[tuple[str, int]] = []  # (name, entry count)
    for lineno, line in content[1:]:
        if H1_RE.match(line):
            problems.append(f"Line {lineno}: only one H1 is allowed.")
        elif DEEP_HEADING_RE.match(line):
            problems.append(f"Line {lineno}: H3 or deeper headings are not allowed.")
        elif H2_RE.match(line):
            phase = "sections"
            sections.append((line[3:].strip(), 0))
        elif phase == "sections":
            if ENTRY_RE.match(line):
                name, count = sections[-1]
                sections[-1] = (name, count + 1)
            else:
                problems.append(f"Line {lineno}: sections may only contain '- [name](url): notes' list items.")
        elif line.startswith(">"):
            if phase not in ("h1", "summary"):
                problems.append(f"Line {lineno}: the summary blockquote must come right after the H1.")
            phase = "summary"
        else:
            phase = "details"

    for name, count in sections:
        if count == 0:
            problems.append(f"Section '{name}' has no links.")
    names = [name for name, _ in sections]
    if OPTIONAL_SECTION in names and names[-1] != OPTIONAL_SECTION:
        problems.append("The Optional section must be last.")
    return problems
