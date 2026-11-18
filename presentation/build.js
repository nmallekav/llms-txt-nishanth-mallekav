const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.SKILL_DIR + "/scripts/apply_theme.js");

const OUT = process.argv[2];
const THEME = {
  name: "llms.txt Generator",
  headFontFace: "Arial",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "171A2B", lt1: "FFFFFF", dk2: "4A5070", lt2: "F2F3F7",
    accent1: "FF5A36", accent2: "1FA898", accent3: "8C92AC", accent4: "FFD166",
    accent5: "E4E6EE", accent6: "FFE6DF", hlink: "FF5A36", folHlink: "C2412A",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "llms.txt Generator";
const C = pres.SchemeColor;

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "Dark Title",
  objects: [
    { rect: { x: 0, y: 0, w: 13.33, h: 7.5, fill: { color: C.text1 } } },
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.2, w: 11.7, h: 1.6,
      fontSize: 48, bold: true, color: C.background1, fontFace: THEME.headFontFace, valign: "bottom", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 4.0, w: 11.7, h: 1.4,
      fontSize: 20, color: C.accent3, valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "Title Only",
  background: { color: "FFFFFF" },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.85,
      fontSize: 34, bold: true, color: C.text1, fontFace: THEME.headFontFace, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { text: { text: "llms.txt generator", options: { x: 0.6, y: 7.0, w: 4, h: 0.3, fontSize: 10, color: C.accent3, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.4, h: 0.3, fontSize: 10, color: C.accent3, align: "right" },
});

let objN = 0;
const name = (p) => `${p}-${++objN}`;
function box(slide, x, y, w, h, opts = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: opts.r ?? 0.12,
    fill: { color: opts.fill ?? C.background2 },
    line: opts.line ? { color: opts.line, width: opts.lineW ?? 1.25, dashType: opts.dash ?? "solid" } : { type: "none" },
    objectName: name(opts.name ?? "box"),
  });
}
function text(slide, t, x, y, w, h, opts = {}) {
  slide.addText(t, { x, y, w, h, isTextBox: true, margin: 0, fontSize: 14, color: C.text1, valign: "top", ...opts, objectName: name(opts.objectName ?? "text") });
}
function badge(slide, label, x, y, d = 0.5, fill = C.accent1) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, objectName: name("badge") });
  slide.addText(label, { x, y, w: d, h: d, isTextBox: true, margin: 0, align: "center", valign: "middle",
    fontSize: d > 0.45 ? 16 : 12, bold: true, color: C.background1, objectName: name("badge-text") });
}
function arrow(slide, x, y, w, h, opts = {}) {
  slide.addShape(pres.shapes.LINE, { x, y, w, h, flipH: !!opts.flipH, flipV: !!opts.flipV,
    line: { color: opts.color ?? C.text2, width: 1.75, endArrowType: "triangle" }, objectName: name("arrow") });
}
function bullets(items, size = 14) {
  return items.map((s, i) => {
    const runs = Array.isArray(s) ? s : [{ text: s }];
    return runs.map((r, j) => ({ text: r.text, options: { ...(j === 0 ? { bullet: { indent: 14 } } : {}), bold: !!r.bold, fontSize: size,
      paraSpaceAfter: 6, breakLine: j === runs.length - 1 && i < items.length - 1 } }));
  }).flat();
}

// ---------- 1. Title ----------
pres.addSection({ title: "Intro" });
let s = pres.addSlide({ masterName: "Dark Title", sectionTitle: "Intro" });
s.addText("llms.txt Generator", { placeholder: "title" });
s.addText("Turn any website into an llms.txt file, and keep it up to date automatically", { placeholder: "body" });
badge(s, "", 0.8, 1.5, 0.35);
text(s, "Take-home project for Profound", 1.3, 1.52, 6, 0.32, { fontSize: 16, color: C.background1, valign: "middle" });
s.addNotes("Introduce the project: a web app that generates llms.txt files for any site and monitors them for changes.");

// ---------- 2. What it does ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Intro" });
s.addText("What it does", { placeholder: "title" });
text(s, "Enter a URL, get a spec-compliant llms.txt: a curated map of a site's most important pages for LLMs.", 0.6, 1.35, 12.1, 0.5, { fontSize: 18, color: C.text2 });
const steps = [
  ["Crawl", ["Best-first crawler always fetches the highest-scoring links next, keeping the ~50 most important pages",
    "Seeds from sitemaps and homepage nav; respects robots.txt and stays on the site"]],
  ["Extract", ["Reads each page's title, meta description and markdown link",
    "Finds the LLM-friendly markdown (.md) version when the site publishes one"]],
  ["Generate", ["Builds the H1 name, > summary, H2 link sections and ## Optional last",
    "Validated against the spec; links point to .md versions where they exist"]],
  ["Score", ["Quality report out of 100 with pass / warning checks",
    "Flags missing descriptions, missing menu pages, no .md versions, oversized files, partial crawls"]],
  ["Monitor", ["Daily ETag check; re-crawls only when something actually changed",
    "Stable link /s/{id}/llms.txt always serves the latest version"]],
];
steps.forEach(([h, items], i) => {
  const y = 2.05 + i * 0.97;
  box(s, 0.6, y, 12.1, 0.85, { fill: i % 2 ? "FFFFFF" : C.background2, name: "step-row" });
  badge(s, String(i + 1), 0.85, y + 0.175);
  text(s, h, 1.55, y, 1.8, 0.85, { fontSize: 20, bold: true, valign: "middle" });
  text(s, bullets(items, 14).map((r) => ({ ...r, options: { ...r.options, paraSpaceAfter: 2 } })), 3.4, y, 9.1, 0.85, { color: C.text2, valign: "middle" });
});
s.addNotes("Walk through the five stages. Stack: Python 3.12, FastAPI, httpx, BeautifulSoup, SQLite, APScheduler, React + Vite, one Docker image on Railway. No API keys needed: it only fetches public pages.");

// ---------- 3. What I considered ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Intro" });
s.addText("What I considered before building", { placeholder: "title" });
text(s, bullets([
  [{ text: "Follow the llms.txt spec (llmstxt.org): ", bold: true }, { text: "one H1 (the only required part), optional > summary, H2 sections of link lists, ## Optional last, no H3+" }],
  [{ text: "Point to markdown: ", bold: true }, { text: "link to a page's .md version when one exists, as the spec recommends" }],
  [{ text: "Curated, not a sitemap: ", bold: true }, { text: "pick the ~50 best pages, so a best-first crawl instead of plain BFS" }],
  [{ text: "Stay current: ", bold: true }, { text: "sites change, so a daily ETag check and publish only on real changes" }],
  [{ text: "Crawl politely and safely: ", bold: true }, { text: "robots.txt, request limits, no private IPs" }],
  [{ text: "Simple to run: ", bold: true }, { text: "no API keys, SQLite, one Docker image" }],
  [{ text: "Check against reality: ", bold: true }, { text: "compare output with 5 published llms.txt files from llmstxt.site" }],
], 18).map((r) => ({ ...r, options: { ...r.options, paraSpaceAfter: 14 } })), 0.6, 1.55, 12.1, 5.2, { color: C.text1 });
s.addNotes("These were the requirements and constraints that shaped the design before writing code: the llms.txt spec itself, the idea that the file is a curated overview, keeping it fresh, crawling responsibly, and keeping deployment simple.");

// ---------- 4. Architecture ----------
pres.addSection({ title: "Architecture" });
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Architecture" });
s.addText("Architecture: the big picture", { placeholder: "title" });
function node(sl, label, sub, x, y, w, h, fill, fg) {
  box(sl, x, y, w, h, { fill, name: "node" });
  sl.addText([{ text: label, options: { bold: true, fontSize: 16, color: fg, breakLine: !!sub } },
    ...(sub ? [{ text: sub, options: { fontSize: 12, color: fg } }] : [])],
    { x, y, w, h, isTextBox: true, margin: 0.05, align: "center", valign: "middle", objectName: name("node-text") });
}
{
  const W = 1.6, H = 1.0;
  const A = 3.5, B = 5.6, Cc = 7.45;
  const R1 = 2.2, R2 = 3.95, R3 = 5.55;
  box(s, 3.25, 1.45, 5.95, 5.35, { fill: C.background2, line: C.accent3, dash: "dash", r: 0.08, name: "container" });
  text(s, "Docker container on Railway (single replica)", 3.45, 1.6, 5.5, 0.35, { fontSize: 12, bold: true, color: C.text2 });
  node(s, "User", "", 0.35, R1 + 0.1, 0.9, 0.8, C.accent3, C.background1);
  node(s, "React UI", "Vite + TS", 1.5, R1, 1.45, H, C.text1, C.background1);
  node(s, "FastAPI", "/api/generate · /s/{id}", A, R1, W, H, C.text1, C.background1);
  node(s, "Crawler", "best-first, 5 at a time", B, R1, W, H, C.accent1, C.background1);
  node(s, "Generator", "build · validate · quality", Cc, R2, 1.55, H, C.text1, C.background1);
  node(s, "Scheduler", "APScheduler · ETag check", B, R2, W, H, C.accent2, C.background1);
  node(s, "SQLite", "sites · versions · pages", A, R3, W, H, C.text2, C.background1);
  node(s, "Website", "robots · sitemaps · pages", 9.45, R1, 1.0, 1.2, C.background1, C.text1);
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 9.45, y: R1, w: 1.0, h: 1.2, rectRadius: 0.12, fill: { type: "none" }, line: { color: C.text1, width: 1.25 }, objectName: "website-outline" });
  arrow(s, 1.25, R1 + 0.5, 0.25, 0);
  arrow(s, 2.95, R1 + 0.5, A - 2.95, 0);
  arrow(s, A + W, R1 + 0.5, B - A - W, 0);
  arrow(s, B + W, R1 + 0.5, 9.45 - B - W, 0);
  arrow(s, B + W, R1 + H, Cc + 0.5 - B - W, R2 - R1 - H); // crawler -> generator
  arrow(s, B + W / 2, R1 + H, 0, R2 - R1 - H, { flipV: true }); // scheduler -> crawler
  arrow(s, A + W / 2, R1 + H, 0, R3 - R1 - H); // api -> sqlite
  arrow(s, A + W, R2 + H, B - A - W, R3 - R2 - H + 0.1, { flipH: true }); // scheduler -> sqlite
  const lab = (t, x, y, w, align = "left", h = 0.3) => text(s, t, x, y, w, h, { fontSize: 11, color: C.text2, align, italic: true });
  lab("URL", 2.95, R1 + 0.15, A - 2.95, "center");
  lab("crawl", A + W, R1 + 0.15, B - A - W, "center");
  lab("fetch", B + W, R1 + 0.15, 9.45 - B - W, "center");
  lab("pages", 7.7, R1 + 1.15, 0.8);
  lab("re-crawl on change", B + W / 2 - 1.75, R1 + 1.25, 1.65, "right");
  lab("save site\n+ version", A + W / 2 + 0.1, R2 + 0.2, 1.1, "left", 0.5);
  lab("due sites, ETags", B + 0.05, R3 - 0.45, 1.6);
  // tech stack panel
  box(s, 10.7, 1.45, 2.05, 5.35, { fill: C.text1, name: "stack-panel" });
  text(s, "Tech stack", 10.95, 1.65, 1.6, 0.4, { fontSize: 18, bold: true, color: C.accent1 });
  const stack = [
    ["Frontend", "React · Vite · TypeScript"],
    ["Backend", "Python 3.12 · FastAPI · httpx · BeautifulSoup"],
    ["Scheduling", "APScheduler, inside the app"],
    ["Storage", "SQLAlchemy + SQLite"],
    ["Deploy", "Docker · Railway"],
  ];
  text(s, stack.map(([k, v], i) => [
    { text: k, options: { fontSize: 12, bold: true, color: C.accent3, breakLine: true } },
    { text: v, options: { fontSize: 14, color: C.background1, paraSpaceAfter: 12, breakLine: i < stack.length - 1 } },
  ]).flat(), 10.95, 2.2, 1.6, 4.45);
}
s.addNotes("Walk the request path along the top row: user, React UI, FastAPI, crawler, target site. The generator builds and validates the file plus the quality report. The scheduler wakes up every 15 minutes, finds sites that are due, runs the ETag check and triggers a re-crawl on change. Everything is stored in one SQLite file. Tech stack on the right: React + Vite front end, FastAPI back end, APScheduler, SQLite, one Docker image on Railway.");

// ---------- 4a. Overall pseudocode ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Architecture" });
s.addText("The whole app in pseudocode", { placeholder: "title" });
{
  const pc = (items) => items.map(([t, lvl], i) => ({ text: t, options: {
    bullet: lvl ? { code: "2013", indent: 14 } : { indent: 14 }, indentLevel: lvl || 0, fontSize: 14,
    paraSpaceAfter: 3, breakLine: i < items.length - 1 } }));
  const card = (title, n, x, y, w, h, items, fill) => {
    box(s, x, y, w, h, { fill: fill ?? C.background2, name: "pseudo-card" });
    badge(s, String(n), x + 0.25, y + 0.2, 0.42);
    text(s, title, x + 0.8, y + 0.2, w - 1.0, 0.42, { fontSize: 17, bold: true, valign: "middle" });
    text(s, pc(items), x + 0.25, y + 0.78, w - 0.45, h - 0.9, { color: C.text2 });
  };
  card("User clicks Generate", 1, 0.6, 1.45, 6.1, 5.3, [
    ["Clean the URL; refuse invalid or private / internal addresses"],
    ["Read robots.txt: keep its rules and Sitemap lines"],
    ["Fetch the homepage (error if it fails); read site name and summary"],
    ["Collect scored candidates: homepage links and sitemap URLs"],
    ["Loop until 50 pages, 45s or no candidates are left:"],
    ["take the 5 highest-scoring and fetch them in parallel", 1],
    ["skip failed, non-HTML, duplicate and noindex pages", 1],
    ["save title, description and ETag; score its links as new candidates", 1],
    ["Link to .md versions where the page declares one"],
    ["Build llms.txt, check it against the spec, score its quality"],
    ["Show the file and quality report (Copy / Download)"],
  ]);
  card("Every 15 minutes (scheduler)", 2, 6.95, 1.45, 5.75, 2.95, [
    ["Find sites whose next check is due"],
    ["Last full crawl 7+ days ago → re-crawl"],
    ["Otherwise send the saved ETags, 5 at a time:"],
    ["all unchanged → next check in 24h · failed → retry in 6h", 1],
    ["something changed → re-crawl", 1],
    ["Same file → keep it; different → save a new version"],
  ]);
  card("Keep it up to date", 3, 6.95, 4.6, 2.8, 2.15, [
    ["Save site, pages, ETags and version 1"],
    ["Next check in 24h"],
  ], C.accent6);
  card("Stable link", 4, 9.95, 4.6, 2.75, 2.15, [
    ["GET /s/{id}/llms.txt returns the latest version"],
  ], C.accent6);
}
s.addNotes("This is section 2 of docs/code-walkthrough.md: the whole app end to end, without splitting it by file. Card 1 is the generate path, card 2 is the scheduler, card 3 is what happens when the user ticks 'keep it up to date', card 4 is the stable link.");

// ---------- 4b. Code map (from docs/code-walkthrough.md) ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Architecture" });
s.addText("Inside the code: how the files fit together", { placeholder: "title" });
{
  const bw = 1.75, bh = 0.62;
  const f = (label, x, y, fill, fg, outline) => {
    box(s, x, y, bw, bh, { fill, line: outline, name: "file" });
    text(s, label, x, y, bw, bh, { fontFace: "Courier New", fontSize: 14, bold: true, color: fg, align: "center", valign: "middle" });
  };
  const DARK = [C.text1, C.background1];
  f("App.tsx", 0.6, 3.5, ...DARK);
  f("main.py", 2.75, 3.5, ...DARK);
  f("generate.py", 4.95, 2.3, ...DARK);
  f("monitor.py", 4.95, 4.75, C.accent2, C.background1);
  f("db.py", 4.95, 6.05, C.text2, C.background1);
  f("llmstxt.py", 7.4, 1.5, ...DARK);
  f("quality.py", 7.4, 2.3, C.accent2, C.background1);
  f("crawler.py", 7.4, 3.85, C.accent1, C.background1);
  const helpers = [["1. urls.py", false], ["2. fetcher.py", true], ["3. extract.py", false], ["4. scoring.py", true], ["5. sitemaps.py", true]];
  helpers.forEach(([h, split], i) => {
    const y = 2.19 + i * 0.83;
    f(h, 10.95, y, split ? C.accent4 : C.background2, C.text1, split ? undefined : C.accent3);
    s.addShape(pres.shapes.LINE, { x: 9.15, y: Math.min(4.16, y + bh / 2), w: 1.8, h: Math.abs(y + bh / 2 - 4.16),
      flipV: y + bh / 2 < 4.16, line: { color: C.accent3, width: 1.25, endArrowType: "triangle" }, objectName: name("helper-arrow") });
  });
  const ar = (x1, y1, x2, y2, opts = {}) => arrow(s, Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1),
    { flipH: x2 < x1, flipV: y2 < y1, ...opts });
  ar(2.35, 3.81, 2.75, 3.81); // App -> main
  ar(4.5, 3.6, 4.95, 2.75); // main -> generate
  ar(4.5, 4.02, 4.95, 4.95); // main -> monitor
  ar(6.7, 2.61, 7.4, 2.61); // generate -> quality
  ar(6.7, 2.4, 7.4, 1.85); // generate -> llmstxt
  ar(6.7, 2.85, 7.4, 3.95); // generate -> crawler
  ar(6.7, 4.85, 7.4, 4.35); // monitor -> crawler
  ar(5.82, 4.75, 5.82, 2.92); // monitor -> generate
  ar(5.82, 5.37, 5.82, 6.05); // monitor -> db
  s.addShape(pres.shapes.LINE, { x: 3.62, y: 4.12, w: 0, h: 2.24, line: { color: C.text2, width: 1.75 }, objectName: "main-db-down" });
  ar(3.62, 6.36, 4.95, 6.36); // main -> db
  const lb = (t, x, y, w, align = "left") => text(s, t, x, y, w, 0.3, { fontSize: 11, italic: true, color: C.text2, align });
  lb("POST /api/generate", 1.6, 3.15, 1.9, "center");
  lb("monitor = true", 3.7, 4.5, 1.0, "right");
  lb("render", 5.9, 3.3, 0.8);
  lb("re-crawl", 6.85, 4.95, 0.8);
  lb("GET /s/{id}/llms.txt", 3.0, 6.45, 1.95, "center");
  // legend
  const leg = [[C.accent4, "Helpers split out of crawler.py"], [C.accent2, "Monitoring and quality report"], [C.accent1, "The crawl itself"]];
  leg.forEach(([c, t], i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: 1.6 + i * 0.42, w: 0.28, h: 0.28, rectRadius: 0.05, fill: { color: c }, line: { type: "none" }, objectName: name("legend") });
    text(s, t, 1.0, 1.58 + i * 0.42, 3.6, 0.32, { fontSize: 13, color: C.text2, valign: "middle" });
  });
  text(s, "Pure helpers (urls, extract, scoring, sitemaps, quality) do no network I/O, so they are easy to unit test.", 0.6, 4.6, 2.85, 1.3, { fontSize: 13, color: C.text2 });
}
s.addNotes("This is the module diagram from docs/code-walkthrough.md. The helpers on the right are numbered in the order the crawler first calls them: urls.py normalizes the start URL and checks it is public (SSRF), fetcher.py downloads robots.txt and the homepage, extract.py parses the homepage, scoring.py ranks the links it found, and sitemaps.py parses the sitemaps. Left half (generate.py and below) turns a URL into a file and never touches the database. monitor.py stores sites, checks them on a schedule, and re-runs the same crawl and render when something changed.");

// ---------- 5. Scheduler ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Architecture" });
s.addText("How the scheduler keeps files fresh", { placeholder: "title" });
text(s, "A cheap ETag check decides when to pay for a crawl; the file hash decides whether to publish.", 0.6, 1.35, 12.1, 0.5, { fontSize: 18, color: C.text2 });
const flow = [
  ["Tick every 15 min", "APScheduler finds up to 5 sites with next_check_at ≤ now"],
  ["ETag check", "Conditional GET (If-None-Match / If-Modified-Since) for homepage, sitemaps and listed pages, 5 at a time; stops at the first change"],
  ["Re-crawl if changed", "Any 200/404/410, or the last full crawl is over 7 days old"],
  ["Publish only if different", "New version saved only when sha256(llms.txt) changes, with a summary like “+3 pages, 2 descriptions changed”"],
];
flow.forEach(([h, b], i) => {
  const y = 2.15 + i * 1.18;
  badge(s, String(i + 1), 0.6, y + 0.1, 0.5, C.accent2);
  text(s, h, 1.3, y, 5.3, 0.4, { fontSize: 17, bold: true });
  text(s, b, 1.3, y + 0.4, 5.3, 0.75, { fontSize: 14, color: C.text2 });
});
// response table
const hdr = { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 14 };
const rows = [
  [{ text: "Response", options: hdr }, { text: "Meaning", options: hdr }],
  ["304 Not Modified", "Unchanged"],
  ["200, same ETag", "Unchanged (CDN ignored If-None-Match, seen on Cloudflare)"],
  ["200, new or no ETag", "Changed → re-crawl"],
  ["404 / 410", "Page removed → re-crawl"],
  ["Timeout / 5xx", "Check failed, not a change; retry in 6h"],
].map((r, i) => i === 0 ? r : r.map((c) => ({ text: c, options: { fontSize: 14, color: C.text1, fill: { color: i % 2 ? "FFFFFF" : HEX.lt2 } } })));
s.addTable(rows, { x: 7.0, y: 2.15, w: 5.7, colW: [2.0, 3.7], rowH: 0.5, border: { type: "solid", pt: 0.75, color: HEX.accent5 }, margin: 0.08, objectName: "etag-table" });
box(s, 7.0, 5.65, 5.7, 1.05, { fill: C.accent6, name: "failsafe" });
text(s, "No validators? Treated as changed (correct, just not cheap). 3 failures in a row → site marked failing.", 7.25, 5.7, 5.25, 0.95, { fontSize: 14, valign: "middle" });
s.addNotes("Default interval is 24 hours per site, so checks spread across the day. The 7-day full-crawl backstop catches pages the ETag check can't see. Failed crawls never overwrite the current version.");

// ---------- 6. Crawler loop ----------
pres.addSection({ title: "Crawler" });
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Crawler" });
s.addText("Crawler: best-first, 5 pages at a time", { placeholder: "title" });
text(s, "Always fetching the highest-scoring links means a full 50-page budget holds the best pages, not just the first ones.", 0.6, 1.35, 12.1, 0.5, { fontSize: 18, color: C.text2 });
// seed
node(s, "Collect candidates", "robots.txt · sitemaps · homepage nav", 0.6, 2.4, 2.6, 1.1, C.text1, C.background1);
arrow(s, 3.2, 2.95, 0.5, 0);
node(s, "Take top 5", "highest score first", 3.7, 2.4, 2.3, 1.1, C.accent1, C.background1);
arrow(s, 6.0, 2.95, 0.5, 0);
// parallel lanes
box(s, 6.5, 2.0, 2.6, 1.9, { fill: C.background2, line: C.accent3, dash: "dash", name: "gather" });
text(s, "asyncio.gather", 6.5, 2.05, 2.6, 0.3, { fontSize: 12, bold: true, color: C.text2, align: "center" });
for (let i = 0; i < 5; i++) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 6.75, y: 2.4 + i * 0.29, w: 2.1, h: 0.22, rectRadius: 0.05, fill: { color: C.accent1, transparency: i * 8 }, line: { type: "none" }, objectName: name("lane") });
  text(s, `GET page ${i + 1}`, 6.85, 2.4 + i * 0.29, 1.9, 0.22, { fontSize: 10, color: C.background1, valign: "middle", bold: true });
}
arrow(s, 9.1, 2.95, 0.5, 0);
node(s, "Extract + score links", "add new candidates, re-score known ones", 9.6, 2.4, 3.1, 1.1, C.text1, C.background1);
// loop back
s.addShape(pres.shapes.LINE, { x: 4.85, y: 4.15, w: 6.3, h: 0, line: { color: C.accent3, width: 1.5, dashType: "dash" }, objectName: "loop-line" });
s.addShape(pres.shapes.LINE, { x: 11.15, y: 3.5, w: 0, h: 0.65, line: { color: C.accent3, width: 1.5, dashType: "dash" }, objectName: "loop-up" });
arrow(s, 4.85, 3.5, 0, 0.65, { flipV: true, color: C.accent3 });
text(s, "repeat until a stop condition", 6.5, 4.2, 3.0, 0.3, { fontSize: 12, italic: true, color: C.text2, align: "center" });
// stat callouts
const stats = [["5", "concurrent requests per batch"], ["50", "pages max per crawl"], ["3", "clicks max depth"], ["45s", "total time budget"]];
stats.forEach(([n, l], i) => {
  const x = 0.6 + i * 3.08;
  box(s, x, 4.85, 2.8, 1.85, { name: "stat" });
  text(s, n, x + 0.3, 4.95, 2.3, 0.95, { fontSize: 48, bold: true, color: C.accent1, valign: "middle" });
  text(s, l, x + 0.3, 5.95, 2.3, 0.6, { fontSize: 14, color: C.text2 });
});
s.addNotes("Each round takes the 5 highest-scoring candidates and fetches them concurrently with asyncio.gather on one shared httpx client.");

// ---------- 7. Scoring ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Crawler" });
s.addText("How we score URLs", { placeholder: "title" });
text(s, "Every URL is scored when it's found, and re-scored each time another page links to it.", 0.6, 1.35, 12.1, 0.5, { fontSize: 18, color: C.text2 });
const sig = [
  ["Linked from homepage nav / header", 30],
  ["High-value keyword (docs, api, pricing, about…)", 20],
  ["Listed in a sitemap (+ priority × 10)", 15],
  ["Linked from a page body", 10],
  ["Linked from a footer", 5],
  ["Each extra page linking to it", 3],
  ["Each path segment deep", -8],
  ["Each page already saved from its section", -5],
];
const zeroX = 5.9, unit = 0.075;
sig.forEach(([l, v], i) => {
  const y = 2.1 + i * 0.5;
  text(s, l, 0.4, y, 4.3, 0.4, { fontSize: 14, valign: "middle", align: "right" });
  const w = Math.abs(v) * unit;
  s.addShape(pres.shapes.RECTANGLE, { x: v > 0 ? zeroX : zeroX - w, y: y + 0.06, w, h: 0.28, fill: { color: v > 0 ? C.accent2 : C.accent1 }, line: { type: "none" }, objectName: name("bar") });
  text(s, (v > 0 ? "+" : "−") + Math.abs(v), v > 0 ? zeroX + w + 0.1 : zeroX - w - 0.6, y, 0.5, 0.4, { fontSize: 14, bold: true, valign: "middle", align: v > 0 ? "left" : "right" });
});
s.addShape(pres.shapes.LINE, { x: zeroX, y: 2.05, w: 0, h: 4.05, line: { color: C.text2, width: 1 }, objectName: "zero-axis" });
// worked examples
box(s, 8.9, 2.1, 3.8, 1.95, { fill: C.background2, name: "ex1" });
text(s, "/docs/getting-started", 9.15, 2.25, 3.4, 0.35, { fontSize: 15, bold: true });
text(s, "nav +30 · sitemap +15 · keyword +20 · depth −16", 9.15, 2.65, 3.4, 0.6, { fontSize: 13, color: C.text2 });
text(s, "= 49", 9.15, 3.3, 3.4, 0.6, { fontSize: 28, bold: true, color: C.accent2 });
box(s, 8.9, 4.2, 3.8, 1.95, { fill: C.background2, name: "ex2" });
text(s, "/careers/senior-engineer", 9.15, 4.35, 3.4, 0.35, { fontSize: 15, bold: true });
text(s, "footer +5 · sitemap +15 · depth −16", 9.15, 4.75, 3.4, 0.6, { fontSize: 13, color: C.text2 });
text(s, "= 4", 9.15, 5.4, 3.4, 0.6, { fontSize: 28, bold: true, color: C.accent1 });
box(s, 0.6, 6.3, 12.1, 0.5, { fill: C.accent6, name: "cap" });
text(s, "The section penalty spreads the 50 pages out, so a huge /blog can't take them all. All weights are env-configurable.", 0.85, 6.3, 11.7, 0.5, { fontSize: 14, valign: "middle" });
s.addNotes("Candidates are sorted by score each round. The section penalty (−5 per page already saved from that section) stops any one section dominating. Known trade-off: best-first is greedy, so a deep page with no sitemap entry can be missed.");

// ---------- 8. Edge cases ----------
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Crawler" });
s.addText("Edge and error cases we handle", { placeholder: "title" });
const cards = [
  ["Scope and safety", ["Same host only; www and bare domain are one site", "Private and loopback IPs refused (SSRF)", "Subpath input stays under that path", "Cross-domain redirects dropped"]],
  ["Duplicate URLs", ["Strip #fragments and utm_, gclid, fbclid", "Sort query params, fold /index.html", "Each URL fetched once"]],
  ["Pages we skip", ["Login, cart, checkout, search, admin", "?page=2, /tag/, /category/, date archives", "Other-language copies (/fr/, /de/)", "PDFs, images, non-HTML, noindex"]],
  ["Politeness and limits", ["robots.txt rules", "10s per request, 5 redirects max", "2 MB body cap, 5 sitemap files max", "Identifying User-Agent"]],
  ["Failures", ["Homepage fails → clear error, no partial file", "A page that fails later is skipped", "45s budget → return what we have"]],
  ["Output quality", ["Boilerplate descriptions on 3+ pages removed", "One-page sections grouped into “Other”", "Sorted by score, then URL: deterministic", "Failed re-crawl never overwrites a version"]],
];
cards.forEach(([h, items], i) => {
  const col = i % 3, row = Math.floor(i / 3);
  const x = 0.6 + col * 4.1, y = 1.45 + row * 2.8;
  box(s, x, y, 3.9, 2.6, { fill: row === 1 && col === 1 ? C.accent6 : C.background2, name: "edge-card" });
  badge(s, String(i + 1), x + 0.25, y + 0.22, 0.42, row === 1 && col === 1 ? C.accent1 : C.text1);
  text(s, h, x + 0.8, y + 0.22, 2.9, 0.42, { fontSize: 17, bold: true, valign: "middle" });
  text(s, bullets(items, 14), x + 0.25, y + 0.8, 3.5, 1.75, { color: C.text2 });
});
s.addNotes("Highlight the failure handling card: the crawler never raises on a bad page; the fetcher returns error codes and that page is skipped. Tests use httpx.MockTransport fake sites so these cases run without the network.");

// ---------- 9. Next steps ----------
pres.addSection({ title: "Next steps" });
s = pres.addSlide({ masterName: "Title Only", sectionTitle: "Next steps" });
s.addText("Next steps", { placeholder: "title" });
const next = [
  ["JavaScript-rendered sites", "Headless browser fallback when server HTML is empty"],
  ["LLM-written summaries", "Better descriptions for pages with weak or missing meta tags"],
  ["llms-full.txt", "Full markdown content alongside the curated index"],
  ["Compare with existing /llms.txt", "Detect a site's own file and show the differences"],
  ["Change diffs and alerts", "Diff view between versions, email or webhook on change"],
  ["Scale out", "Postgres + a separate worker queue, so the app can run multiple replicas"],
];
next.forEach(([h, b], i) => {
  const col = i % 2, row = Math.floor(i / 2);
  const x = 0.6 + col * 6.15, y = 1.55 + row * 1.75;
  box(s, x, y, 5.95, 1.5, { name: "next-card" });
  badge(s, String(i + 1), x + 0.3, y + 0.5, 0.5);
  text(s, h, x + 1.05, y + 0.25, 4.7, 0.45, { fontSize: 18, bold: true });
  text(s, b, x + 1.05, y + 0.72, 4.7, 0.65, { fontSize: 14, color: C.text2 });
});
s.addNotes("Also worth mentioning: handling sites that block bots via CDN (403/429), and descriptions with live values that create a new version whenever they change.");

// ---------- 10. Close ----------
s = pres.addSlide({ masterName: "Dark Title", sectionTitle: "Next steps" });
s.addText("Thank you", { placeholder: "title" });
s.addText("Live app: web-production-905dcb88.up.railway.app\nCode: github.com/nmallekav/llms-txt-nishanth-mallekav", { placeholder: "body" });
badge(s, "", 0.8, 1.5, 0.35);
text(s, "Questions?", 1.3, 1.52, 6, 0.32, { fontSize: 16, color: C.background1, valign: "middle" });

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
