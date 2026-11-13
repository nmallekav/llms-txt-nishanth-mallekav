"""All tunable settings, loaded from environment variables or a .env file.

Every field can be overridden with an env var of the same name in upper case,
e.g. CRAWL_MAX_PAGES=100.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'llms.db'}"
    # Public URL of this app, used to build stable links. Empty = use the request's host.
    public_base_url: str = ""
    frontend_dist: str = str(REPO_ROOT / "frontend" / "dist")
    log_level: str = "INFO"

    # --- Crawl limits ---
    crawl_max_pages: int = 50
    crawl_max_depth: int = 3
    crawl_timeout_seconds: float = 45
    crawl_request_timeout_seconds: float = 10
    crawl_concurrency: int = 5
    crawl_max_redirects: int = 5
    crawl_max_body_bytes: int = 2_000_000
    crawl_max_consecutive_errors: int = 5
    crawl_max_sitemaps: int = 5
    crawl_max_sitemap_urls: int = 5000
    crawl_section_ceiling: int = 15
    crawl_user_agent: str = "llms-txt-generator/1.0 (+https://github.com/nmallekav/llms-txt-nishanth-mallekav)"
    # Block private, loopback and link-local addresses (protects the server from SSRF).
    crawl_allow_private_hosts: bool = False

    # --- Markdown version probing (spec: link to .md versions when they exist) ---
    crawl_probe_markdown: bool = True
    crawl_probe_timeout_seconds: float = 15

    # --- Scoring weights (see docs/crawler.md) ---
    score_nav: float = 30
    score_sitemap: float = 15
    score_sitemap_priority: float = 10
    score_body: float = 10
    score_footer: float = 5
    score_inbound: float = 3
    score_keyword: float = 20
    score_depth_penalty: float = 8
    score_section_penalty: float = 5
    score_keywords: list[str] = [
        "docs", "documentation", "guide", "guides", "api", "getting-started",
        "quickstart", "reference", "about", "pricing", "product", "products",
        "features", "faq",
    ]

    # --- Output ---
    description_max_chars: int = 200

    # --- Monitoring (see docs/monitoring.md) ---
    monitor_enabled: bool = True
    monitor_check_interval_hours: float = 24
    monitor_full_crawl_ttl_days: float = 7  # 0 disables the forced full crawl
    monitor_tick_minutes: float = 15
    monitor_sites_per_tick: int = 5
    monitor_retry_hours: float = 6
    monitor_max_retries: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
