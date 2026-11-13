"""URL normalization, scoping and filtering. Pure functions, no network."""

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {"gclid", "fbclid", "ref", "mc_cid", "mc_eid", "msclkid", "yclid"}

SKIP_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".dmg", ".exe", ".pkg", ".deb",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom", ".txt",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav",
    ".woff", ".woff2", ".ttf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
}

UTILITY_SEGMENTS = {
    "login", "logout", "signin", "sign-in", "signup", "sign-up", "register",
    "cart", "checkout", "basket", "account", "my-account", "search", "admin",
    "wp-admin", "wp-login.php", "auth", "oauth", "password", "reset-password",
}

ARCHIVE_SEGMENTS = {"tag", "tags", "category", "categories", "author", "authors"}

# Common non-English language prefixes. Skipped unless the start URL is under one.
LANGUAGE_CODES = {
    "fr", "de", "es", "it", "pt", "ja", "ko", "zh", "ru", "nl", "sv", "pl", "tr",
    "ar", "he", "id", "vi", "th", "cs", "da", "fi", "no", "nb", "uk", "el", "hu",
    "ro", "hi", "bn", "ms", "fa",
}
LANGUAGE_RE = re.compile(r"^([a-z]{2})(?:[-_][a-z]{2,4})?$", re.IGNORECASE)

INDEX_FILE_RE = re.compile(r"/index\.(?:html?|php)$", re.IGNORECASE)
PAGE_NUMBER_RE = re.compile(r"^\d+$")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def normalize(url: str, base: str | None = None) -> str | None:
    """Return a canonical form of `url` (resolved against `base`), or None if not http(s)."""
    url = url.strip()
    if base:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None

    host = parts.hostname.lower()
    port = parts.port if parts.port and parts.port not in (80, 443) else None
    netloc = f"{host}:{port}" if port else host

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = INDEX_FILE_RE.sub("/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))

    return urlunsplit((scheme, netloc, path, query, ""))


def bare_host(host: str) -> str:
    """Host without a leading 'www.', so www and apex count as the same site."""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, site_host: str) -> bool:
    host = urlsplit(url).hostname or ""
    return bare_host(host) == bare_host(site_host)


def with_host(url: str, host: str) -> str:
    """Rewrite `url` to use `host` (used to fold www/apex variants into one form)."""
    parts = urlsplit(url)
    netloc = host if not parts.port else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def in_scope(url: str, scope_path: str) -> bool:
    """True if the URL's path is at or under scope_path (e.g. '/docs')."""
    if scope_path in ("", "/"):
        return True
    path = urlsplit(url).path
    return path == scope_path or path.startswith(scope_path.rstrip("/") + "/")


def path_segments(url: str, scope_path: str = "/") -> list[str]:
    """Path segments relative to the scope, e.g. /docs/api/auth under /docs -> ['api', 'auth']."""
    path = urlsplit(url).path
    if scope_path not in ("", "/") and path.startswith(scope_path):
        path = path[len(scope_path):]
    return [s for s in path.split("/") if s]


def section_of(url: str, scope_path: str = "/") -> str:
    """First path segment under the scope, or '' for top-level pages (they go in Main).

    Date-based blog paths (/2024/05/post) are grouped together as 'posts'.
    """
    segments = path_segments(url, scope_path)
    if len(segments) < 2:
        return ""
    return "posts" if YEAR_RE.match(segments[0]) else segments[0].lower()


def skip_reason(url: str, scope_path: str = "/") -> str | None:
    """Why this URL should not be crawled, or None if it is fine."""
    parts = urlsplit(url)
    path = parts.path.lower()
    segments = [s for s in path.split("/") if s]

    last = segments[-1] if segments else ""
    dot = last.rfind(".")
    if dot != -1 and last[dot:] in SKIP_EXTENSIONS:
        return "file"

    if any(s in UTILITY_SEGMENTS for s in segments):
        return "utility"
    if any(s in ARCHIVE_SEGMENTS for s in segments):
        return "archive"

    # Pagination: /page/2, ?page=2, ?p=3
    for i, s in enumerate(segments[:-1]):
        if s == "page" and PAGE_NUMBER_RE.match(segments[i + 1]) and segments[i + 1] != "1":
            return "pagination"
    query = dict(parse_qsl(parts.query))
    for key in ("page", "p", "paged"):
        if key in query and query[key] not in ("", "1"):
            return "pagination"

    # Date archives: /2024/ or /2024/05 as the whole path
    if segments and YEAR_RE.match(segments[0]) and len(segments) <= 2 and all(s.isdigit() for s in segments):
        return "archive"

    # Other-language copies, unless the scope itself is under a language prefix
    scope_segments = [s for s in scope_path.lower().split("/") if s]
    scope_is_lang = bool(scope_segments) and _is_language(scope_segments[0])
    if segments and not scope_is_lang and _is_language(segments[0]):
        return "language"

    return None


def _is_language(segment: str) -> bool:
    match = LANGUAGE_RE.match(segment)
    return bool(match) and match.group(1).lower() in LANGUAGE_CODES


def ensure_scheme(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def is_public_host(host: str) -> bool:
    """False if the host resolves to a private, loopback, link-local or reserved address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True
