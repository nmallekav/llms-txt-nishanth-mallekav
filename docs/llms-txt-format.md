# llms.txt Output Format

← Back to [PLAN.md](../PLAN.md)

How the generator turns crawled pages into an `llms.txt` file that conforms to the
[llms.txt spec (v2)](https://llmstxt.org/).

---

## Spec rules we must follow

The file is Markdown with these parts, **in this order**:

| # | Part | Required? | What we put there |
|---|---|---|---|
| 1 | H1: `# Name` | **Yes** (the only required part) | Site name |
| 2 | Blockquote: `> summary` | Optional | One-sentence site summary |
| 3 | Detail paragraphs / lists (**no headings**) | Optional | Omitted in v1 unless we have a useful line |
| 4 | H2 sections with file lists | Optional | Pages grouped by section |

File list entries:

```
- [Link title](https://url): Optional notes
```

- The `[name](url)` hyperlink is required; `: notes` is optional.
- A section named exactly `## Optional` holds secondary links agents can skip.

Hard constraints the generator enforces:

- Exactly one H1, and it is the first line.
- No H3+ headings anywhere; no headings inside the detail block.
- Every H2 contains only a markdown list of links.
- `## Optional`, if present, is the last section.
- UTF-8 output, `\n` line endings, single blank line between blocks.

---

## Where each value comes from

| Output | Source (first match wins) |
|---|---|
| Site name (H1) | `og:site_name` → homepage `<title>`, in both cases with taglines like ` – Home` / `| Tagline` stripped → domain name |
| Summary (blockquote) | Homepage `meta description` → `og:description` → first meaningful `<p>` |
| Link title | `og:title` → `<title>` (site-name suffix stripped) → first `<h1>` → URL path |
| Link notes | `meta description` → `og:description` → first meaningful `<p>`; omitted if none |
| Link URL | Markdown version if found (see [crawler.md §8](crawler.md#8-markdown-versions-of-pages)), else canonical page URL |

## Cleaning text

- Collapse whitespace and newlines into single spaces (each entry must be one line).
- Trim notes to ~200 characters at a word boundary, adding `…` if cut.
- Escape `[` and `]` in link titles; percent-encode spaces and `)` in URLs.
- Drop notes that just repeat the title.
- **Boilerplate descriptions:** if 3+ pages share the same description (a site-wide default
  meta tag), each of those pages uses its own fallback instead (`og:description`, then
  first paragraph), or no note.
- The homepage itself is not listed: its name and summary are the H1 and blockquote.
- Drop duplicate entries (same URL, or same title + notes).

## Grouping into sections

1. Group pages by section (first path segment): `/docs/...` → **Docs**,
   `/blog/...` → **Blog**.
2. Section names are title-cased with `-`/`_` turned into spaces
   (`getting-started` → **Getting Started**).
3. Top-level pages (`/about`, `/pricing`) go into a **Main** section, listed first. A
   top-level page that is a section's overview (`/blog` when `/blog/*` exists) goes into
   that section, listed first.
4. Sections with only one page are collected into one **Other** section (before Optional)
   instead of many one-line sections.
5. Remaining sections are ordered by their highest page score from the crawler.
6. Within a section, pages are ordered by crawler score (ties by URL, so output is deterministic).
7. A section is never named `Optional`, `Main` or `Other` by accident; a site path `/optional` becomes **Optional Pages**.
8. Acronyms are kept upper case, including plurals: `api` → **API**, `apis` → **APIs**.

## What goes in `## Optional`

Pages that are useful but secondary:

- Legal and policy pages: `privacy`, `terms`, `cookies`, `legal`, `security`, `accessibility`.
- Careers, press, and changelog pages.

---

## Example output

```markdown
# Acme

> Acme makes developer tools for building and deploying APIs.

## Main

- [Pricing](https://acme.com/pricing): Plans for individuals, teams, and enterprises.
- [About](https://acme.com/about): The team and mission behind Acme.

## Docs

- [Quickstart](https://acme.com/docs/quickstart.md): Install the CLI and deploy your first API.
- [API Reference](https://acme.com/docs/api.md): Every endpoint, parameter, and response type.

## Blog

- [Launching Acme 2.0](https://acme.com/blog/acme-2): What's new in our biggest release.

## Optional

- [Privacy Policy](https://acme.com/privacy)
- [Terms of Service](https://acme.com/terms)
```

---

## Validation

A small parser in `backend/app/llmstxt.py` re-reads every generated file and checks
the rules above (H1 first, part order, no H3+, every H2 body is a link list, Optional last).
Unit tests run it against generated output for several fixture sites, so a formatting
regression fails the test suite.
