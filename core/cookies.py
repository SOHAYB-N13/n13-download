"""Cookie resolution for the download engine.

Cookies can come from three sources, tried in priority order:

1. A raw ``Cookie`` header string (``cookies`` config field).
2. A Netscape/Mozilla cookies.txt file (``cookie_file`` config field).
3. A live browser profile (``browser_cookies`` config field).

All browser-cookie loading is optional and lazily imported so that the core
download engine keeps working even when ``browser_cookie3`` is not installed.
"""

from __future__ import annotations

from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path
from typing import Optional

from config.settings import AppConfig


def _load_netscape_file(path: Path) -> Optional[CookieJar]:
    """Load a Netscape-format cookies.txt into a CookieJar."""
    if not path.exists():
        return None
    jar = MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def _load_browser_cookies(browser: str) -> Optional[CookieJar]:
    """Import cookies from a browser profile using ``browser_cookie3``.

    Returns ``None`` if the library is missing or loading fails, so the
    download still proceeds without cookies rather than crashing.
    """
    try:
        import browser_cookie3 as bc
    except ImportError:
        return None

    name = (browser or "").strip().lower()
    try:
        if name in ("chrome", "chromium"):
            return bc.chrome()
        if name == "edge":
            return bc.edge()
        if name == "firefox":
            return bc.firefox()
        if name == "opera":
            return bc.opera()
        if name == "brave":
            return bc.brave()
    except Exception:
        return None
    return None


def resolve_cookie_jar(config: AppConfig) -> Optional[CookieJar]:
    """Build a CookieJar from the active configuration, if any cookies set."""
    if config.cookie_file:
        jar = _load_netscape_file(Path(config.cookie_file).expanduser())
        if jar is not None:
            return jar
    if config.browser_cookies:
        jar = _load_browser_cookies(config.browser_cookies)
        if jar is not None:
            return jar
    return None


def cookie_header_from_config(config: AppConfig) -> Optional[str]:
    """Return a raw Cookie header value when the user set one directly."""
    cookies = (config.cookies or "").strip()
    return cookies or None
