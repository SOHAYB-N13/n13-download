"""Link detection, URL normalisation and resource identity.

Three concerns live here on purpose, because they have *opposite* failure
modes and mixing them is what makes URL handling unreliable:

* **Detection** — :func:`extract_urls` finds candidate links inside arbitrary
  text (clipboard payloads, pasted lists, HTML).  It must never *invent* a
  link, so by default it recognises only explicit ``http(s)://`` URLs.  A
  sentence such as ``"unzip file.zip first"`` must not yield ``file.zip``.
* **Normalisation** — :func:`normalize_url` repairs what a user typed into
  something requestable.  It *does* accept a bare domain (``example.com/x``),
  because a single-field input carries no ambiguity about intent.
* **Identity** — :func:`canonical_url` / :func:`same_resource` answer "are
  these two URLs the same resource?" for duplicate detection.  They normalise
  only the parts that can never change what is served (scheme and host case,
  the default port, the fragment) and leave the query string untouched:
  ``?id=1`` and ``?id=2`` are different files, and a signed URL's token must
  not be normalised away or two distinct links would collide.

Nothing in this module raises on malformed input.  A link the engine cannot
parse is returned unchanged (or ``""`` from detection) so a bad paste can
never take a download down.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import parse_qsl, unquote, urlparse, urlunparse

__all__ = [
    "extract_urls",
    "first_url",
    "normalize_url",
    "trim_url",
    "url_filename",
    "canonical_url",
    "same_resource",
    "is_http_url",
    "looks_like_filename",
    "sanitize_filename",
    "INVALID_CHARS",
    "MIME_TO_EXT",
    "ext_for_content_type",
]

# ---------------------------------------------------------------------------
# Filename hygiene (shared with core.utils, which re-exports these)
# ---------------------------------------------------------------------------

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Extension lookup for common MIME types — used as a last-resort hint when the
# server gives no filename (common with dynamic / download.php?id=... links).
MIME_TO_EXT = {
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-bzip2": ".bz2",
    "application/x-xz": ".xz",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/octet-stream": "",
    "application/x-msdownload": ".exe",
    "application/x-msi": ".msi",
    "application/vnd.android.package-archive": ".apk",
    "application/x-iso9660-image": ".iso",
    "application/x-shockwave-flash": ".swf",
    "application/java-archive": ".jar",
    "application/x-tfont": ".ttf",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/x-rpm": ".rpm",
    "application/x-debian-package": ".deb",
    "application/x-dmg": ".dmg",
    "application/x-apple-diskimage": ".dmg",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "text/xml": ".xml",
    "application/json": ".json",
    "application/xml": ".xml",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-flv": ".flv",
    "video/mpeg": ".mpeg",
    "video/3gpp": ".3gp",
}


def sanitize_filename(name: str) -> str:
    """Make *name* safe to join onto a directory.

    Strips every separator and reserved character, so the result can never
    escape its destination folder, and never returns an empty string.
    """
    return INVALID_CHARS.sub("_", name or "").strip(". ") or "downloaded_file"


def looks_like_filename(name: str) -> bool:
    """True when *name* is usable as a file name *with a real extension*.

    Used to decide whether a URL segment or a ``Content-Disposition`` value is
    a filename at all, so ``/download/`` never becomes the name of a file.
    """
    name = (name or "").strip()
    if not name or name in {".", ".."}:
        return False
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return False
    return bool(stem) and bool(ext) and re.fullmatch(r"[A-Za-z0-9]+", ext) is not None


def ext_for_content_type(content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    return MIME_TO_EXT.get(ct, "")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# A scheme URL runs until whitespace or a character that cannot appear raw in
# one.  Quotes and angle brackets are delimiters in every realistic context
# (HTML attributes, markdown links, chat text), so they terminate the match.
_URL_SCHEME_RE = re.compile(r"""(?i)\bhttps?://[^\s<>"'`\\]+""")

# Bare domains are opt-in only (see extract_urls).  The lookbehind keeps
# ``user@example.com`` and ``1.2.3.4``-style tokens out.
_BARE_DOMAIN_RE = re.compile(
    r"""(?i)(?<![\w@.\-/])"""
    r"""(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"""
    r"""(?::\d{1,5})?"""
    r"""(?:/[^\s<>"'`\\]*)?"""
)

_HTML_ENTITY_RE = re.compile(r"&(amp|#0*38|lt|gt|quot|apos|#0*39|#x0*27);", re.I)
_HTML_ENTITY_MAP = {
    "amp": "&",
    "#38": "&",
    "#038": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "#39": "'",
    "#039": "'",
    "#x27": "'",
}

# Sentence punctuation that is never part of a link when it is the last
# character.  ``?`` is included: a trailing query separator serves no file.
_TRAILING_JUNK = ".,;:!?"
# Closing brackets that belong to the surrounding prose unless the URL itself
# opened one — ``https://en.wikipedia.org/wiki/Foo_(bar)`` must keep its ``)``.
_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


def _decode_html_entities(text: str) -> str:
    """Turn ``&amp;`` back into ``&`` before detection.

    An HTML attribute ``href="...?a=1&amp;b=2"`` is the single most common way
    a real download URL gets corrupted: without decoding, the query parameter
    becomes ``amp;b`` and the signed link fails.  Only the entities that can
    appear inside a URL are decoded.
    """
    return _HTML_ENTITY_RE.sub(
        lambda m: _HTML_ENTITY_MAP.get(m.group(1).lower(), m.group(0)), text
    )


def trim_url(candidate: str) -> str:
    """Drop the punctuation a link picked up from the text around it.

    Public because the clipboard monitor applies the same trimming to a whole
    line before deciding whether that line *is* a link (see
    :func:`core.clipboard.extract_url`).
    """
    url = candidate
    while url:
        before = url
        while url and url[-1] in _TRAILING_JUNK:
            url = url[:-1]
        while url and url[-1] in _BRACKET_PAIRS:
            closer = url[-1]
            if url.count(closer) > url.count(_BRACKET_PAIRS[closer]):
                url = url[:-1]
            else:
                break
        if url == before:
            break
    return url


# Kept for internal readability; the public name is trim_url.
_trim_url = trim_url


def is_http_url(url: str) -> bool:
    """True for an absolute ``http(s)`` URL with a host."""
    try:
        parsed = urlparse(url or "")
    except (ValueError, TypeError):
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def extract_urls(
    text: Optional[str],
    *,
    allow_bare_domains: bool = False,
    max_urls: int = 0,
) -> List[str]:
    """Every distinct ``http(s)`` URL found in *text*, in order of appearance.

    Handles the shapes real pastes actually arrive in: URLs wrapped in quotes,
    backticks, parentheses or angle brackets; URLs embedded in prose; several
    URLs on one line; HTML-escaped query strings; percent-encoded and Unicode
    paths; and URLs with no file extension at all.

    ``allow_bare_domains`` additionally recognises scheme-less domains such as
    ``example.com/x.zip``.  It is **off by default**: in free text the token
    ``file.zip`` is far more likely to be a filename than a host, and inventing
    a download from prose is worse than missing one.  ``max_urls`` truncates
    the result (``0`` means no limit).
    """
    if not text:
        return []
    decoded = _decode_html_entities(str(text))
    found: List[str] = []
    seen: set = set()

    def _add(raw: str) -> None:
        url = normalize_url(_trim_url(raw))
        if not is_http_url(url):
            return
        key = canonical_url(url)
        if key in seen:
            return
        seen.add(key)
        found.append(url)

    for match in _URL_SCHEME_RE.finditer(decoded):
        _add(match.group(0))

    if allow_bare_domains:
        # Blank out the scheme URLs first, otherwise their own host would be
        # matched a second time as a "bare domain".
        remainder = _URL_SCHEME_RE.sub(" ", decoded)
        for match in _BARE_DOMAIN_RE.finditer(remainder):
            _add(match.group(0))

    if max_urls and len(found) > max_urls:
        return found[:max_urls]
    return found


def first_url(text: Optional[str], **kwargs) -> Optional[str]:
    """The first URL in *text*, or ``None`` when there is none."""
    urls = extract_urls(text, max_urls=1, **kwargs)
    return urls[0] if urls else None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)
_BARE_DOMAIN_HEAD_RE = re.compile(
    r"^[a-z0-9.\-]+\.[a-z]{2,}(?::\d{1,5})?(?:[/?#]|$)", re.I
)


def normalize_url(url: str) -> str:
    """Repair a user-supplied link into something requestable.

    Trims whitespace and the quotes/brackets that come with copy-paste, turns
    a scheme-relative ``//host/x`` into https, and prefixes ``https://`` to a
    bare domain.  It never rewrites a path or a query string, so a signed URL
    passes through byte-for-byte.
    """
    cleaned = (url or "").strip().strip("\"'`<>").strip()
    if not cleaned:
        return ""
    lower = cleaned.lower()
    if lower.startswith(("http://", "https://", "ftp://")):
        return cleaned
    if cleaned.startswith("//"):
        return "https:" + cleaned
    if _BARE_DOMAIN_HEAD_RE.match(lower) or lower.startswith("www."):
        return "https://" + cleaned
    return cleaned


# ---------------------------------------------------------------------------
# Filename from a URL
# ---------------------------------------------------------------------------

# Query parameters that conventionally carry a filename.  Kept short and
# explicit: guessing from an arbitrary parameter would name files after ids.
_FILENAME_PARAMS = frozenset(
    {"file", "filename", "file_name", "name", "download", "dl", "f",
     "attachment", "fn", "document"}
)


def _filename_from_query(query: str) -> str:
    """A filename carried in the query string (``?file=setup.exe``)."""
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=False)
    except ValueError:
        return ""
    for key, value in pairs:
        if key.lower() in _FILENAME_PARAMS:
            name = sanitize_filename(unquote(value))
            if looks_like_filename(name):
                return name
    return ""


def url_filename(url: str) -> str:
    """Best-effort file name from a URL alone, without any response headers.

    Priority: the last path segment, then a filename carried in the query
    string, then the host.  Returns ``""`` only when the URL has neither a
    usable path nor a host.
    """
    try:
        parsed = urlparse(url or "")
    except (ValueError, TypeError):
        return ""

    path = unquote(parsed.path or "")
    trimmed = path.rstrip("/")
    if trimmed:
        segment = trimmed.rsplit("/", 1)[-1]
        name = sanitize_filename(segment)
        # A path segment is authoritative even without an extension
        # (``/download`` names the file "download"); only the generic
        # placeholder is rejected.
        if name and name != "downloaded_file":
            return name

    query_name = _filename_from_query(parsed.query or "")
    if query_name:
        return query_name

    host = (parsed.hostname or "").strip()
    if host:
        return host
    return ""


# ---------------------------------------------------------------------------
# Identity (duplicate detection)
# ---------------------------------------------------------------------------

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(url: str) -> str:
    """A stable identity key for duplicate detection.

    Normalises only what can never change the bytes served: the scheme and
    host case, the default port, and the fragment.  The path, parameters and
    query are preserved exactly, so two signed URLs with different tokens stay
    distinct and a re-download of ``?id=1`` is not mistaken for ``?id=2``.

    An unparseable URL is returned trimmed, so it still compares equal to
    itself and never silently matches something else.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except (ValueError, TypeError):
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return raw
    if ":" in host:  # IPv6 literal — urlparse strips the brackets.
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None and port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def same_resource(a: str, b: str) -> bool:
    """True when two URLs name the same resource for duplicate purposes."""
    key_a = canonical_url(a)
    return bool(key_a) and key_a == canonical_url(b)
