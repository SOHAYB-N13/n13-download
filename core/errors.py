"""Friendly, user-facing error messages (Phase 23).

The engine's internals deal in raw ``requests`` exceptions and HTTP status
codes.  This module converts those into the short, actionable sentences shown
to the user (and stored in ``DownloadTask.error``) without leaking stack
traces, URLs with credentials, cookies or auth headers.

Note: ``requests.exceptions.ConnectionError`` subclasses both
``requests.RequestException`` *and* ``IOError``/``OSError``, so the requests
checks must come before the bare ``OSError`` branch.
"""

from __future__ import annotations

import errno
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests

# Any absolute URL inside a log/diagnostic string.
_URL_IN_TEXT = re.compile(r"""https?://[^\s'"<>]+""", re.I)
# ``requests``/urllib3 also quote *relative* URLs (``... url: /f.zip?token=…``),
# which carry exactly the same secret and are the more common shape.
_REFERENCE_AFTER_LABEL = re.compile(r"""(?i)(\burl:\s*)([^\s'"<>]+)""")


def _scrub_reference(raw: str) -> str:
    """Drop the query string and any embedded userinfo from one URL."""
    try:
        parts = urlsplit(raw)
    except (ValueError, TypeError):
        return raw
    if not parts.query and "@" not in parts.netloc:
        return raw
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop user:pass@
    query = "<redacted>" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_secrets(text: str) -> str:
    """Strip credentials and query strings out of URLs inside *text*.

    Exception messages from ``requests`` embed the request URL, and a signed
    download link carries its authorisation **in the query string**.  Logging
    the exception verbatim therefore writes a working credential to disk.  This
    keeps the host and path (which is what a diagnosis needs) and replaces the
    query and any embedded userinfo with ``<redacted>``.

    Both absolute URLs and the relative ones urllib3 quotes after ``url:`` are
    handled.  A string containing neither is returned unchanged, so this is
    safe to apply to every log line.
    """
    if not text:
        return text

    def _scrub(match: "re.Match[str]") -> str:
        raw = match.group(0)
        if not urlsplit(raw).netloc:
            return raw
        return _scrub_reference(raw)

    text = _URL_IN_TEXT.sub(_scrub, text)
    return _REFERENCE_AFTER_LABEL.sub(
        lambda m: m.group(1) + _scrub_reference(m.group(2)), text
    )


class BlockedURLError(requests.RequestException):
    """A request was refused by the URL security policy.

    Raised by the transport when a *redirect* points at a target the SSRF
    policy forbids (a private/loopback address, a non-http scheme, an embedded
    credential).  It is a ``requests`` exception on purpose so every existing
    ``except requests.RequestException`` handler still catches it, but it is
    classified as **non-retryable**: retrying a policy decision can never
    succeed and would only delay the honest error.
    """

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(reason)
        self.reason = reason or "Blocked by URL security policy"
        self.url = url


def _os_error_reason(exc: OSError) -> Optional[str]:
    if exc.errno == errno.ENOSPC:
        return "Disk is full - free some space and try again"
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "Permission denied - cannot write to the destination folder"
    if exc.errno == errno.ENAMETOOLONG:
        return "File name is too long"
    return None


def friendly_error_message(exc: BaseException, status: Optional[int] = None) -> str:
    """Return a clean user-facing message for *exc* (and optional HTTP status)."""
    if isinstance(exc, BlockedURLError):
        return f"Blocked by security policy: {exc.reason}"
    if isinstance(exc, requests.Timeout):
        return "Connection timed out - the server did not respond"
    if isinstance(exc, requests.ConnectionError):
        return "Network connection lost - will retry automatically"
    if isinstance(exc, requests.TooManyRedirects):
        return "Too many redirects"
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSL certificate verification failed"
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        status = (resp.status_code if resp is not None else None) or status

    if status is not None:
        if status == 401 or status == 403:
            return "Authentication required - the server rejected the request (401/403)"
        if status == 404:
            return "Server returned 404 - the file was not found"
        if status == 408 or status == 429:
            return "Server is busy - will retry automatically"
        if status >= 500:
            return f"Server error (HTTP {status}) - will retry automatically"

    if isinstance(exc, requests.RequestException):
        return f"Request failed: {exc.__class__.__name__}"

    if isinstance(exc, OSError):
        reason = _os_error_reason(exc)
        if reason:
            return reason
        if exc.errno in (errno.ECONNRESET, errno.ECONNABORTED, errno.ENETRESET, errno.EPIPE):
            return "Network connection was lost"
        if exc.errno in (errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH):
            return "Network connection lost - will retry automatically"
        return f"File system error: {exc}"

    return str(exc) or "Download failed"
