# Crawler Design

← Back to [PLAN.md](../PLAN.md)

How the crawler decides **which links to visit**, **in what order**, and **when to stop**.
The goal is not to crawl the whole site. It is to find the ~50 pages that best
describe it, because an llms.txt file is a curated overview, not a sitemap.

---

## Overview: best-first crawl

The crawler is a **best-first search**. Every discovered link gets a score; the
crawler always fetches the highest-scoring links next. When
the page budget runs out, we have the best pages seen so far, not just the first ones.

```mermaid
flowchart TD
    A[Start URL] --> B[Fetch robots.txt]
    B --> C[Fetch homepage]
    C -->|fails| X[Return error]
    C --> D[Collect candidates:<br/>homepage links + sitemap URLs]
    D --> E{Stop condition hit?}
    E -->|yes| Z[Return extracted pages]
    E -->|no| F[Take highest-scoring URLs]
    F --> I[Fetch page]
    I --> J{Fetched OK, HTML,<br/>not noindex?}
    J -->|no| E
    J -->|yes| K[Extract title, description,<br/>markdown alternate]
    K --> L[Normalize + filter new links]
    L --> M[Score new links<br/>or re-score already-found ones]
    M --> E
```

---

## 1. Discovering links

Sources, collected before crawling starts:

1. **Sitemaps:** `Sitemap:` lines in `robots.txt`, or `/sitemap.xml` if there are none.
   Nested sitemap indexes are followed (max 5 sitemap files).
2. **Homepage navigation:** links inside `<nav>`, `<header>`, and `<footer>`.
3. **Page links:** links found on each fetched page, added as the crawl proceeds.

Seeding with sitemap and nav links up front gives the crawler a good view of the site
before the greedy search begins.

## 2. Scope

- Same host only. `www.example.com` and `example.com` are treated as one site, and every
  URL is rewritten onto the homepage's scheme and host so they aren't counted twice.
- Private, loopback and link-local addresses are refused (SSRF protection), unless
  `CRAWL_ALLOW_PRIVATE_HOSTS=true`.
- If the user enters a subpath (e.g. `example.com/docs`), only URLs under `/docs`
  are crawled. This matches the spec: an llms.txt file covers the pages under its path.
- Redirects to another domain are dropped.

## 3. URL normalization

Applied to every link so each page is counted once:

- Resolve relative URLs against the page URL.
- Lowercase scheme and host; drop default ports.
- Remove `#fragment`.
- Remove tracking params: `utm_*`, `gclid`, `fbclid`, `ref`, `mc_cid`, `mc_eid`.
- Sort remaining query params.
- Consistent trailing slash (strip it, except for the root `/`).
- Fold `/index.html`, `/index.htm` and `/index.php` into the directory URL.

## 4. Filtering (skip these links)

| Rule | Examples |
|---|---|
| Non-HTTP schemes | `mailto:`, `tel:`, `javascript:` |
| Non-HTML file extensions | `.pdf .jpg .png .gif .svg .zip .css .js .xml .mp4` |
| Utility pages | `/login`, `/signin`, `/signup`, `/register`, `/cart`, `/checkout`, `/account`, `/search`, `/admin` |
| Near-duplicates | `?page=2+`, `/page/2+`, `/tag/`, `/category/`, `/YYYY/MM/` archives |
| Other-language copies | `/fr/`, `/de/`, `/es/`, … when the start URL isn't under one |
| Disallowed | Paths blocked by `robots.txt` for our User-Agent |

Checked after fetching:

- `Content-Type` is not `text/html` → skip.
- `<meta name="robots" content="noindex">` → don't include in output.

---

## 5. Scoring

### How URLs are picked

- A `candidates` dict holds every URL found but not fetched yet, with its signals and score.
- A URL found again gets its signals updated and is re-scored; it's still only fetched once.
- Each round, the candidates are sorted by score and the top 5 are fetched.

### Score formula (higher = more important)

| Signal | Points |
|---|---|
| Linked from homepage nav / header | +30 |
| Listed in a sitemap | +15, plus `<priority>` × 10 if present |
| Linked from a page body | +10 |
| Linked from a footer | +5 |
| Each additional page linking to it | +3 |
| High-value path keyword (`docs`, `guide`, `api`, `getting-started`, `quickstart`, `reference`, `about`, `pricing`, `product`, `features`, `faq`) | +20 |
| Each path segment deep (`/a/b/c` = 3) | −8 |
| Each page already saved from the same section | −5 |

A **section** is the first path segment under the scope (`/docs/...` → `docs`); top-level
pages (`/about`) have no section and go to **Main**; date-based paths (`/2024/05/post`)
form one `posts` section.

### When scores are computed

A URL is scored when it's found, and re-scored each time it's found again (e.g. another
page links to it). The section penalty uses the number of pages saved from that section
**at that moment**, which spreads the 50 pages across sections instead of letting one
big section (e.g. `/blog`) take them all.

### Known trade-off

Best-first is greedy: it can only rank links it has already discovered, so a valuable
page buried deep with no sitemap entry can be missed. Seeding from sitemaps and nav
links covers this for most real sites.

---

## 6. Stop conditions

The crawl ends at the **first** of these:

| Condition | Default |
|---|---|
| Pages fetched | 50 |
| Link depth from start URL | 3 |
| Total crawl time | 45s: no new requests start after that (requests already running may take up to 10s more) |
| No candidates left | — |

Early exit: the homepage fails to load → return a clear error (no partial result).
A page that fails later (timeout, 404, 5xx...) is just skipped.

## 7. Request limits and politeness

| Setting | Default |
|---|---|
| Concurrent requests | 5 |
| Per-request timeout | 10s |
| Max redirects | 5 |
| Max HTML body size | 2 MB (larger pages skipped) |
| User-Agent | `llms-txt-generator/1.0 (+<repo url>)` |

---

## 8. Markdown versions of pages

The spec asks that llms.txt links point to LLM-friendly content where it exists.
If a page declares a markdown version, the llms.txt entry links to it instead of the HTML page:

1. `<link rel="alternate" type="text/markdown" href="...">` in the page `<head>`, or
2. the same relation in an HTTP `Link:` response header.

We don't guess conventional `.md` URLs; only versions the site declares are used.

---

## Configuration

All numbers above live in `backend/app/config.py` and can be overridden with
environment variables (e.g. `CRAWL_MAX_PAGES=100`, `CRAWL_TIMEOUT_SECONDS=60`).
Weights are expected to be tuned after testing on real sites.
