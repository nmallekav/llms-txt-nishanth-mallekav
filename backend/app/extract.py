"""Pull the metadata llms.txt needs out of an HTML page. Pure functions, no network."""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

TITLE_SEPARATORS = re.compile(r"\s+[|\-–—·•]{1,2}\s+")
GENERIC_TITLE_PARTS = {"home", "homepage", "home page", "welcome", "official site", "official website", "index"}
MIN_PARAGRAPH_CHARS = 40


@dataclass
class Link:
    url: str  # raw href, resolved later by the crawler
    source: str  # "nav", "footer" or "body"


@dataclass
class PageMeta:
    title: str | None = None
    og_title: str | None = None
    h1: str | None = None
    description: str | None = None
    og_description: str | None = None
    first_paragraph: str | None = None
    site_name: str | None = None
    md_alternate: str | None = None
    noindex: bool = False
    links: list[Link] = field(default_factory=list)

    @property
    def best_title(self) -> str | None:
        return self.og_title or self.title or self.h1

    @property
    def best_description(self) -> str | None:
        return self.description or self.og_description or self.first_paragraph


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_html(content: bytes | str, encoding: str | None = None) -> PageMeta:
    soup = BeautifulSoup(content, "lxml", from_encoding=encoding if isinstance(content, bytes) else None)
    meta = PageMeta()

    if soup.title and soup.title.string:
        meta.title = clean_text(soup.title.string)
    meta.og_title = _meta_content(soup, prop="og:title")
    meta.site_name = _meta_content(soup, prop="og:site_name")
    meta.description = _meta_content(soup, name="description")
    meta.og_description = _meta_content(soup, prop="og:description")

    robots = (_meta_content(soup, name="robots") or "").lower()
    meta.noindex = "noindex" in robots

    for link in soup.find_all("link", href=True):
        rels = [r.lower() for r in (link.get("rel") or [])]
        if "alternate" in rels and (link.get("type") or "").lower() == "text/markdown" and not meta.md_alternate:
            meta.md_alternate = link["href"].strip()

    h1 = soup.find("h1")
    if h1:
        meta.h1 = clean_text(h1.get_text(" "))

    meta.first_paragraph = _first_paragraph(soup)
    meta.links = _links(soup)
    return meta


def _meta_content(soup: BeautifulSoup, name: str | None = None, prop: str | None = None) -> str | None:
    if name:
        tag = soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
    else:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        return clean_text(tag["content"])
    return None


def _in_chrome(tag: Tag) -> str | None:
    """'nav' if inside a nav/header, 'footer' if inside a footer, else None."""
    for parent in tag.parents:
        if parent.name in ("nav", "header") or parent.get("role") == "navigation":
            return "nav"
        if parent.name == "footer" or parent.get("role") == "contentinfo":
            return "footer"
    return None


def _first_paragraph(soup: BeautifulSoup) -> str | None:
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for p in root.find_all("p"):
        if _in_chrome(p):
            continue
        text = clean_text(p.get_text(" "))
        if text and len(text) >= MIN_PARAGRAPH_CHARS:
            return text
    return None


def _links(soup: BeautifulSoup) -> list[Link]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        links.append(Link(url=href, source=_in_chrome(a) or "body"))
    return links


def parse_link_header(value: str | None) -> str | None:
    """Return the markdown alternate URL from an HTTP Link header, if any."""
    if not value:
        return None
    for part in value.split(","):
        match = re.match(r"\s*<([^>]+)>(.*)", part)
        if not match:
            continue
        url, params = match.groups()
        params = params.lower()
        if re.search(r'rel="?[^";]*\balternate\b', params) and "text/markdown" in params:
            return url.strip()
    return None


def split_title(title: str) -> list[str]:
    return [p.strip() for p in TITLE_SEPARATORS.split(title) if p.strip()]


def site_name_from_title(title: str | None) -> str | None:
    """'Home | Acme' -> 'Acme'; 'Acme - Build APIs fast' -> 'Acme'."""
    if not title:
        return None
    parts = [p for p in split_title(title) if p.lower() not in GENERIC_TITLE_PARTS]
    return parts[0] if parts else clean_text(title)


def strip_site_name(title: str, site_name: str | None) -> str:
    """'Pricing | Acme' -> 'Pricing'. Leaves the title alone if nothing would remain."""
    if not site_name:
        return title
    matches = list(TITLE_SEPARATORS.finditer(title))
    if not matches:
        return title
    name = site_name.lower()
    first, last = matches[0], matches[-1]
    if title[last.end():].strip().lower() == name:
        return title[:last.start()].strip()
    if title[:first.start()].strip().lower() == name:
        return title[first.end():].strip()
    return title
