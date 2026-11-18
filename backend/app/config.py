"""All tunable settings, read from environment variables (or a .env file in the repo root).

Every setting can be overridden with an environment variable of the same name,
e.g. CRAWL_MAX_PAGES=100. Values are read once, when the app starts.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Copy KEY=VALUE lines from a .env file into os.environ. Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv(REPO_ROOT / ".env")


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# --- App ---
DATABASE_URL = _str("DATABASE_URL", f"sqlite:///{REPO_ROOT / 'data' / 'llms.db'}")
# Public URL of this app, used to build stable links. Empty = use the request's host.
PUBLIC_BASE_URL = _str("PUBLIC_BASE_URL", "")
FRONTEND_DIST = _str("FRONTEND_DIST", str(REPO_ROOT / "frontend" / "dist"))
LOG_LEVEL = _str("LOG_LEVEL", "INFO")

# --- Crawl limits ---
CRAWL_MAX_PAGES = _int("CRAWL_MAX_PAGES", 50)
CRAWL_MAX_DEPTH = _int("CRAWL_MAX_DEPTH", 3)
CRAWL_TIMEOUT_SECONDS = _float("CRAWL_TIMEOUT_SECONDS", 45)
CRAWL_REQUEST_TIMEOUT_SECONDS = _float("CRAWL_REQUEST_TIMEOUT_SECONDS", 10)
CRAWL_CONCURRENCY = _int("CRAWL_CONCURRENCY", 5)
CRAWL_MAX_REDIRECTS = _int("CRAWL_MAX_REDIRECTS", 5)
# Largest response we download from one URL (pages, robots.txt, sitemaps). Bigger ones are skipped.
# Gzipped sitemaps may unpack to 5x this. Protects memory and the crawl's time budget.
CRAWL_MAX_BODY_BYTES = _int("CRAWL_MAX_BODY_BYTES", 2_000_000)
# How many sitemap files we fetch per crawl, including child sitemaps from a sitemap index.
CRAWL_MAX_SITEMAPS = _int("CRAWL_MAX_SITEMAPS", 5)
CRAWL_MAX_SITEMAP_URLS = _int("CRAWL_MAX_SITEMAP_URLS", 5000)
CRAWL_USER_AGENT = _str(
    "CRAWL_USER_AGENT", "llms-txt-generator/1.0 (+https://github.com/nmallekav/llms-txt-nishanth-mallekav)")
# Block private, loopback and link-local addresses (protects the server from SSRF).
CRAWL_ALLOW_PRIVATE_HOSTS = _bool("CRAWL_ALLOW_PRIVATE_HOSTS", False)

# --- Scoring weights (see docs/crawler.md) ---
SCORE_NAV = _float("SCORE_NAV", 30)
SCORE_SITEMAP = _float("SCORE_SITEMAP", 15)
SCORE_SITEMAP_PRIORITY = _float("SCORE_SITEMAP_PRIORITY", 10)
SCORE_BODY = _float("SCORE_BODY", 10)
SCORE_FOOTER = _float("SCORE_FOOTER", 5)
SCORE_INBOUND = _float("SCORE_INBOUND", 3)
SCORE_KEYWORD = _float("SCORE_KEYWORD", 20)
SCORE_DEPTH_PENALTY = _float("SCORE_DEPTH_PENALTY", 8)
SCORE_SECTION_PENALTY = _float("SCORE_SECTION_PENALTY", 5)
SCORE_KEYWORDS = {
    "docs", "documentation", "guide", "guides", "api", "getting-started",
    "quickstart", "reference", "about", "pricing", "product", "products",
    "features", "faq",
}

# --- Output ---
DESCRIPTION_MAX_CHARS = _int("DESCRIPTION_MAX_CHARS", 200)

# --- Monitoring (see docs/monitoring.md) ---
MONITOR_ENABLED = _bool("MONITOR_ENABLED", False)  # off for now; set MONITOR_ENABLED=true to start the scheduler
MONITOR_CHECK_INTERVAL_HOURS = _float("MONITOR_CHECK_INTERVAL_HOURS", 24)
MONITOR_FULL_CRAWL_TTL_DAYS = _float("MONITOR_FULL_CRAWL_TTL_DAYS", 7)  # 0 disables the forced full crawl
MONITOR_TICK_MINUTES = _float("MONITOR_TICK_MINUTES", 15)
MONITOR_SITES_PER_TICK = _int("MONITOR_SITES_PER_TICK", 5)
MONITOR_RETRY_HOURS = _float("MONITOR_RETRY_HOURS", 6)
MONITOR_MAX_RETRIES = _int("MONITOR_MAX_RETRIES", 3)
