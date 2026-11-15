"""Build and validate llms.txt files. Format rules: docs/llms-txt-format.md."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from .extract import clean_text
from .urls import path_segments

OPTIONAL_KEYWORDS = {
    "privacy", "privacy-policy", "terms", "terms-of-service", "terms-of-use", "tos",
    "cookies", "cookie-policy", "legal", "security", "accessibility", "careers", "jobs",
    "press", "changelog", "imprint", "disclaimer", "gdpr", "dmca", "status",
}
ACRONYMS = {"api", "faq", "sdk", "cli", "ui", "ux", "ai", "ios", "css", "html", "js", "llm", "rss", "seo", "kb"}
MAIN_SECTION = "Main"
OPTIONAL_SECTION = "Optional"
OTHER_SECTION = "Other"
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
    alt_descriptions: list[str] = field(default_factory=list)


def build(site_name: str, summary: str | None, pages: list, homepage_url: str | None = None,
          scope_path: str = "/", description_max_chars: int = 200) -> str:
    """Render an llms.txt file from crawled pages (anything with Entry's attributes)."""
    pages = _drop_boilerplate([p for p in pages if p.url != homepage_url])
    entries = _dedupe(pages, description_max_chars)

    optional = [e for e in entries if _is_optional(e.url)]
    regular = [e for e in entries if not _is_optional(e.url)]
    sections = _group(regular, scope_path)

    lines = [f"# {clean_text(site_name) or 'Untitled site'}", ""]
    summary = _truncate(clean_text(summary), SUMMARY_MAX_CHARS)
    if summary:
        lines += [f"> {summary}", ""]

    for name, items in sections:
        lines += [f"## {name}", ""] + [_format_entry(e, description_max_chars) for e in items] + [""]
    if optional:
        lines += [f"## {OPTIONAL_SECTION}", ""] + [_format_entry(e, description_max_chars) for e in _sorted(optional)] + [""]

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------- grouping ----------

def _group(entries: list, scope_path: str) -> list[tuple[str, list]]:
    """Group pages into (section name, pages): Main first, then sections by best page, then Other."""
    groups: dict[str, list] = defaultdict(list)
    for e in entries:
        groups[e.section].append(e)
    main = groups.pop("", [])  # top-level pages

    # A top-level page like /blog belongs with the /blog/* pages, not in Main.
    for e in list(main):
        segments = path_segments(e.url, scope_path)
        if len(segments) == 1 and segments[0].lower() in groups:
            main.remove(e)
            groups[segments[0].lower()].insert(0, e)

    # Sections with a single page go into one "Other" section instead of many tiny ones.
    other = [items[0] for items in groups.values() if len(items) == 1]
    groups = {key: items for key, items in groups.items() if len(items) > 1}

    result = []
    if main:
        result.append((MAIN_SECTION, _sorted(main)))
    used_names = {MAIN_SECTION, OTHER_SECTION}
    for key, items in sorted(groups.items(), key=lambda kv: (-max(e.score for e in kv[1]), kv[0])):
        name = _section_name(key)
        while name in used_names:
            name += " Pages"
        used_names.add(name)
        # The section's overview page (/blog) first, then the rest by score.
        result.append((name, sorted(items, key=lambda e: (len(path_segments(e.url, scope_path)), -e.score, e.url))))
    if other:
        result.append((OTHER_SECTION, _sorted(other)))
    return result


def _section_name(key: str) -> str:
    words = re.split(r"[-_\s]+", key.strip())
    name = " ".join(_word(w) for w in words if w)
    name = name or MAIN_SECTION
    reserved = (OPTIONAL_SECTION.lower(), MAIN_SECTION.lower(), OTHER_SECTION.lower())
    return f"{name} Pages" if name.lower() in reserved else name


def _word(word: str) -> str:
    lower = word.lower()
    if lower in ACRONYMS:
        return lower.upper()
    if lower.endswith("s") and lower[:-1] in ACRONYMS:  # apis -> APIs
        return lower[:-1].upper() + "s"
    return word.capitalize()


def _is_optional(url: str) -> bool:
    segments = [s.lower().rsplit(".", 1)[0] for s in urlsplit(url).path.split("/") if s]
    return any(s in OPTIONAL_KEYWORDS for s in segments)


def _sorted(entries: list) -> list:
    return sorted(entries, key=lambda e: (-e.score, e.url))


def _drop_boilerplate(pages: list) -> list:
    """Replace descriptions repeated across many pages (a site-wide default) with each page's fallback."""
    counts = Counter(p.description for p in pages if p.description)
    boilerplate = {d for d, n in counts.items() if n >= BOILERPLATE_MIN_PAGES}
    if not boilerplate:
        return pages
    result = []
    for p in pages:
        if p.description in boilerplate:
            fallback = next((d for d in getattr(p, "alt_descriptions", []) if d not in boilerplate), None)
            p = replace(p, description=fallback)
        result.append(p)
    return result


def _dedupe(pages: list, max_chars: int) -> list:
    """Drop repeated URLs and pages with the same title and description, keeping the best-scored one."""
    seen_urls: set[str] = set()
    seen_text: set[tuple[str, str]] = set()
    result = []
    for p in _sorted(pages):
        text = (p.title.lower(), (_note(p, max_chars) or "").lower())
        if p.url in seen_urls or text in seen_text:
            continue
        seen_urls.add(p.url)
        seen_text.add(text)
        result.append(p)
    return result


# ---------- formatting ----------

def _format_entry(e, max_chars: int) -> str:
    title = (clean_text(e.title) or e.url).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    url = _escape_url(e.md_url or e.url)
    note = _note(e, max_chars)
    return f"- [{title}]({url}): {note}" if note else f"- [{title}]({url})"


def _note(e, max_chars: int) -> str | None:
    note = _truncate(clean_text(e.description), max_chars)
    if not note or note.lower().rstrip(".") == (e.title or "").lower().rstrip("."):
        return None
    return note


def _escape_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _truncate(text: str | None, max_chars: int) -> str | None:
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return cut + "…"


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
