"""How the crawler decides which URL to fetch next. Weights are in config.py (score_*).

A higher score means "fetch sooner". A URL earns points for where links to it appear
and loses points for being deep or in a section we already have many pages from.
"""

from dataclasses import dataclass, field

from . import urls
from .config import Settings


@dataclass
class Candidate:
    """A URL waiting in the crawl queue, with what we know about it so far."""
    url: str
    depth: int  # clicks from the homepage
    sources: set[str] = field(default_factory=set)  # where links to it appeared: "nav", "body", "footer", "sitemap"
    inbound: int = 0  # how many pages link to it
    sitemap_priority: float | None = None  # 0-1, from <priority> in the sitemap
    score: float = 0.0  # its score when it was taken off the queue


def score(c: Candidate, scope: str, pages_in_section: int, cfg: Settings) -> float:
    total = 0.0

    # Where links to it appeared. The main navigation is the strongest signal.
    if "nav" in c.sources:
        total += cfg.score_nav
    if "sitemap" in c.sources:
        total += cfg.score_sitemap + (c.sitemap_priority or 0) * cfg.score_sitemap_priority
    if "body" in c.sources:
        total += cfg.score_body
    if "footer" in c.sources:
        total += cfg.score_footer
    total += cfg.score_inbound * max(0, c.inbound - 1)  # linked from many pages

    # What the URL looks like.
    segments = [s.lower() for s in urls.path_segments(c.url, scope)]
    if any(s in cfg.score_keywords for s in segments):  # /docs, /pricing, /about...
        total += cfg.score_keyword
    total -= cfg.score_depth_penalty * len(segments)  # /a/b/c is less important than /a
    total -= cfg.score_section_penalty * pages_in_section  # spread pages across sections
    return total
