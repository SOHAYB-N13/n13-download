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
from typing import Optional

import requests


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
