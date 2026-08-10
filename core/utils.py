"""Shared utilities."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

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
    return INVALID_CHARS.sub("_", name).strip(". ") or "downloaded_file"


def _looks_like_filename(name: str) -> bool:
    """A token counts as a usable filename only if it has an extension."""
    name = name.strip()
    if not name or name in {".", ".."}:
        return False
    # must contain a dot that is not leading/trailing and is followed by alnum
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return False
    return bool(stem) and bool(ext) and re.fullmatch(r"[A-Za-z0-9]+", ext) is not None


def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    return MIME_TO_EXT.get(ct, "")


def get_filename_from_response(headers: dict, url: str, final_url: str | None = None) -> str:
    resolved = final_url or url

    # 1) Content-Disposition (RFC 5987 extended) — strongest signal
    cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
    match = re.search(r"filename\*\s*=\s*UTF-8''([^\s;]+)", cd, re.I)
    if match:
        name = sanitize_filename(unquote(match.group(1)))
        if _looks_like_filename(name):
            return name
    match = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', cd, re.I)
    if match:
        name = sanitize_filename(match.group(1).strip())
        if _looks_like_filename(name):
            return name

    # 2) Last path segment of the final URL (after redirects)
    candidates = [resolved, url]
    ct = headers.get("content-type", "") or headers.get("Content-Type", "")
    ext_hint = _ext_from_content_type(ct)
    for cand in candidates:
        path = urlparse(cand).path
        raw = path.split("/")[-1]
        if raw:
            name = sanitize_filename(unquote(raw))
            if _looks_like_filename(name):
                return name

    # 3) Path had a name but no extension — append Content-Type extension
    for cand in candidates:
        path = urlparse(cand).path
        raw = path.split("/")[-1]
        if raw:
            name = sanitize_filename(unquote(raw))
            if name and name not in {"downloaded_file"} and ext_hint:
                return name + ext_hint

    # 4) Generic fallback — use Content-Type extension so the file is at least usable
    if ext_hint:
        return "download" + ext_hint

    return "downloaded_file"


def normalize_url(url: str) -> str:
    """Trim surrounding whitespace/quotes that come from copy-paste.

    Also auto-prefix https:// when a user pastes a bare domain such as
    "example.com/file.zip" or "www.example.com/x".
    """
    cleaned = (url or "").strip().strip("\"'`").strip()
    if not cleaned:
        return ""
    lower = cleaned.lower()
    if lower.startswith(("http://", "https://", "ftp://")):
        return cleaned
    # scheme-relative
    if cleaned.startswith("//"):
        return "https:" + cleaned
    # bare domain / path
    if re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}(/|$)", lower) or lower.startswith("www."):
        return "https://" + cleaned
    return cleaned


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
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    # A real file almost never arrives with an explicit text/html type.
    if ct in ("text/html", "application/xhtml+xml"):
        # ...unless the server explicitly named a .html file via disposition,
        # in which case we trust the user's URL intent.
        return True
    return False


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
