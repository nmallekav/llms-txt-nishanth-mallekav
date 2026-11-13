"""Generate an llms.txt from the command line.

    python -m app.cli https://example.com [--out llms.txt] [--max-pages 50]
"""

import argparse
import asyncio
import logging
import sys

from .config import get_settings
from .crawler import CrawlError
from .generate import generate


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an llms.txt file for a website.")
    parser.add_argument("url")
    parser.add_argument("--out", help="Write to this file instead of stdout")
    parser.add_argument("--max-pages", type=int, help="Override CRAWL_MAX_PAGES")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    if args.max_pages:
        settings = settings.model_copy(update={"crawl_max_pages": args.max_pages})

    try:
        result = asyncio.run(generate(args.url, settings))
    except CrawlError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result.llms_txt)
    else:
        sys.stdout.write(result.llms_txt)

    crawl = result.crawl
    print(
        f"\n{len(crawl.pages)} pages, {len(crawl.sitemaps)} sitemaps, {crawl.duration_seconds}s, "
        f"stopped: {crawl.stop_reason}, problems: {result.problems or 'none'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
