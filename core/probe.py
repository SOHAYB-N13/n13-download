"""URL probing with multi-strategy fallback (HEAD -> Range GET -> full GET).

``probe_url`` keeps the original 5-field return contract.  ``probe_with_headers``
extends it with the raw response headers so the analyzer can extract
``ETag`` / ``Last-Modified`` / ``Server`` / ``Content-Type`` for the task model.

What the probe guarantees
=========================
A probe is *metadata discovery*, and the transfer plan is built from its
answer, so a wrong answer is expensive: claiming range support a server does
not have makes every part fetch the whole file, and claiming a size the server
does not serve produces a truncated "complete" download.

The strategy is therefore "cheapest reliable answer, never a guess":

1. ``HEAD`` — one round trip, no body.
2. If HEAD did not advertise ``Accept-Ranges: bytes`` but the file is large
   enough for the answer to matter, spend **one extra byte** on
   ``Range: bytes=0-0`` to find out whether ranges work anyway.  Plenty of CDNs
   support them without advertising, and this is the only way to tell.  Small
   files skip it: they download as a single stream either way, so the answer
   cannot change the plan.
3. If HEAD was unusable (405/403/blocked/slow), fall back to the ranged GET and
   then to a plain GET.

A ``206`` is only believed when it carries a ``Content-Range`` whose start
matches the byte that was asked for, and its total is treated as authoritative
(it corrects a ``Content-Length`` that HEAD got wrong).  A ``200`` answer to a
ranged request means ranges are *not* honoured — the ``Accept-Ranges`` header
is ignored in that case, because a server that ignores ranges while advertising
them is exactly the case that turns one download into N full-file downloads.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from core.errors import BlockedURLError
from core.security import validate_download_url
from core.utils import (
    build_browser_headers,
    get_filename_from_response,
    is_html_error_response,
)

if TYPE_CHECKING:
    from config.settings import AppConfig
    from core.session import SessionManager

# Below this size the range answer cannot change the plan (the downloader uses
# a single stream anyway), so the extra byte is not spent.
RANGE_PROBE_MIN_SIZE = 1 * 1024 * 1024

# ---------------------------------------------------------------------------
# Probe budgets.
#
# Metadata discovery must never be the reason a download "takes 40 seconds to
# start".  A HEAD that has not answered within this many seconds tells us
# nothing useful — the server either ignores HEAD or is slow — and waiting the
# full generic read timeout before trying the request we actually need (a
# ranged GET) delayed working downloads by 8 s and dead ones by far more.
# ---------------------------------------------------------------------------
PROBE_HEAD_READ_TIMEOUT = 3.0
PROBE_HEAD_CONNECT_TIMEOUT = 3.0
# Wall-clock cap for the whole probe, including every fallback request.  When
# it expires the probe reports what it has and lets the transfer start: the
# first real response is a better source of metadata than a longer probe.
PROBE_TOTAL_BUDGET = 6.0

# Statuses for which a HEAD failure says nothing about GET: the method may be
# blocked (405/501), the endpoint may want different headers (403), or it may be
# rate-limiting (401/429).  For every *other* error status the server has given
# a definitive answer, and re-asking with a ranged GET only burns the probe
# budget — the transfer layer retries transient 5xx itself.
_HEAD_MAY_BE_BLOCKED = frozenset({400, 401, 403, 405, 406, 429, 501})

# Content encodings that make byte ranges meaningless: the server is free to
# re-compress each response, so offsets in the compressed stream do not line up
# with offsets in the file.
_IDENTITY_ENCODINGS = frozenset({"", "identity", "none"})


def _metadata_from_headers(r: requests.Response) -> Tuple[int, bool]:
    """Return ``(total_size, advertises_ranges)`` for a HEAD/200 response.

    ``advertises_ranges`` is only the *claim* (``Accept-Ranges: bytes`` or a
    ``206`` with a usable ``Content-Range``); proving it takes a ranged request.
    """
    total_size = 0
    supports_range = False

    if r.status_code == 206:
        size, honoured = _range_info(r, requested_start=None)
        if honoured:
            total_size = size
            supports_range = True

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


def _range_info(
    r: requests.Response, requested_start: Optional[int]
) -> Tuple[int, bool]:
    """Return ``(total_size, range_honoured)`` for a *real* ranged response.

    Believed only when the status is ``206``, the body is not re-encoded, and
    ``Content-Range`` parses and starts where we asked it to.  ``requested_start
    =None`` skips the start check (used when reading HEAD metadata).
    """
    if r.status_code != 206:
        return 0, False
    encoding = (r.headers.get("Content-Encoding") or "").strip().lower()
    if encoding not in _IDENTITY_ENCODINGS:
        # A compressed body cannot be reassembled from byte ranges.
        return 0, False

    content_range = r.headers.get("Content-Range", "")
    if not content_range:
        return 0, False
    try:
        unit, _, rest = content_range.partition(" ")
        rng, _, total = rest.partition("/")
        start_text, _, _end_text = rng.partition("-")
        start = int(start_text)
        total_size = int(total)
    except (ValueError, AttributeError):
        return 0, False

    if unit.strip().lower() != "bytes":
        return 0, False
    if requested_start is not None and start != requested_start:
        # The server answered a different range than the one requested —
        # resuming against this response would write the wrong bytes.
        return 0, False
    if total_size <= 0:
        return 0, False
    return total_size, True


def _verify_range_support(
    session: requests.Session,
    url: str,
    config: "AppConfig",
    timeout_tuple: Tuple[float, float],
) -> Optional[int]:
    """Spend one byte to learn whether ranges work.  ``None`` = they do not.

    Returns the authoritative total size from ``Content-Range`` when the server
    answers with a usable ``206``; ``None`` for anything else (including a
    ``200``, which means the Range header was ignored).
    """
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
    except Exception:
        return None
    try:
        total_size, honoured = _range_info(r, requested_start=0)
        return total_size if honoured else None
    finally:
        r.close()


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
    long: it runs inside a wall-clock budget (:data:`PROBE_TOTAL_BUDGET`) and
    gives HEAD its own short deadline (:data:`PROBE_HEAD_READ_TIMEOUT`), so a
    server that ignores or stalls HEAD costs a few seconds rather than the full
    generic read timeout.  When the budget expires the probe returns what it
    has — the transfer's first real response is a better metadata source than a
    longer probe.
    """
    ok, err = validate_download_url(url, block_private=config.block_private_urls)
    if not ok:
        return False, 0, False, "", err, {}, ""

    session = session_manager.probe_session
    connect = timeout if timeout is not None else config.probe_connect_timeout
    read = read_timeout if read_timeout is not None else config.probe_read_timeout
    timeout_tuple = (connect, read)

    deadline = time.monotonic() + PROBE_TOTAL_BUDGET

    def _budgeted(connect_t: float, read_t: float) -> Optional[Tuple[float, float]]:
        """Clamp a timeout pair to what is left of the probe budget."""
        left = deadline - time.monotonic()
        if left <= 0.5:
            return None
        return (max(0.5, min(connect_t, left)), max(0.5, min(read_t, left)))

    # ---- Strategy A: HEAD request ---------------------------------------
    head: Optional[requests.Response] = None
    head_timeout = _budgeted(PROBE_HEAD_CONNECT_TIMEOUT, PROBE_HEAD_READ_TIMEOUT)
    try:
        if head_timeout is None:
            raise requests.exceptions.Timeout("probe budget exhausted")
        candidate = session.head(
            url,
            headers=build_browser_headers(url, config.user_agent, accept="*/*"),
            allow_redirects=True,
            timeout=head_timeout,
            verify=config.verify_ssl,
        )
        if candidate.status_code < 400 and candidate.status_code != 405:
            ct = candidate.headers.get("Content-Type", "")
            # An HTML interstitial is not the file; fall through to the ranged
            # GET, which is a better position to diagnose it from.
            if not is_html_error_response(ct, urlparse(candidate.url).path):
                head = candidate
        elif candidate.status_code not in _HEAD_MAY_BE_BLOCKED:
            # A definitive error (404, 410, 500, 502 …) — the server has told us
            # what it thinks.  Running the ranged-GET fallback here would spend
            # the whole probe budget to learn the same thing, so report it and
            # let the transfer layer decide whether to retry.
            reason = candidate.reason or ""
            return (
                False, 0, False, "",
                f"HTTP {candidate.status_code}" + (f" - {reason}" if reason else ""),
                {}, candidate.url,
            )
    except BlockedURLError as exc:
        return False, 0, False, "", f"Blocked by security policy: {exc.reason}", {}, ""
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

    if head is not None:
        total_size, advertises = _metadata_from_headers(head)
        filename = get_filename_from_response(dict(head.headers), url, head.url)
        headers = dict(head.headers)

        if not advertises and total_size >= RANGE_PROBE_MIN_SIZE:
            # Only worth one extra byte while there is budget left for it.
            probe_timeout = _budgeted(connect, read)
            if probe_timeout is not None:
                proven_size = _verify_range_support(
                    session, url, config, probe_timeout
                )
                if proven_size:
                    # The 206 Content-Range is authoritative and also repairs a
                    # Content-Length that HEAD reported incorrectly.
                    return True, proven_size, True, filename, "", headers, head.url

        return True, total_size, advertises, filename, "", headers, head.url

    # ---- Strategy B: ranged GET (1 byte) --------------------------------
    try:
        timeout_tuple = _budgeted(connect, read) or (1.0, 1.0)
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
                r = session.get(
                    url,
                    headers=build_browser_headers(url, config.user_agent, accept="*/*"),
                    stream=True,
                    timeout=timeout_tuple,
                    allow_redirects=True,
                    verify=config.verify_ssl,
                )
            except Exception:
                return False, 0, False, "", f"HTTP {r.status_code} - {getattr(r, 'reason', '')}", {}, ""

        if r.status_code >= 400:
            reason = getattr(r, "reason", "") or ""
            status = r.status_code
            r.close()
            return False, 0, False, "", f"HTTP {status} - {reason}", {}, ""

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

        # A 206 here *proves* range support and its Content-Range total is the
        # authoritative size.  A 200 means ranges were ignored, so the
        # Accept-Ranges header must not be trusted.
        proven_size, honoured = _range_info(r, requested_start=0)
        if honoured:
            total_size, supports_range = proven_size, True
        else:
            total_size, _advertises = _metadata_from_headers(r)
            supports_range = False

        filename = get_filename_from_response(dict(r.headers), url, r.url)
        headers = dict(r.headers)
        final_url = r.url
        r.close()
        return True, total_size, supports_range, filename, "", headers, final_url

    except BlockedURLError as exc:
        return False, 0, False, "", f"Blocked by security policy: {exc.reason}", {}, ""
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
