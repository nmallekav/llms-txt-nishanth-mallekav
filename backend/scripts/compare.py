"""Compare our generated llms.txt with the one a site publishes (docs/testing.md).

    python scripts/compare.py https://camel.apache.org https://fastht.ml/docs ...

For each site prints: whether ours passes the spec validator, both H1s, section names,
and link overlap (share of their non-Optional links that also appear in ours).
"""

import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llmstxt, urls  # noqa: E402
from app.crawler import CrawlError  # noqa: E402
from app.fetcher import make_client  # noqa: E402
from app.generate import generate  # noqa: E402

LINK_RE = re.compile(r"^- \[(?:[^\]\\]|\\.)+\]\(([^)\s]+)\)", re.M)


def parse(text: str) -> tuple[str, list[str], set[str]]:
    text = text.lstrip("\ufeff")
    h1 = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "?")
    sections = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    main_part = re.split(r"^## Optional\s*$", text, flags=re.M)[0]
    links = {_key(u) for u in LINK_RE.findall(main_part)}
    return h1, sections, links


def _key(url: str) -> str:
    """Compare pages, not formats: drop .md / .html.md / index.html.md suffixes."""
    url = urls.normalize(re.sub(r"(/index)?(\.html)?\.md$", "", url)) or url
    host = httpx.URL(url).host
    return url.replace(host, urls.bare_host(host), 1)


async def published_llms_txt(site: str) -> str | None:
    parts = httpx.URL(urls.ensure_scheme(site))
    path = parts.path.rstrip("/")
    async with make_client() as client:
        for candidate in (f"{parts.scheme}://{parts.host}{path}/llms.txt", f"{parts.scheme}://{parts.host}/llms.txt"):
            r = await client.get(candidate)
            if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                return r.text
    return None


async def compare(site: str) -> None:
    print(f"\n=== {site}")
    theirs = await published_llms_txt(site)
    try:
        ours = (await generate(site)).llms_txt
    except CrawlError as e:
        print(f"  our crawl failed: {e}")
        return
    o_h1, o_sections, o_links = parse(ours)
    print(f"  ours:   valid={not llmstxt.validate(ours)}  h1={o_h1!r}  links={len(o_links)}  sections={o_sections}")
    if theirs is None:
        print("  theirs: not found")
        return
    t_h1, t_sections, t_links = parse(theirs)
    print(f"  theirs: valid={not llmstxt.validate(theirs)}  h1={t_h1!r}  links={len(t_links)}  sections={t_sections}")
    if t_links:
        overlap = len(t_links & o_links) / len(t_links)
        print(f"  overlap: {overlap:.0%} of their main links are in ours")


async def main(sites: list[str]) -> None:
    for site in sites:
        await compare(site)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
