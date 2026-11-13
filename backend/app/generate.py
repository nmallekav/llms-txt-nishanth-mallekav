"""Crawl a site and turn the result into an llms.txt file."""

from dataclasses import dataclass

from . import llmstxt
from .config import Settings, get_settings
from .crawler import CrawlResult, Page, crawl


@dataclass
class Generated:
    llms_txt: str
    crawl: CrawlResult
    problems: list[str]  # spec violations found by the validator (should be empty)


def render(result: CrawlResult, pages: list[Page] | None = None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return llmstxt.build(
        site_name=result.site_name,
        summary=result.summary,
        pages=result.pages if pages is None else pages,
        homepage_url=result.homepage_url,
        scope_path=result.scope_path,
        description_max_chars=settings.description_max_chars,
    )


async def generate(url: str, settings: Settings | None = None) -> Generated:
    settings = settings or get_settings()
    result = await crawl(url, settings)
    text = render(result, settings=settings)
    return Generated(llms_txt=text, crawl=result, problems=llmstxt.validate(text))
