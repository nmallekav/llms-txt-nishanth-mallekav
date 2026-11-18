"""Crawl a site and turn the result into an llms.txt file."""

from dataclasses import dataclass

from . import config, llmstxt, quality
from .crawler import CrawlResult, crawl


@dataclass
class Generated:
    llms_txt: str
    crawl: CrawlResult
    problems: list[str]  # spec violations found by the validator (should be empty)
    quality: quality.QualityReport


def render(result: CrawlResult) -> str:
    return llmstxt.build(
        site_name=result.site_name,
        summary=result.summary,
        pages=result.pages,
        homepage_url=result.homepage_url,
        scope_path=result.scope_path,
        description_max_chars=config.DESCRIPTION_MAX_CHARS,
    )


async def generate(url: str) -> Generated:
    result = await crawl(url)
    text = render(result)
    problems = llmstxt.validate(text)
    return Generated(llms_txt=text, crawl=result, problems=problems,
                     quality=quality.assess(result, text, problems))
