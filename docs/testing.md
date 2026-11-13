# Testing Against Real Sites

← Back to [PLAN.md](../PLAN.md)

While building, check our output against llms.txt files that real sites already publish.
Reference directory: **[llmstxt.site](https://llmstxt.site/)**.

These files are hand-written or produced by docs platforms, and many don't follow the
spec exactly, so **we don't expect identical text**. The goal is to confirm that our file
captures the same site: a similar name and summary, similar sections, and mostly the
same key pages.

---

## How to run

```bash
cd backend
.venv/bin/python scripts/compare.py https://fastht.ml/docs https://camel.apache.org https://www.limecraft.com https://apistax.io https://docs.stripe.com
```

For each site, the script fetches the site's published llms.txt, generates ours, and
prints: whether each passes our spec validator, both H1s, the section names, and
**link overlap** (the share of their non-Optional links that also appear in ours, comparing
pages rather than formats, so `page.html.md` matches `page.html`).

## Sample sites

Chosen from llmstxt.site to cover different site types:

| Type | Site | What it tests |
|---|---|---|
| Small docs site following the spec | `fastht.ml/docs` (subpath) | Scope, `.md` links, format reference |
| Large open-source project | `camel.apache.org` | Many sections, big sitemap |
| Company / marketing site | `www.limecraft.com` | Nav-driven site, blog and case studies |
| Small SaaS / API product | `apistax.io` | Few pages, docs + blog |
| Large developer docs | `docs.stripe.com` | Deep docs tree, section caps, markdown alternates |

`cybersectools.com` was tried first but returned `429 Too Many Requests` to our crawler,
so it was replaced by `docs.stripe.com`.

## What to compare

| Check | Pass if |
|---|---|
| Spec validity | Our file passes our validator (H1 first, order, H2 link lists, Optional last) |
| Site name (H1) | Refers to the same site/product |
| Summary | Describes the same thing |
| Sections | Sections reflect the site's real structure |
| Key pages | Their most important pages show up in ours |
| No junk | No login, cart, tag, pagination or duplicate pages in ours |

## Results log

Run on 2026-11-14 with default settings (50-page budget).

| Site | Ours valid? | Theirs valid? | H1 (ours / theirs) | Link overlap | Notes |
|---|---|---|---|---|---|
| fastht.ml/docs | ✅ | ✅ | python-fasthtml / FastHTML | 14% (1 of 7) | Their file is 7 hand-picked links, most of them off-site (GitHub, gists); we stay on-site by design. We link the same `.html.md` versions. Our H1 is their `og:site_name`. |
| camel.apache.org | ✅ | ❌ | Apache Camel / Apache Camel | 13% | Theirs is hand-curated with 94 deep links and prose inside its H2 sections (not a pure link list). Our 50-page budget covers the top of each section instead. |
| www.limecraft.com | ✅ | ✅ | Limecraft / Limecraft: The Workspace for Video Teams | 56% | Closest match: same sections (Workflows, Solutions, Cases, Partners). |
| apistax.io | ✅ | ❌ | APIstax / APIstax | n/a | Their links aren't in spec list format, so overlap can't be measured. Our sections (APIs, Docs, Blog) match their topics. |
| docs.stripe.com | ✅ | ❌ | Stripe Documentation / Stripe Documentation | 3% | Theirs lists 459 links; ours is a 50-page overview. Same H1 and summary, and we use their `.md` alternates. |

### Takeaways

- **Every file we generated passed the spec validator; 3 of the 5 published files did not**
  (prose or non-link items inside their H2 sections).
- **Site names match** on every site (allowing for taglines and `og:site_name` differences).
- **Link overlap is low on hand-curated or very large files.** They link deep or off-site
  pages chosen by a human; we pick the top ~50 on-site pages by score. Raising
  `CRAWL_MAX_PAGES` increases overlap on big docs sites at the cost of crawl time.
- Fixes made because of this testing: `og:site_name` taglines are stripped ("APIstax - Secure
  and reliable APIs" → "APIstax"), plural acronyms are cased correctly ("APIs"), and
  site-wide boilerplate descriptions are replaced with page-specific text.
