"""Crawl a site and turn the result into an llms.txt file."""

from dataclasses import dataclass

from . import llmstxt, quality
from .crawler import CrawlResult, crawl


@dataclass
class Generated:
    llms_txt: str
    crawl: CrawlResult
    problems: list[str]  # spec violations found by the validator (should be empty)
    quality: quality.QualityReport


def render(result: CrawlResult) -> str:
    return llmstxt.build(result.site_name, result.summary, result.pages, result.homepage_url)


async def generate(url: str) -> Generated:
    result = await crawl(url)
    text = render(result)
    problems = llmstxt.validate(text)
    return Generated(llms_txt=text, crawl=result, problems=problems,
                     quality=quality.assess(result, text, problems))
