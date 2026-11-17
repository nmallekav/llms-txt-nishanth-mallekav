"""Quality report: how good is a generated llms.txt, and what could the site improve?

Computed from the crawl result and the generated text, with no extra requests.
Each check is "ok" or "warn" with a short message, and the score starts at 100
and loses points for each weakness.
"""

import re
from collections import Counter
from dataclasses import dataclass

from .config import get_settings
from .crawler import CrawlResult
from .llmstxt import BOILERPLATE_MIN_PAGES, OPTIONAL_SECTION

ENTRY_RE = re.compile(r"^- \[(?:[^\]\\]|\\.)+\]\([^)\s]+\)(: .+)?$")
MAX_TOKENS = 10_000  # roughly what fits comfortably in an agent's context
MAX_SECTIONS = 12
LARGE_MENU = 30  # a "menu" bigger than this is a sitemap-style mega menu, not a list of key pages


@dataclass
class Check:
    name: str
    status: str  # "ok" or "warn"
    message: str


@dataclass
class QualityReport:
    score: int  # 0-100
    checks: list[Check]


def assess(result: CrawlResult, text: str, problems: list[str]) -> QualityReport:
    """Score a generated llms.txt and list what's good and what's weak."""
    checks: list[Check] = []
    penalty = 0.0
    lines = text.splitlines()
    entries = [line for line in lines if ENTRY_RE.match(line)]
    pages = [p for p in result.pages if p.url != result.homepage_url]

    # 1. Spec
    if problems:
        checks.append(Check("Spec", "warn", f"{len(problems)} spec problem(s): {problems[0]}"))
        penalty += 40
    else:
        checks.append(Check("Spec", "ok", "Follows the llmstxt.org format"))

    # 2. Descriptions
    with_notes = sum(1 for line in entries if ENTRY_RE.match(line).group(1))
    if entries:
        coverage = with_notes / len(entries)
        status = "ok" if coverage >= 0.8 else "warn"
        checks.append(Check("Descriptions", status, f"{with_notes} of {len(entries)} links have a description"))
        penalty += 30 * (1 - coverage)
    else:
        checks.append(Check("Descriptions", "warn", "The file has no links"))
        penalty += 30

    # 3. Boilerplate descriptions (a site-wide default meta description)
    counts = Counter(p.description for p in pages if p.description)
    shared = sum(n for n in counts.values() if n >= BOILERPLATE_MIN_PAGES)
    if shared:
        checks.append(Check("Unique descriptions", "warn",
                            f"{shared} pages share the same description; replaced with each page's own text"))
        penalty += 5
    else:
        checks.append(Check("Unique descriptions", "ok", "No site-wide default descriptions"))

    # 4. Key pages: everything linked from the homepage menu should be in the file
    if result.nav_urls:
        page_urls = {p.url for p in result.pages}
        included = sum(1 for url in result.nav_urls if url in page_urls)
        total = len(result.nav_urls)
        if total > LARGE_MENU:
            # Not every link in a huge menu can be a key page, so don't penalize missing ones.
            checks.append(Check("Key pages", "ok", f"{included} of {total} menu links included (large menu)"))
        else:
            status = "ok" if included == total else "warn"
            checks.append(Check("Key pages", status, f"{included} of {total} homepage menu pages included"))
            penalty += 15 * (1 - included / total)

    # 5. Markdown versions
    with_md = sum(1 for p in pages if p.md_url)
    if with_md:
        checks.append(Check("Markdown links", "ok", f"{with_md} of {len(pages)} links point to .md versions"))
    else:
        checks.append(Check("Markdown links", "warn", "The site doesn't publish .md versions of its pages"))
        penalty += 5

    # 6. Structure
    sections = [line[3:] for line in lines if line.startswith("## ") and line[3:] != OPTIONAL_SECTION]
    if 1 <= len(sections) <= MAX_SECTIONS:
        checks.append(Check("Structure", "ok", f"{len(sections)} sections"))
    else:
        checks.append(Check("Structure", "warn", f"{len(sections)} sections (aim for 1–{MAX_SECTIONS})"))
        penalty += 5

    # 7. Size
    tokens = len(text) // 4
    if tokens <= MAX_TOKENS:
        checks.append(Check("Size", "ok", f"About {tokens:,} tokens, small enough for an AI's context"))
    else:
        checks.append(Check("Size", "warn", f"About {tokens:,} tokens, which may be too long for some agents"))
        penalty += 10

    # 8. Coverage of the site
    if result.partial:
        checks.append(Check("Coverage", "warn", f"The crawl stopped early ({result.stop_reason}); some pages may be missing"))
        penalty += 10
    elif result.stop_reason == "max_pages" and result.sitemap_url_count > len(result.pages):
        count = result.sitemap_url_count
        # We stop reading sitemaps at a cap, so a count at the cap means "at least this many".
        size = f"{count:,}+" if count >= get_settings().crawl_max_sitemap_urls else f"~{count:,}"
        checks.append(Check("Coverage", "warn", f"Top {len(result.pages)} pages of {size} in the sitemap"))
    else:
        checks.append(Check("Coverage", "ok", f"Crawled {len(result.pages)} pages"))

    return QualityReport(score=max(0, min(100, round(100 - penalty))), checks=checks)
