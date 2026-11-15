"""Parsing sitemap.xml files (https://www.sitemaps.org). Pure functions, no network."""

import xml.etree.ElementTree as ET
import zlib


def parse_sitemap(body: bytes, max_decompressed: int) -> tuple[list[tuple[str, float | None]], list[str]] | None:
    """Parse a sitemap or sitemap index (optionally gzipped).

    Returns ([(page url, priority)], [child sitemap urls]), or None if it isn't a valid sitemap.
    A regular sitemap lists pages; a sitemap index lists other sitemaps.
    """
    if body[:2] == b"\x1f\x8b":  # gzip
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, max_decompressed)
        except zlib.error:
            return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    pages: list[tuple[str, float | None]] = []
    children: list[str] = []
    for item in root:
        loc, priority = _read_item(item)
        if not loc:
            continue
        if _tag(root) == "sitemapindex":
            children.append(loc)
        else:
            pages.append((loc, priority))
    return pages, children


def _read_item(item: ET.Element) -> tuple[str | None, float | None]:
    """<url><loc>https://…</loc><priority>0.8</priority></url> -> ("https://…", 0.8)"""
    loc = priority = None
    for child in item:
        if _tag(child) == "loc" and child.text:
            loc = child.text.strip()
        elif _tag(child) == "priority" and child.text:
            try:
                priority = min(1.0, max(0.0, float(child.text)))
            except ValueError:
                pass
    return loc, priority


def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]  # drop the XML namespace
