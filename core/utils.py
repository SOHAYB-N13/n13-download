"""Shared utilities.

URL *knowledge* (detection, normalisation, identity, filename hygiene) lives
in :mod:`core.urls`; the names that historically lived here are re-exported so
existing ``from core.utils import ...`` callers keep working.  What stays here
is response-header handling and the generic formatters.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from core.urls import (  # noqa: F401  (re-exported for backwards compatibility)
    INVALID_CHARS,
    MIME_TO_EXT,
    ext_for_content_type as _ext_from_content_type,
    looks_like_filename as _looks_like_filename,
    normalize_url,
    sanitize_filename,
    url_filename,
)


def get_filename_from_response(headers: dict, url: str, final_url: str | None = None) -> str:
    """Resolve a filename from response headers, then the URL.

    Priority: ``Content-Disposition`` (the server's explicit answer) → the
    final URL after redirects → the original URL → a Content-Type extension →
    a safe generated name.  The URL half of the decision is delegated to
    :func:`core.urls.url_filename`, so the frontend's display name and the
    engine's saved filename come from one implementation.
    """
    resolved = final_url or url
    ct = headers.get("content-type", "") or headers.get("Content-Type", "")
    ext_hint = _ext_from_content_type(ct)

    # 1) Content-Disposition — strongest signal.  RFC 5987 extended form first.
    cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^\s;]+)", cd, re.I)
    if match:
        name = sanitize_filename(unquote(match.group(1)))
        if _looks_like_filename(name):
            return name
    match = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', cd, re.I)
    if match:
        name = sanitize_filename(match.group(1).strip())
        if _looks_like_filename(name):
            return name

    # 2) The URL itself (final URL after redirects wins over the original).
    for cand in (resolved, url):
        name = url_filename(cand)
        if not name:
            continue
        if _looks_like_filename(name):
            return name
        # 3) A path segment with no extension — append the Content-Type hint
        # so the file is still openable.
        if ext_hint and name not in {"downloaded_file"}:
            return name + ext_hint

    # 4) Generic fallback — use the Content-Type extension when we have one.
    if ext_hint:
        return "download" + ext_hint

    return "downloaded_file"


def validate_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except (ValueError, TypeError):
        return False


def format_size(size_bytes: int) -> str:
    if size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.2f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / (1024**2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def human_size(value: float) -> str:
    """Byte count as a short display string for the graphical UI.

    The GUI's formatter, alongside :func:`format_size` (the console's).  Both live
    here so a third copy never appears: ``core/store.py`` used to carry its own
    private duplicate, which put display formatting inside a persistence module.

    Returns an em dash rather than a fabricated "0 B" when the value is not a
    number — an unknown size and a zero-byte file are different things.
    """
    try:
        size = max(0.0, float(value))
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024.0 or unit == "PB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_speed(speed_bps: float) -> str:
    if speed_bps >= 1024**3:
        return f"{speed_bps / (1024**3):.2f} GB/s"
    if speed_bps >= 1024**2:
        return f"{speed_bps / (1024**2):.1f} MB/s"
    if speed_bps >= 1024:
        return f"{speed_bps / 1024:.1f} KB/s"
    return f"{speed_bps:.0f} B/s"


def detect_hash_algorithm(expected_hash: str) -> str:
    h = expected_hash.strip().lower()
    if len(h) == 32 and re.fullmatch(r"[0-9a-f]{32}", h):
        return "md5"
    if len(h) == 64 and re.fullmatch(r"[0-9a-f]{64}", h):
        return "sha256"
    raise ValueError("Hash must be 32 (MD5) or 64 (SHA256) hex characters")


def calculate_checksum(filepath: str | Path, algorithm: str = "sha256") -> str:
    hasher = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_rename(src: str | Path, dst: str | Path) -> None:
    try:
        Path(src).replace(dst)
    except OSError:
        shutil.move(str(src), str(dst))


def is_valid_directory(path: str | Path) -> bool:
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p.is_dir()
    except OSError:
        return False


def unique_filepath(directory: Path, filename: str) -> Path:
    """Avoid overwriting existing files by appending a numeric suffix."""
    base = directory / filename
    if not base.exists():
        return base
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def origin_from_url(url: str) -> str | None:
    """Return the scheme://host[:port] origin, used as a Referer hint."""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/"
    except (ValueError, TypeError):
        pass
    return None


# Some file hosts / CDNs require a specific Referer (usually the marketing
# site, not the CDN origin) or they hand back a tiny HTML "download not
# complete" / landing page instead of the real binary. Map known CDN hosts to
# the Referer a browser would send when clicking the download from that site.
#
# Keys are matched as exact hostnames or as ".suffix" to cover subdomains.
REFERER_OVERRIDES = {
    # AMD drivers are served by Akamai and REQUIRE an amd.com referer.
    "drivers.amd.com": "https://www.amd.com/",
    "download.amd.com": "https://www.amd.com/",
    # NVIDIA also gates some downloads behind an nvidia.com referer.
    "us.download.nvidia.com": "https://www.nvidia.com/",
    "download.nvidia.com": "https://www.nvidia.com/",
    "international.download.nvidia.com": "https://www.nvidia.com/",
    # SourceForge forces a "use a mirror" flow unless the referer is set.
    "sourceforge.net": "https://sourceforge.net/",
    "downloads.sourceforge.net": "https://sourceforge.net/",
    # GitHub release assets are fine with origin, but be explicit.
    "github.com": "https://github.com/",
    "objects.githubusercontent.com": "https://github.com/",
    "release-assets.githubusercontent.com": "https://github.com/",
    # Softpedia / majorgeeks-style hubs gate direct CDN links.
    "download.softpedia.com": "https://www.softpedia.com/",
}


def best_referer_for(url: str) -> str | None:
    """Pick the Referer a real browser would send for this URL.

    Falls back to the URL's own origin when no override is known. This matters
    for hosts that block "no-referer" requests with an HTML interstitial.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, TypeError):
        host = ""
    if host in REFERER_OVERRIDES:
        return REFERER_OVERRIDES[host]
    # Subdomain match against .suffix entries (if any added later).
    for suffix, ref in REFERER_OVERRIDES.items():
        if suffix.startswith(".") and host.endswith(suffix):
            return ref
    return origin_from_url(url)


def is_html_error_response(content_type: str, url_path: str, content_disposition: str = "") -> bool:
    """Heuristic: is this response an HTML interstitial instead of the file?

    Used to reject the classic "Download Not Complete" / landing-page pages
    that protected CDNs return when they don't like the request headers.

    A ``text/html`` body is treated as an interstitial **unless the server
    explicitly named an HTML document** — via ``Content-Disposition`` or a
    ``.html`` / ``.htm`` / ``.xhtml`` URL path.  Those are real downloads the
    user asked for, and refusing them made it impossible to fetch a web page.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in ("text/html", "application/xhtml+xml"):
        return False
    if re.search(r"\.html?['\"]?\s*$", (content_disposition or "").strip(), re.I):
        return False
    path = (url_path or "").split("?")[0].split("#")[0].lower()
    if path.endswith((".html", ".htm", ".xhtml")):
        return False
    return True


def build_browser_headers(
    url: str,
    user_agent: str,
    *,
    range_header: str | None = None,
    accept: str = "*/*",
) -> dict:
    """Build a realistic browser-like header set.

    Many CDNs / file hosts (Cloudflare, MediaFire-style, etc.) reject requests
    that lack common browser headers, so we send a complete set. Referer is
    derived from the URL origin which is usually accepted.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",  # prevent gzip on binary streams
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    referer = best_referer_for(url)
    if referer:
        headers["Referer"] = referer
        try:
            parsed = urlparse(referer)
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        except (ValueError, TypeError):
            pass
    if range_header:
        headers["Range"] = range_header
    return headers
