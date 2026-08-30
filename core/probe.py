"""URL probing with multi-strategy fallback (HEAD -> Range GET -> full GET).

``probe_url`` keeps the original 5-field return contract.  ``probe_with_headers``
extends it with the raw response headers so the analyzer can extract
``ETag`` / ``Last-Modified`` / ``Server`` / ``Content-Type`` for the task model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple
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


def _probe_impl(
    url: str,
    config: "AppConfig",
    session_manager: "SessionManager",
    timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
) -> Tuple[bool, int, bool, str, str, Dict[str, str], str]:
    """Probe implementation.

    Returns ``(ok, size, range, name, err, headers, final_url)`` where
    ``final_url`` is the URL after redirect resolution (requests' ``.url``),
    empty when the probe failed.  Callers use it to avoid traversing the same
    redirect chain again when the transfer starts.

    The probe is metadata discovery and must never gate the download start for
    long: timeouts default to the configured fast startup budget
    (``probe_connect_timeout`` / ``probe_read_timeout``), and it uses the
    session's dedicated probe transport (no read-retry multiplication).
    """
    ok, err = validate_download_url(url, block_private=config.block_private_urls)
    if not ok:
        return False, 0, False, "", err, {}, ""

    session = session_manager.probe_session
    connect = timeout if timeout is not None else config.probe_connect_timeout
    read = read_timeout if read_timeout is not None else config.probe_read_timeout
    timeout_tuple = (connect, read)

    # ---- Strategy A: HEAD request ---------------------------------------
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
            ct = head.headers.get("Content-Type", "")
            if is_html_error_response(ct, urlparse(head.url).path):
                head = None
            else:
                return True, total_size, supports_range, filename, "", dict(head.headers), head.url
    except requests.exceptions.SSLError:
        return False, 0, False, "", "SSL certificate verification failed", {}, ""
    except requests.exceptions.TooManyRedirects:
        return False, 0, False, "", "Too many redirects", {}, ""
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        # HEAD is unreliable/blocked/slow on some servers — fall through to
        # the ranged GET instead of failing the whole probe.
        head = None
    except Exception:
        head = None

    # ---- Strategy B: ranged GET (1 byte) --------------------------------
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
                return False, 0, False, "", f"HTTP {r.status_code} - {getattr(r, 'reason', '')}", {}, ""

        if r.status_code >= 400:
            reason = getattr(r, "reason", "") or ""
            r.close()
            return False, 0, False, "", f"HTTP {r.status_code} - {reason}", {}, ""

        ct = r.headers.get("Content-Type", "")
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
                {},
                "",
            )

        total_size, supports_range = _parse_total_from_response(r)
        filename = get_filename_from_response(dict(r.headers), url, r.url)
        headers = dict(r.headers)
        r.close()
        return True, total_size, supports_range, filename, "", headers, r.url

    except requests.exceptions.SSLError:
        return False, 0, False, "", "SSL certificate verification failed", {}, ""
    except requests.exceptions.Timeout:
        return False, 0, False, "", "Timeout: server did not respond", {}, ""
    except requests.exceptions.ConnectionError:
        return False, 0, False, "", "Connection error: cannot reach server", {}, ""
    except requests.exceptions.TooManyRedirects:
        return False, 0, False, "", "Too many redirects", {}, ""
    except Exception as exc:
        return False, 0, False, "", f"Error: {exc}", {}, ""


def probe_url(
    url: str,
    config: "AppConfig",
    session_manager: "SessionManager",
    timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
) -> Tuple[bool, int, bool, str, str]:
    """Return ``(reachable, total_size, supports_range, filename, error)``."""
    ok, total, supports, name, err, _headers, _final = _probe_impl(
        url, config, session_manager, timeout, read_timeout
    )
    return ok, total, supports, name, err


def probe_with_headers(
    url: str,
    config: "AppConfig",
    session_manager: "SessionManager",
    timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
) -> Tuple[bool, int, bool, str, str, Dict[str, str], str]:
    """Like :func:`probe_url` but also returns the raw response headers AND
    the final URL after redirect resolution (``""`` when the probe failed)."""
    return _probe_impl(url, config, session_manager, timeout, read_timeout)
