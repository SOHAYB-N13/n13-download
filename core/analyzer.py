"""Smart URL analysis for the ANALYZING task state.

Wraps :func:`core.probe.probe_with_headers` and distils the result into the
metadata a :class:`core.task.DownloadTask` needs before it starts:
filename, size, content type, server, range support, ETag, Last-Modified,
HTTP status, and whether the response looks authenticated.  Runs once, in the
queue worker thread — never on the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Optional
from urllib.parse import urlparse

from core.utils import get_filename_from_response, is_html_error_response

if TYPE_CHECKING:
    from config.settings import AppConfig
    from core.session import SessionManager


@dataclass
class Analysis:
    """Structured result of a pre-download URL analysis."""

    ok: bool = False
    total_size: int = 0
    supports_range: bool = False
    filename: str = ""
    content_type: str = ""
    server: str = ""
    etag: str = ""
    last_modified: str = ""
    status: int = 0
    auth_required: bool = False
    checksum_available: bool = False
    error: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "size": int(self.total_size or 0),
            "range": bool(self.supports_range),
            "filename": self.filename or "",
            "content_type": self.content_type or "",
            "server": self.server or "",
            "etag": self.etag or "",
            "last_modified": self.last_modified or "",
            "status": int(self.status or 0),
            "auth_required": bool(self.auth_required),
            "checksum_available": bool(self.checksum_available),
            "error": self.error or "",
        }


def _classify_content_type(content_type: str, url_path: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct


def analyze_url(
    url: str,
    config: "AppConfig",
    session_manager: "SessionManager",
    timeout: int = 20,
) -> Analysis:
    """Analyse *url* in a worker thread and return an :class:`Analysis`."""
    from core.probe import probe_with_headers

    result = Analysis()
    try:
        reachable, total, supports, filename, err, headers = probe_with_headers(
            url, config, session_manager, timeout
        )
    except Exception as exc:  # never leak a traceback to the UI
        result.error = str(exc)
        return result

    if not reachable:
        result.error = err or "Analysis failed"
        return result

    result.ok = True
    result.total_size = int(total or 0)
    result.supports_range = bool(supports)
    result.filename = filename or get_filename_from_response(
        headers, url, url
    )
    result.headers = headers
    result.etag = (headers.get("ETag") or headers.get("etag") or "").strip()
    result.last_modified = (
        headers.get("Last-Modified") or headers.get("last-modified") or ""
    ).strip()
    result.server = (headers.get("Server") or headers.get("server") or "").strip()

    ct = _classify_content_type(
        headers.get("Content-Type", ""), urlparse(url).path
    )
    result.content_type = ct

    # 401/403 or a WWW-Authenticate header => auth required.
    www_auth = headers.get("WWW-Authenticate") or headers.get("www-authenticate")
    if www_auth:
        result.auth_required = True
        result.status = 401

    # Some hosts expose a checksum/size in headers (rare, but harmless to note).
    x_checksum = (
        headers.get("X-Checksum-MD5")
        or headers.get("X-Checksum-Sha256")
        or headers.get("X-File-MD5")
        or ""
    )
    result.checksum_available = bool(x_checksum)

    # Guard against HTML interstitials that slipped through the probe.
    if is_html_error_response(result.content_type, urlparse(url).path):
        result.ok = False
        result.error = (
            "Server returned an HTML page instead of the file "
            "(the link may be expired, region-blocked, or require a browser session)."
        )
    return result


DEFAULT_CATEGORY_EXTENSIONS = {
    # Videos
    "Videos": ["mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts",
               "mpeg", "mpg", "3gp", "wmv", "m2ts"],
    # Music
    "Music": ["mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma", "mid"],
    # Images
    "Images": ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico",
               "tiff", "heic", "avif", "jfif"],
    # Documents
    "Documents": ["pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx",
                  "ppt", "pptx", "csv", "md", "epub", "odp", "ods"],
    # Archives
    "Archives": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso",
                 "tgz", "tbz2", "cab", "zst"],
    # Programs
    "Programs": ["exe", "msi", "apk", "dmg", "deb", "rpm", "jar", "pkg",
                 "appimage", "bat", "sh"],
    # Other
    "Other": ["bin", "dat", "db", "dll", "so", "dylib", "img", "part", "torrent"],
}


def detect_category(
    filename: str,
    content_type: str = "",
    ext_map: Optional[Dict[str, list]] = None,
) -> str:
    """Map a filename/content-type to a download category.

    ``ext_map`` optionally overrides the built-in extension mapping (used by
    the configurable ``category_extensions`` setting).  The effective map is
    the override merged over the defaults.
    """
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""

    ext_map = ext_map or {}
    merged: Dict[str, list] = {cat: list(exts) for cat, exts in DEFAULT_CATEGORY_EXTENSIONS.items()}
    for cat, exts in ext_map.items():
        custom = [str(e).lower().lstrip(".") for e in exts]
        base = merged.get(cat, [])
        for e in custom:
            if e and e not in base:
                base.append(e)
        merged[cat] = base
    # Order: most specific category wins (dict insertion order).
    for cat, exts in merged.items():
        if ext and ext in exts:
            return cat

    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("video/"):
        return "Videos"
    if ct.startswith("audio/"):
        return "Music"
    if ct.startswith("image/"):
        return "Images"
    if ct in ("application/pdf", "text/plain", "text/csv", "text/html", "text/xml"):
        return "Documents"
    if ct in (
        "application/zip", "application/x-7z-compressed", "application/x-rar-compressed",
        "application/x-tar", "application/gzip", "application/x-bzip2", "application/x-xz",
    ):
        return "Archives"
    if ct in ("application/x-msdownload", "application/x-msi",
              "application/vnd.android.package-archive"):
        return "Programs"
    if ct:
        return "Other"
    return "General"
