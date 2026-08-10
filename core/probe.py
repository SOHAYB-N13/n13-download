"""URL probing with multi-strategy fallback (HEAD -> Range GET -> full GET)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple
from urllib.parse import urlparse

import requests

from core.security import validate_download_url
from core.utils import (
    build_browser_headers,
    get_filename_from_response,
    is_html_error_response,
)

if TYPE_CHECKING:
    from config.settings import AppConfig
    from core.session import SessionManager


def _parse_total_from_response(r: requests.Response) -> Tuple[int, bool]:
    """Return (total_size, supports_range) extracted from a response."""
    supports_range = r.status_code == 206
    total_size = 0

    if supports_range:
        cr = r.headers.get("Content-Range", "")
        if cr.startswith("bytes"):
            try:
                total_size = int(cr.split("/")[1])
            except (IndexError, ValueError):
                pass

    if total_size == 0:
        cl = r.headers.get("Content-Length")
        if cl:
            try:
                total_size = int(cl)
            except ValueError:
                pass

    if not supports_range:
        # Accept-Ranges can contain multiple comma-separated values
        # (e.g. "bytes, bytes" from CDN/origin concatenation).
        # Check each token individually.
        ar = r.headers.get("Accept-Ranges", "")
        for token in ar.lower().split(","):
            if token.strip() == "bytes":
                supports_range = True
                break

    return total_size, supports_range


def probe_url(
    url: str,
    config: "AppConfig",
    session_manager: "SessionManager",
    timeout: int = 20,
) -> Tuple[bool, int, bool, str, str]:
    ok, err = validate_download_url(url, block_private=config.block_private_urls)
    if not ok:
        return False, 0, False, "", err

    session = session_manager.session
    # Slightly longer connect/read budget — some hosts stall before the first byte.
    timeout_tuple = (timeout, max(timeout, 60))

    # ---- Strategy A: HEAD request ---------------------------------------
    # Cheap and works for most direct-download hosts. Many CDNs only answer
    # Content-Length/Accept-Ranges on HEAD.
    try:
        head = session.head(
            url,
            headers=build_browser_headers(url, config.user_agent, accept="*/*"),
            allow_redirects=True,
            timeout=timeout_tuple,
            verify=config.verify_ssl,
        )
        if head.status_code < 400 and head.status_code != 405:
            total_size, supports_range = _parse_total_from_response(head)
            filename = get_filename_from_response(dict(head.headers), url, head.url)
            # Some CDNs return 200 with a text/html "download not complete"
            # interstitial when they don't like the headers. Treat that as a
            # soft block and fall through to the GET strategies, which give the
            # host another chance (and may surface a clearer error).
            ct = head.headers.get("Content-Type", "")
            if is_html_error_response(ct, urlparse(head.url).path):
                head = None
            else:
                return True, total_size, supports_range, filename, ""
    except requests.exceptions.SSLError:
        return False, 0, False, "", "SSL certificate verification failed"
    except requests.exceptions.Timeout:
        return False, 0, False, "", "Timeout: server did not respond"
    except requests.exceptions.ConnectionError:
        return False, 0, False, "", "Connection error: cannot reach server"
    except requests.exceptions.TooManyRedirects:
        return False, 0, False, "", "Too many redirects"
    except Exception:
        # Fall through to GET-based probing for hosts that dislike HEAD.
        head = None

    # ---- Strategy B: ranged GET (1 byte) --------------------------------
    # Determines real range support and Content-Range total size.
    try:
        r = session.get(
            url,
            headers=build_browser_headers(
                url, config.user_agent, range_header="bytes=0-0", accept="*/*"
            ),
            stream=True,
            timeout=timeout_tuple,
            allow_redirects=True,
            verify=config.verify_ssl,
        )

        if r.status_code >= 400:
            # Some hosts reject Range but accept a plain GET. Try once more
            # without the Range header before giving up.
            r.close()
            try:
                r2 = session.get(
                    url,
                    headers=build_browser_headers(url, config.user_agent, accept="*/*"),
                    stream=True,
                    timeout=timeout_tuple,
                    allow_redirects=True,
                    verify=config.verify_ssl,
                )
                r = r2
            except Exception:
                return False, 0, False, "", f"HTTP {r.status_code} - {r.reason if hasattr(r,'reason') else ''}"

        if r.status_code >= 400:
            reason = getattr(r, "reason", "") or ""
            r.close()
            return False, 0, False, "", f"HTTP {r.status_code} - {reason}"

        ct = r.headers.get("Content-Type", "")
        # A protected/CDN host sometimes returns 200 + a text/html error page
        # (e.g. AMD's "Download Not Complete"). Don't save that as the file —
        # surface a clear, actionable error instead.
        if is_html_error_response(ct, urlparse(r.url).path):
            r.close()
            return (
                False,
                0,
                False,
                "",
                "Server returned an HTML page instead of the file "
                "(the link may be expired, region-blocked, or require a "
                "browser session). Try opening it in a browser first.",
            )

        total_size, supports_range = _parse_total_from_response(r)
        filename = get_filename_from_response(dict(r.headers), url, r.url)
        r.close()
        return True, total_size, supports_range, filename, ""

    except requests.exceptions.SSLError:
        return False, 0, False, "", "SSL certificate verification failed"
    except requests.exceptions.Timeout:
        return False, 0, False, "", "Timeout: server did not respond"
    except requests.exceptions.ConnectionError:
        return False, 0, False, "", "Connection error: cannot reach server"
    except requests.exceptions.TooManyRedirects:
        return False, 0, False, "", "Too many redirects"
    except Exception as exc:
        return False, 0, False, "", f"Error: {exc}"
