"""Multi-threaded download engine.

Optimisation notes
==================
1.  **Socket buffer tuning** — set in :class:`core.session._OptimisedAdapter`
    via ``SO_RCVBUF`` / ``SO_SNDBUF`` (4 MB / 512 KB) so the TCP window
    can handle high-BDP links without stalling.

2.  **Lock-free cancellation** — ``DownloadContext._cancel_event`` is a
    :class:`threading.Event` whose ``is_set()`` is lock-free in the hot
    download loop, replacing the previous class-level mutex that was
    acquired on *every iteration*.

3.  **Progress batching** — each worker accumulates bytes locally and
    flushes to shared state only when a threshold is crossed, reducing
    lock acquisitions from every chunk (4 MB → 25+ /s) to every
    ``_PROGRESS_FLUSH_THRESHOLD`` bytes (~4× fewer on average).

4.  **Progress callback throttle** — external callbacks (GUI, CLI) are
    limited to ~20 Hz so they never become the bottleneck.

5.  **Thread pool reuse** — a module-level :class:`ThreadPoolExecutor`
    is shared across downloads (lazy-created, auto-scaled) to avoid the
    overhead of creating / destroying threads on every ``download_file``
    call.

6.  **Content negotiation** — ``Accept-Encoding`` is *not* set in request
    headers, allowing urllib3 to negotiate gzip/deflate/brotli with the
    server.  Transparent decompression in C (zlib) adds ~1 % CPU per
    100 MB/s — negligible vs the improved CDN compatibility.
"""

from __future__ import annotations

import logging
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from rich.progress import Progress

from config.settings import AppConfig
from core.context import DownloadContext
from core.budget import ConnectionMeter, sync_connection_budget
from core.merge import merge_parts
from core.optimizer import MANUAL_MODE, is_adaptive
from core.parts import (
    DownloadPart,
    build_direct_parts,
    build_parts,
    remap_parts_for_resume,
)
from core.probe import probe_url
from core.progress import ProgressLedger
from core.session import SessionManager
from core.speed import SpeedTracker
from core.state import DownloadState
from core.throttle import BandwidthLimiter, get_global_limiter, sync_limiter_from_config
from core.errors import BlockedURLError, friendly_error_message, redact_secrets
from core.urls import looks_like_filename, url_filename
from core.utils import (
    build_browser_headers,
    calculate_checksum,
    detect_hash_algorithm,
    fit_filename,
    format_size,
    is_html_error_response,
    safe_rename,
    unique_filepath,
)
from ui.progress import create_download_progress

# ---------------------------------------------------------------------------
# Part-transfer thread-pool — one executor per download_file() call.
#
# A single process-wide pool was tried first, to avoid executor construction
# on every call.  It turned one stalled transfer into a process-wide freeze:
# part threads blocked on a socket that has stopped delivering keep their
# worker, and because the pool is only ever grown, a download that needs the
# same capacity waits in the queue forever instead of transferring.  The
# executor is cheap next to a network transfer, so each download now owns its
# own and releases it as soon as its futures are collected.
# ---------------------------------------------------------------------------

# Progress flush threshold: local accumulation before hitting shared
# state (reduces lock contention). 256 KB is a good balance — frequent
# enough for responsive UI, infrequent enough to avoid lock storms.
_PROGRESS_FLUSH_THRESHOLD = 256 * 1024

# Max progress callback frequency (Hz).  20 Hz = every 50 ms.
_CALLBACK_INTERVAL = 0.05

# Longest a byte counter may go unflushed.  Without a time bound the UI only
# learned about data when the byte threshold was crossed, which on a slow link
# meant the bar sat still for tens of seconds and then jumped.
_PROGRESS_FLUSH_INTERVAL = 0.10

# ---------------------------------------------------------------------------
# Read slice and the pre-first-byte budget.
#
# ``iter_content(n)`` blocks until n bytes are buffered, so the read size IS the
# progress granularity.  With the 4 MB default chunk a 200 KB/s link delivered
# its first progress update only after ~20 s — the bar sat still and then jumped
# by a whole chunk, which is exactly the "progress jumps forward" report.  Reads
# are therefore sliced; the file write stays buffered, so throughput is
# unaffected while accounting becomes smooth and resume granularity finer.
# ---------------------------------------------------------------------------
_READ_SLICE = 512 * 1024

# Per-part write buffer.
#
# This is user-space memory held by *every* part thread until it fills, so the
# cost multiplies by the part count: the previous 8 MB per part meant a
# 16-part download (the default thread count) pinned 128 MB of buffers before
# anything reached the disk, and three concurrent downloads could hold ~384 MB.
# Measured on this machine, 8 parts at 8 MB grew the working set by 71 MB
# versus 9 MB for a single stream.
#
# Reads arrive in `_READ_SLICE` chunks, so 1 MB still coalesces them into large
# sequential writes; the Windows file cache does the rest.  Benchmarked before
# and after: no throughput change on loopback (which is the only link this
# harness can measure honestly).
_PART_WRITE_BUFFER = 1024 * 1024

# Wall-clock cap for "connected but no body byte yet".  Without it, six startup
# attempts at a 15 s read timeout left a dead-but-accepting server frozen for
# ~98 s before the user saw an error.
#
# This is a *floor*: the real budget is derived from the user-visible
# ``startup_read_timeout`` (see _startup_budget), so raising that setting
# actually helps a slow server instead of being silently defeated by a fixed
# ceiling.
_STARTUP_TOTAL_BUDGET = 20.0
# How much longer than a single read the whole phase may take (room for a
# retry after a fast failure).
_STARTUP_BUDGET_MARGIN = 5.0
# Attempts allowed before the first byte.  The generic retry budget (15) is far
# too many when every attempt is a cheap failure: a refused port spent 26 s
# cycling them.  Four is enough to ride out a restart and short enough that a
# dead host is reported quickly.
_STARTUP_MAX_ATTEMPTS = 4
# Pre-first-byte backoff cap.  These attempts are cheap, so the exponential
# ladder's long tails (8 s, 16 s) only delay the honest error; the total budget
# is what bounds the phase.
_STARTUP_MAX_BACKOFF = 1.0

log = logging.getLogger("n13")


class DownloadTimeline:
    """Phase timestamps for one download, for diagnostics that explain delays.

    The point is to separate *genuine* server/network delay from engine delay.
    ``metadata=0.02s headers=0.05s first_byte=12.40s`` says the server took
    twelve seconds to produce a body byte; ``metadata=7.90s`` says our own
    metadata step did.  A single INFO summary is logged per download and each
    mark is also emitted at DEBUG, so verbose detail is opt-in and cheap.

    Thread-safe: parts of one download mark concurrently.
    """

    __slots__ = ("_t0", "_order", "_at", "_lock")

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._order: List[str] = []
        self._at: dict = {}
        self._lock = threading.Lock()

    def mark(self, name: str) -> float:
        """Record *name* once (later calls are ignored) and return its offset."""
        now = time.monotonic() - self._t0
        with self._lock:
            if name in self._at:
                return self._at[name]
            self._order.append(name)
            self._at[name] = now
        log.debug("TIMELINE %-12s %.3fs", name, now)
        return now

    def get(self, name: str) -> Optional[float]:
        with self._lock:
            return self._at.get(name)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def summary(self) -> str:
        with self._lock:
            return " ".join(f"{n}={self._at[n]:.2f}s" for n in self._order)

    def as_dict(self) -> dict:
        """Phase offsets keyed by name, for structured diagnostics."""
        with self._lock:
            return {n: round(self._at[n], 3) for n in self._order}


@dataclass
class SegmentReport:
    """What one byte-range segment actually achieved.

    ``written`` is read back from the part file rather than taken from a
    running counter, so a buffered-but-unflushed write cannot be counted as
    delivered.
    """

    index: int
    start: int
    end: int
    expected: int
    written: int
    complete: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "expected": self.expected,
            "written": self.written,
            "complete": self.complete,
        }


@dataclass
class DownloadReport:
    """Structured, redaction-safe account of one download attempt.

    Answers, for a single transfer: which strategy ran, what the server
    claimed, how many bytes each segment really holds, where the time went,
    and — the part that matters most — whether completion was *verified* or
    merely *assumed*.

    ``integrity`` is deliberately three-valued, because collapsing it to a
    boolean is how a truncated media file gets reported as a success:

    ``verified``
        Every segment held its exact byte count, the assembled file matched
        the declared length, and the checksum matched when one was supplied.
    ``unverifiable``
        The server never declared a length.  The bytes are consistent with
        what was received, but nothing independent confirms the file is whole.
    ``failed``
        Not completed, or a check rejected the result.

    ``url`` is redacted: a signed link carries its authorisation in the query
    string, and this record is meant to be safe to log or attach to a bug
    report.
    """

    url: str = ""
    host: str = ""
    strategy: str = ""            # "ranged" | "single-stream"
    range_supported: bool = False
    length_known: bool = False
    expected_length: int = 0
    bytes_written: int = 0
    segments: List[SegmentReport] = field(default_factory=list)
    peak_connections: int = 0       # measured client-side, not a server counter
    ttfb: Optional[float] = None
    duration: float = 0.0
    integrity: str = "failed"     # verified | unverifiable | failed
    validation: str = ""          # the completion gate's own verdict
    failure_reason: str = ""
    timeline: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "host": self.host,
            "strategy": self.strategy,
            "range_supported": self.range_supported,
            "length_known": self.length_known,
            "expected_length": self.expected_length,
            "bytes_written": self.bytes_written,
            "segments": [s.to_dict() for s in self.segments],
            "peak_connections": self.peak_connections,
            "ttfb": self.ttfb,
            "duration": round(self.duration, 3),
            "integrity": self.integrity,
            "validation": self.validation,
            "failure_reason": self.failure_reason,
            "timeline": dict(self.timeline),
        }


def _new_part_pool(max_workers: int) -> ThreadPoolExecutor:
    """Create the executor that runs one download's part transfers.

    Scoped to a single ``download_file`` call so a part thread that is stuck
    on a dead peer can never occupy a worker another download needs.
    """
    return ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="n13-dl",
    )


# HTTP status codes that are worth retrying (transient/server-side).
_RETRYABLE_STATUS = frozenset(
    {408, 425, 429, 500, 502, 503, 504}
)
# Status codes that mean "do not bother retrying" (client-side/permanent).
_FATAL_STATUS = frozenset(range(400, 500)) - _RETRYABLE_STATUS

# A server-supplied ``Retry-After`` is honoured, but never beyond this, so a
# broken or hostile value cannot park a download (and its queue slot) for hours.
_RETRY_AFTER_CAP = 60.0

# Write-buffer flush threshold: accumulate chunks in memory and flush to disk
# in larger blocks to reduce syscall overhead on every part download.

# Pre-first-byte retry budget.  Before ANY byte has been received, retries use
# a fast, bounded schedule (0.5 / 1 / 2 s) and a hard attempt cap so a flaky
# server can never stall the download startup for tens of seconds.  Once the
# transfer has begun, the normal (more tolerant) retry behaviour applies.
_STARTUP_RETRY_BASE = 0.5
_STARTUP_RETRY_CAP = 2.0

# Pre-first-byte request timeouts (connect, read) — short so a dead-but-
# accepting endpoint fails within a bounded time.
_DOWNLOAD_TIMEOUT = (30, 120)

#: Longest a healthy transfer may go without receiving a single byte before the
#: connection is abandoned and re-issued from the offset already on disk.
#:
#: This is a *stall* threshold, not an inactivity timeout for the whole
#: download: it only fires when the socket itself delivers nothing.  The old
#: value (the 120 s read timeout above) meant a connection that went silent
#: mid-transfer kept a governor slot for two minutes, which is exactly what
#: "the speed drops to zero and then jumps back" looks like.  Twenty seconds is
#: long enough that no healthy link trips it — bytes arriving at 10 KB/s still
#: reset the clock every second.
STALL_TIMEOUT = 20.0

#: A stall is a transport hiccup, not a server rejection, so the retry is
#: immediate-ish instead of walking the exponential backoff ladder.
_STALL_RETRY_DELAY = 0.5


def _response_socket(response):
    """Find the live socket behind a streamed response, or ``None``.

    urllib3 2.x clears ``HTTPConnection.sock`` as soon as a streamed response is
    handed to the caller, so the only reliable route is the ``http.client``
    response underneath: ``raw._fp`` (``HTTPResponse``) -> ``.fp``
    (``BufferedReader``) -> ``.raw`` (``SocketIO``) -> ``._sock``.

    Both layouts are tried because the older one is still valid for
    non-streamed responses.  Returning ``None`` is not an error: the caller
    then leaves the request's own timeout in place.
    """
    raw = getattr(response, "raw", None)
    if raw is None:
        return None
    candidates = []
    try:
        conn = getattr(raw, "_connection", None)
        if conn is not None:
            candidates.append(getattr(conn, "sock", None))
    except Exception:
        pass
    try:
        inner = getattr(getattr(raw, "_fp", None), "fp", None)
        inner = getattr(inner, "raw", None)
        candidates.append(getattr(inner, "_sock", None))
    except Exception:
        pass
    for sock in candidates:
        if sock is not None:
            return sock
    return None


def _is_timeout_like(exc: BaseException) -> bool:
    """True when *exc* is, or wraps, a read timeout.

    ``requests`` does not always surface a read timeout as
    ``requests.exceptions.Timeout``: a timeout that urllib3 has already retried
    inside the adapter arrives as a ``ConnectionError`` whose ``reason`` is the
    original ``ReadTimeoutError``.  Classifying by type alone therefore misses
    exactly the case the stall watchdog exists for, so the cause chain is
    walked as well.

    Only the *timeout* family counts.  A reset, a refused connection or an
    HTTP error is a rejection and must keep its existing handling.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (requests.exceptions.Timeout, socket.timeout)):
            return True
        if type(current).__name__ in ("ReadTimeoutError", "TimeoutError", "Timeout"):
            return True
        # urllib3 puts the real cause in ``reason``; requests and Python use
        # the chaining attributes; and ``requests`` wraps the adapter's
        # ``MaxRetryError`` positionally, so ``args[0]`` is walked too.
        nxt: Optional[BaseException] = None
        for candidate in (
            getattr(current, "reason", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(candidate, BaseException):
                nxt = candidate
                break
        if nxt is None:
            args = getattr(current, "args", ())
            if args and isinstance(args[0], BaseException):
                nxt = args[0]
        current = nxt
    return False


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """The server's ``Retry-After`` instruction, in seconds, if it gave one.

    Accepts both forms the RFC allows: a delay in seconds and an HTTP-date.
    Returns ``None`` when the header is absent or unparseable — an absent
    instruction is not the same as "retry immediately".
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = (getattr(response, "headers", {}) or {}).get("Retry-After")
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return float(raw)
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    try:
        now = datetime.now(target.tzinfo) if target.tzinfo else datetime.now()
    except Exception:
        return None
    return max(0.0, (target - now).total_seconds())


def _retry_delay(attempt: int, config: AppConfig, status: Optional[int] = None,
                 started: bool = False, retry_after: Optional[float] = None) -> float:
    """Compute the next backoff delay, capped and jittered.

    ``started=False`` (no byte received yet) uses a fast bounded schedule —
    the startup must never be gated by a long sleep chain.  ``started=True``
    keeps the configured exponential backoff for in-transfer retries.

    ``retry_after`` (from a ``429``/``503`` response) raises the delay to what
    the server asked for, capped by :data:`_RETRY_AFTER_CAP` so it can never
    stall the transfer indefinitely.
    """
    if not started:
        base = min(_STARTUP_RETRY_BASE, config.retry_delay)
        backoff = 2.0
        cap = min(_STARTUP_RETRY_CAP, config.retry_max_delay)
    else:
        base = config.retry_delay
        backoff = config.retry_backoff
        cap = config.retry_max_delay
    delay = base * (backoff ** (attempt - 1))
    delay = min(delay, cap)
    # Decorrelated jitter in [delay*(1-j), delay*(1+j)].
    jitter = config.retry_jitter
    delay *= 1.0 - jitter + random.random() * (2 * jitter)
    delay = max(0.0, delay)
    if retry_after is not None:
        delay = max(delay, min(retry_after, _RETRY_AFTER_CAP))
    return delay


def _interruptible_sleep(seconds: float, control=None) -> bool:
    """Sleep in short slices so a cancel request is honoured within ~0.25 s.

    Checks both the process-global ``DownloadContext._cancel_event`` (for CLI
    downloads) and the per-task ``control`` (for queue downloads).  Returns
    ``True`` when the full delay elapsed, ``False`` when cancelled.
    """
    cancel_event = DownloadContext._cancel_event
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if cancel_event.is_set():
            return False
        if control is not None and control.cancelled:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))


def _is_retryable_exception(exc: BaseException) -> tuple[bool, Optional[int]]:
    """Classify an exception as (retryable, status_code).

    Connection/timeout errors are transient.  HTTP errors are retryable only
    for server-side or explicit-retry status codes.  A refusal by the URL
    security policy, a redirect loop, and a malformed URL are all permanent:
    retrying them can never change the outcome, so they must not consume the
    retry budget (which used to mean 15 pointless attempts and a long stall
    before the honest error surfaced).
    """
    status: Optional[int] = None
    if isinstance(exc, BlockedURLError):
        return False, None
    if isinstance(
        exc,
        (
            requests.TooManyRedirects,
            requests.exceptions.URLRequired,
            requests.exceptions.MissingSchema,
            requests.exceptions.InvalidSchema,
            requests.exceptions.InvalidURL,
        ),
    ):
        return False, None
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        status = resp.status_code if resp is not None else None
        if status is not None:
            if status in _RETRYABLE_STATUS:
                return True, status
            if status in _FATAL_STATUS:
                return False, status
            # Other 4xx/5xx: only the explicit transient set is retried —
            # 501/505 and friends fail fast instead of wasting attempts.
            return False, status
        return True, status
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, ConnectionError)):
        return True, None
    if isinstance(exc, requests.RequestException):
        return True, None
    return False, status


class _RangeState:
    """Shared verdict on whether the parallel byte-range plan is usable.

    Set by the first worker that learns the server will not honour the plan —
    either it answered a ranged request with ``200`` (ranges ignored) or its
    ``Content-Range`` reports a different total than the one the plan was built
    from (metadata changed between the probe and the transfer).  Every other
    worker then stops immediately instead of each fetching the whole file, and
    the orchestrator re-runs the transfer as a single stream.

    A plain ``threading.Event`` is enough: the state only ever goes one way, and
    ``is_set()`` is lock-free on the hot path.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def mark_unusable(self) -> None:
        self._event.set()

    @property
    def unusable(self) -> bool:
        return self._event.is_set()


class _EntityGuard:
    """Watches the identity of the resource being fetched.

    ``ETag`` / ``Last-Modified`` are the only way to tell "the same file, still
    the same file" from "a different file that happens to be the same length".
    Without them a resume silently splices two versions together, which is
    exactly the sort of corruption that survives every size check.

    Seeded with the validators from a previous session it *compares*; seeded
    empty it *records* the first ones it sees, so a fresh download can persist
    them for the next resume.  A server that omits them simply leaves the guard
    with nothing to compare, which is reported honestly as "cannot tell".
    """

    __slots__ = ("_lock", "etag", "last_modified", "_mismatch")

    def __init__(self, etag: str = "", last_modified: str = "") -> None:
        self._lock = threading.Lock()
        self.etag = etag or ""
        self.last_modified = last_modified or ""
        self._mismatch = threading.Event()

    def observe(self, headers) -> bool:
        """Record or compare validators; ``False`` means the entity changed."""
        try:
            etag = (headers.get("ETag") or "").strip()
            modified = (headers.get("Last-Modified") or "").strip()
        except Exception:  # pragma: no cover — defensive against odd mocks
            return True
        with self._lock:
            if not self.etag and not self.last_modified:
                self.etag = etag
                self.last_modified = modified
                return True
            if self.etag and etag and etag != self.etag:
                self._mismatch.set()
                return False
            if self.last_modified and modified and modified != self.last_modified:
                self._mismatch.set()
                return False
        return True

    @property
    def changed(self) -> bool:
        return self._mismatch.is_set()


def _content_range_total(header: str) -> int:
    """The total entity size from a ``Content-Range`` header (``0`` if absent).

    ``"bytes 0-99/1000"`` → ``1000``.  An unknown total (``"bytes 0-99/*"``)
    and a malformed header both yield ``0``, meaning "no information" — never a
    fabricated size.
    """
    try:
        _, _, total = (header or "").partition("/")
        return int(total)
    except (ValueError, TypeError):
        return 0


def _content_range_start(header: str) -> Optional[int]:
    """The first byte offset reported by a ``Content-Range`` header."""
    try:
        unit, _, rest = (header or "").partition(" ")
        if unit.strip().lower() != "bytes":
            return None
        rng, _, _total = rest.partition("/")
        start_text, _, _end_text = rng.partition("-")
        return int(start_text)
    except (ValueError, TypeError):
        return None


def _content_range_span(header: str) -> Optional[tuple]:
    """Parse ``bytes START-END/TOTAL`` into ``(start, end, total)``.

    ``total`` is ``0`` when the server wrote ``*`` (unknown).  Returns ``None``
    when the header is absent, uses another unit, or is malformed — in every
    one of those cases the response cannot be checked against the range that
    was requested, so the caller must not trust it.
    """
    try:
        unit, _, rest = (header or "").partition(" ")
        if unit.strip().lower() != "bytes":
            return None
        rng, sep, total_text = rest.partition("/")
        if not sep:
            return None
        start_text, _, end_text = rng.partition("-")
        start = int(start_text)
        end = int(end_text)
        if start < 0 or end < start:
            return None
        total = 0
        if total_text.strip() != "*":
            total = int(total_text)
            if total <= 0:
                return None
        return start, end, total
    except (ValueError, TypeError):
        return None


def _parts_within_directory(parts: List[DownloadPart], directory: Path) -> bool:
    """Reject resume state whose part files escape the download directory."""
    try:
        base = directory.resolve()
        for part in parts:
            part.path.resolve().relative_to(base)
    except (ValueError, OSError):
        return False
    return True


def _carry_direct_coverage(
    old_parts: List[DownloadPart], new_parts: List[DownloadPart],
    file_tail: int,
) -> None:
    """Copy proven byte coverage into a new direct-write range plan.

    Direct parts share the destination, so the bytes are already at their
    absolute offsets.  Only the *knowledge* of which prefixes are complete
    has to move: a new range keeps the contiguous prefix that the old ranges
    prove was written.  Anything not proven is re-downloaded.
    """
    intervals: list[tuple[int, int]] = []
    for part in old_parts:
        written = min(part.written, part.size)
        start = part.start
        end = min(start + written, part.end + 1, file_tail)
        if end > start:
            intervals.append((start, end))
    intervals.sort()
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    for part in new_parts:
        position = part.start
        while position <= part.end:
            covering = next(
                (span for span in merged if span[0] <= position < span[1]),
                None,
            )
            if covering is None:
                break
            position = covering[1]
        part.written = max(0, min(part.size, position - part.start))


def _fallback_filename(url: str) -> str:
    """Best-effort file name from a URL when the probe supplied none.

    One implementation, shared with the UI's display name and the engine's
    header-based resolver (see :func:`core.urls.url_filename`), so a URL whose
    filename lives in the query string cannot be named three different things
    in three places.  Only a *filename-shaped* answer is accepted here —
    ``url_filename`` falls back to the host, and a file called ``example.com``
    is less useful than the generic name.
    """
    name = url_filename(url)
    return name if name and looks_like_filename(name) else "download"


class DownloadController:
    """Coordinates downloads with injectable dependencies."""

    def __init__(
        self,
        config: AppConfig,
        session_manager: Optional[SessionManager] = None,
        console_print: Optional[Callable[..., None]] = None,
        show_progress: bool = True,
    ):
        self.config = config
        # Ensure the session picks up proxy/auth/cookies from the config.
        if session_manager is None:
            session_manager = SessionManager(config)
        else:
            session_manager.configure(config)
        self.session = session_manager
        # A failing log/print callback must never fail the download itself, so
        # every console_print call is guarded.  (Fixes e.g. a cp1252 console
        # choking on the '✓' glyph in the success message.)
        _raw_print = console_print or (lambda *args, **kwargs: None)

        def _safe_print(*args, **kwargs) -> None:
            try:
                _raw_print(*args, **kwargs)
            except Exception:
                pass

        self._print = _safe_print
        self.show_progress = show_progress
        # Last failure, in user-friendly form (read by queue runners).
        self.last_error: str = ""
        # Share one process-wide limiter so UI speed-cap changes apply immediately.
        sync_limiter_from_config(config)
        self._limiter: Optional[BandwidthLimiter] = get_global_limiter()
        # Bound the *total* number of transfer sockets across every download,
        # not just this one's.  See core/budget.py for why the product of
        # per-download ceilings needs an explicit cap.
        self._budget = sync_connection_budget(config)
        if self._limiter and self._limiter.enabled:
            self._print(
                f"[dim]Speed limit: {format_size(self._limiter.max_rate)}/s[/dim]"
            )
        # Optional per-task cap, layered *on top of* the global limiter.  It is
        # ``None`` unless a caller opts in via :meth:`set_task_speed_limit`, so
        # the CLI/TUI and every existing download path are byte-for-byte
        # unaffected.
        self._task_limiter: Optional[BandwidthLimiter] = None
        # Per-download diagnostics + the "first body byte arrived" signal.
        # Reset at the start of every download_file() call, because one
        # controller instance may serve several downloads (CLI, tests).
        self._timeline: Optional[DownloadTimeline] = None
        # Structured diagnostics for the most recent download_file() call.
        # None until a download has run.
        self._report: Optional[DownloadReport] = None
        self._report_state: Dict[str, Any] = {}
        self._first_byte = threading.Event()
        self._first_byte_hook: Optional[Callable[[], None]] = None
        # Seconds the metadata probe already spent on this download, subtracted
        # from the pre-first-byte budget so the two phases cannot add up.
        self._startup_credit: float = 0.0

    # ------------------------------------------------------------------ #
    # Per-download phase reporting
    # ------------------------------------------------------------------ #

    def _fire_first_byte(self) -> None:
        """Announce the first body byte exactly once per download.

        The UI must be able to tell "still connecting" from "transferring": the
        task stays in STARTING until this fires, and only then becomes
        DOWNLOADING.  Reporting DOWNLOADING before the first byte is what made a
        dead server look like a running download for a minute and a half.
        """
        if self._first_byte.is_set():
            return
        self._first_byte.set()
        if self._timeline is not None:
            self._timeline.mark("first_byte")
        hook = self._first_byte_hook
        if hook is not None:
            try:
                hook()
            except Exception:
                pass

    #: Outcomes that mean the download did not finish — the reason is part of
    #: the message for these, because "failed in 2.48s" without a reason leaves
    #: the user (and the next diagnosis) with nothing to act on.
    _FAILURE_OUTCOMES = frozenset({
        "failed", "blocked", "merge-failed", "checksum-mismatch",
    })

    def _build_report(self, url: str) -> "DownloadReport":
        """Assemble this attempt's :class:`DownloadReport`.

        Called from a ``finally``, so it must never raise: diagnostics are
        worth having but never worth breaking a download over.  Every field
        comes from state the transfer already maintains, so there is no second
        source of truth to drift out of sync.
        """
        timeline = self._timeline
        marks = timeline.as_dict() if timeline is not None else {}
        state = getattr(self, "_report_state", None) or {}
        parts = state.get("parts") or []
        succeeded = bool(state.get("ok"))

        # A successful transfer deletes its part files, so their sizes cannot be
        # read afterwards.  Use the snapshot taken while they still existed and
        # only fall back to live stats for paths that never reached that point
        # (failures and cancellations, where the parts are still on disk).
        snapshot = state.get("segments")
        if snapshot is not None:
            segments = list(snapshot)
        else:
            segments = [
                SegmentReport(
                    index=p.index,
                    start=p.start,
                    end=p.end,
                    expected=p.size,
                    written=p.downloaded_size,
                    complete=p.is_complete,
                )
                for p in parts
            ]
        written = sum(s.written for s in segments)
        expected = int(state.get("expected_length") or 0)
        length_known = bool(state.get("length_known"))
        gate = str(state.get("validation") or "")

        if not succeeded:
            integrity = "failed"
        elif not length_known:
            # No declared length: nothing independent can confirm the file is
            # whole.  Saying "verified" here would be a lie.
            integrity = "unverifiable"
        elif gate or self.config.verify_size:
            integrity = "verified"
        else:
            integrity = "unverifiable"

        return DownloadReport(
            url=redact_secrets(url),
            host=(urlparse(url).hostname or ""),
            strategy=str(state.get("strategy") or ""),
            range_supported=bool(state.get("range_supported")),
            length_known=length_known,
            expected_length=expected,
            bytes_written=written,
            segments=segments,
            peak_connections=int(state.get("peak_connections") or 0),
            ttfb=marks.get("first_byte"),
            duration=timeline.elapsed if timeline is not None else 0.0,
            integrity=integrity,
            validation=gate,
            failure_reason=("" if succeeded else redact_secrets(self.last_error or "")),
            timeline=marks,
        )

    @property
    def last_report(self) -> Optional["DownloadReport"]:
        """Structured record of the most recent download attempt, or None."""
        return self._report

    def _log_timeline(self, url: str, outcome: str) -> None:
        """Emit one INFO line describing where this download spent its time.

        Deliberately one line per download: enough to tell a slow server from a
        slow engine, cheap enough to leave on in production.  The URL is
        redacted — a signed link keeps its authorisation in the query string.

        Failures also carry :attr:`last_error`.  A link delivered by the browser
        extension is added and run without any dialog, so this line is the only
        place its failure can be explained; without the reason the log showed a
        duration with no cause and a delivery could only be diagnosed by
        reproducing it by hand.
        """
        if self._timeline is None:
            return
        reason = (self.last_error or "").strip()
        if outcome in self._FAILURE_OUTCOMES and reason:
            log.info(
                "download %s %s reason=%s (%s)",
                outcome,
                self._timeline.summary(),
                redact_secrets(reason),
                redact_secrets(url),
            )
            return
        log.info(
            "download %s %s (%s)",
            outcome,
            self._timeline.summary(),
            redact_secrets(url),
        )

    def _save_resume_state(self, state_mgr, url: str, total_size: int,
                           parts: List[DownloadPart], threads: int,
                           entity_guard=None, layout: str = "parts") -> bool:
        """Persist resume state, never letting a sidecar failure abort a transfer.

        ``DownloadState.save`` deliberately raises so callers *can* notice, but
        resume state is auxiliary metadata: a disk-full or permission error while
        writing ``*.dlstate`` must not kill a download that is otherwise fine.
        It is logged as a warning (resume would restart from scratch) and the
        transfer continues — the real destination write will report ENOSPC on
        its own if the disk is genuinely full.
        """
        try:
            state_mgr.save(
                url,
                total_size,
                parts,
                threads,
                etag=entity_guard.etag if entity_guard is not None else "",
                last_modified=(
                    entity_guard.last_modified if entity_guard is not None else ""
                ),
                layout=layout,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — auxiliary metadata only
            log.warning(
                "Could not persist resume state for %s (%s); "
                "an interrupted download will restart from scratch",
                redact_secrets(url), exc,
            )
            return False

    def _startup_budget(self) -> float:
        """Wall-clock cap for the whole pre-first-byte phase.

        Derived from the user-visible ``startup_read_timeout`` so that raising
        that setting genuinely tolerates a slower server.  A fixed ceiling would
        silently override it — and a budget shorter than one read timeout is
        self-defeating: the first attempt would be cut off before the server
        could legitimately answer, and the retry would restart the wait from
        zero, failing a healthy server that was about to respond.

        The time the *probe* already spent is subtracted (see
        ``_startup_credit``), so metadata discovery and the transfer share one
        overall deadline.  Before that they were additive — 13 s of probe plus
        20 s of transfer — which is how a dead host reached the reported 35 s
        before the user saw anything.
        """
        try:
            read = float(getattr(self.config, "startup_read_timeout", 0) or 0)
        except (TypeError, ValueError):
            read = 0.0
        budget = max(_STARTUP_TOTAL_BUDGET, read + _STARTUP_BUDGET_MARGIN)
        return max(_STARTUP_TOTAL_BUDGET * 0.4, budget - self._startup_credit)

    def _tighten_read_timeout(self, response, timeout: float) -> None:
        """Shorten this connection's read timeout once the body has started.

        ``requests`` takes a single ``(connect, read)`` pair per request and
        applies it to every read on the connection, so the timeout cannot be
        changed through the public API mid-stream.  Setting it on the socket
        takes effect on the very next read — which is what lets a stall be
        noticed in seconds instead of waiting out the startup budget.

        Every failure path is silent on purpose: if urllib3's internals move,
        the original timeout stands and the transfer is merely slower to give
        up, never wrong.
        """
        sock = _response_socket(response)
        if sock is None:
            return
        try:
            sock.settimeout(timeout)
        except OSError:
            pass

    def _startup_timeouts(self, first_byte_seen: bool,
                          deadline: Optional[float]) -> Optional[tuple]:
        """``(connect, read)`` for this attempt, or ``None`` when out of budget.

        The whole pre-first-byte phase is bounded by ``deadline``, and the first
        attempt is allowed to wait out whatever is left of it: a server that
        needs 15 s for its first byte should simply take 15 s, not be cut off at
        some invented shorter interval and then retried from scratch.

        The bounds cover *every* way the phase can go wrong — a server that says
        nothing, and one that answers 5xx forever — because in both cases not a
        single body byte has arrived and the task would otherwise sit in
        STARTING indefinitely.  Retries still matter for failures that are
        *fast* (a refused connection, an immediate 5xx, a reset), which is
        exactly where the remaining attempts and the 1 s backoff are spent.

        After the first byte the read timeout becomes :data:`STALL_TIMEOUT`
        (or the user's ``stall_timeout``), so a connection that goes silent
        mid-transfer is abandoned in seconds instead of waiting out the generic
        two-minute read timeout.
        """
        if first_byte_seen:
            try:
                stall = float(getattr(self.config, "stall_timeout", 0) or 0)
            except (TypeError, ValueError):
                stall = 0.0
            return (_DOWNLOAD_TIMEOUT[0], max(1.0, stall or STALL_TIMEOUT))
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0.5:
            return None
        try:
            connect_timeout = float(self.config.startup_connect_timeout)
        except (TypeError, ValueError):
            connect_timeout = 10.0
        if remaining is None:
            return (connect_timeout, _DOWNLOAD_TIMEOUT[1])
        return (max(1.0, min(connect_timeout, remaining)), max(1.0, remaining))

    def set_task_speed_limit(self, max_bytes_per_second: int) -> None:
        """Apply a per-download bandwidth cap (0 disables it).

        The cap is enforced in addition to the global ``max_speed_bps`` limit,
        so a single download can be slowed without touching every other
        transfer in the process.
        """
        rate = max(0, int(max_bytes_per_second or 0))
        if rate <= 0:
            self._task_limiter = None
            return
        if self._task_limiter is None:
            self._task_limiter = BandwidthLimiter(rate)
        else:
            self._task_limiter.update_limit(rate)
        self._print(f"[dim]Per-download speed limit: {format_size(rate)}/s[/dim]")

    # ------------------------------------------------------------------ #
    # Request helpers
    # ------------------------------------------------------------------ #
    def smart_request(
        self,
        url: str,
        headers: dict,
        timeout: tuple[int, int] = (30, 120),
        stream: bool = False,
        session=None,
    ) -> requests.Response:
        """Issue a request through the given transport (default: main session).

        ``session`` may be the probe transport (``SessionManager.probe_session``)
        which retries almost nothing — used for pre-first-byte requests so a
        dead-but-accepting server can never multiply its read timeout into a
        multi-minute stall before the first byte.
        """
        sess = session if session is not None else self.session.session
        return sess.get(
            url,
            headers=headers,
            timeout=timeout,
            stream=stream,
            verify=self.config.verify_ssl,
            allow_redirects=True,
        )

    def _notify_progress(
        self,
        progress_callback: Optional[Callable[[int, int], None]],
        completed: int,
        total: int,
    ) -> None:
        """Keep optional UI/reporting callbacks from interrupting a download."""
        if not progress_callback:
            return
        try:
            progress_callback(completed, total)
        except Exception as exc:
            self._print(f"[yellow]Progress listener error ignored: {exc}[/yellow]")

    @staticmethod
    def _notify_status(
        status_callback: Optional[Callable[[str], None]], name: str
    ) -> None:
        """Report a phase transition (e.g. MERGING / VERIFYING) to the queue.

        A failing callback must never interrupt the download, so errors are
        swallowed.
        """
        if not status_callback:
            return
        try:
            status_callback(name)
        except Exception:
            pass

    def _throttle(self, chunk_len: int, control=None) -> None:
        global_limiter = self._limiter
        task_limiter = self._task_limiter
        if global_limiter is None and task_limiter is None:
            return

        def _stop() -> bool:
            # Abort the throttle sleep when paused or cancelled so pause and
            # shutdown stay responsive even mid-throttle.
            if DownloadContext._cancel_event.is_set():
                return True
            if control is not None:
                return bool(control.paused or control.cancelled)
            return False

        # Both caps apply: the global one protects total bandwidth, the
        # per-task one slows this single transfer.  Consuming from the smaller
        # bucket first would starve the other, so always drain both.
        if global_limiter is not None:
            global_limiter.consume(chunk_len, should_stop=_stop)
        if task_limiter is not None:
            task_limiter.consume(chunk_len, should_stop=_stop)

    # ------------------------------------------------------------------ #
    # Per-task control helpers
    #
    # When a caller passes a ``control`` (a ``TaskControl``-like object with
    # ``cancelled`` / ``paused`` / ``wait_if_paused``), the download honours it
    # instead of the process-global ``DownloadContext``, giving every task an
    # isolated pause/cancel lifecycle.  With ``control=None`` the original
    # global behaviour (CLI / TUI) is preserved unchanged.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ctl_cancelled(control) -> bool:
        if control is not None:
            return bool(control.cancelled)
        return DownloadContext.is_cancelled()

    @staticmethod
    def _ctl_wait(control) -> bool:
        """Block while paused; return False when cancelled."""
        if control is not None:
            control.wait_if_paused()
            return not bool(control.cancelled)
        return DownloadContext.wait_if_paused()

    # ------------------------------------------------------------------ #
    # Part download
    # ------------------------------------------------------------------ #
    def download_part(
        self,
        url: str,
        part: DownloadPart,
        progress: Optional[Progress],
        task_id: Optional[int],
        ledger: ProgressLedger,
        speed_tracker: SpeedTracker,
        total_size: int,
        control=None,
        optimizer=None,
        range_state: Optional["_RangeState"] = None,
        entity_guard: Optional["_EntityGuard"] = None,
        meter: Optional[ConnectionMeter] = None,
    ) -> bool:
        """Download one byte-range part with retry, rate-limiting, and perf-optimised progress.

        Hot-path optimisations
        ----------------------
        *   **Lock-free cancellation** — ``DownloadContext._cancel_event.is_set()``
            instead of ``is_cancelled()`` (which acquired a class-level mutex).
        *   **Single progress source** — bytes go to a
            :class:`core.progress.ProgressLedger`, which coalesces delivery and
            guarantees the sink never sees a decreasing counter.  The old
            per-part local accumulator plus a shared dict could deliver values
            out of order, which the task layer misread as a restart.
        *   **Redundant Content-Type check** — reserved for the *first* attempt
            only; subsequent retries skip it because the probe already validated
            the response type.

        When *range_state* is supplied and the server turns out not to honour
        the requested range, the part aborts **before** reading a single body
        byte and records that on the shared state.  Silently consuming a
        full-body ``200`` here is what used to make an N-part download transfer
        the whole file N times over.
        """
        max_retries = max(1, self.config.max_retries)
        # Read in bounded slices: the read size *is* the progress granularity
        # (iter_content blocks until it has n bytes), so a 4 MB chunk made a
        # slow link report nothing for tens of seconds and then jump.
        chunk_size = min(self.config.chunk_size, _READ_SLICE)
        cancel_event = DownloadContext._cancel_event
        startup_attempts = max(
            1,
            min(max_retries, self.config.startup_max_attempts, _STARTUP_MAX_ATTEMPTS),
        )
        # Wall-clock cap for the whole pre-first-byte phase (all attempts).
        startup_deadline = time.monotonic() + self._startup_budget()
        first_byte_seen = False

        for attempt in range(1, max_retries + 1):
            if cancel_event.is_set() or self._ctl_cancelled(control):
                return False
            if range_state is not None and range_state.unusable:
                # Another part already proved the byte-range plan unusable.
                # Stop before spending a request (and a full-body response)
                # confirming it.
                return False
            if not self._ctl_wait(control):
                return False

            if part.is_complete:
                part.done = True
                return True

            # Budget the pre-first-byte phase before spending a request.
            timeouts = self._startup_timeouts(first_byte_seen, startup_deadline)
            if timeouts is None:
                self.last_error = (
                    "Server accepted the connection but sent no data within "
                    f"{self._startup_budget():.0f}s. It may be down, rate-limiting, "
                    "or waiting for a browser session."
                )
                self._print(f"[red]{self.last_error}[/red]")
                log.warning(
                    "TIMELINE %s gave up waiting for a first byte (%s)",
                    self._timeline.summary() if self._timeline else "",
                    redact_secrets(url),
                )
                return False

            existing = part.downloaded_size
            range_start = part.start + existing
            if range_start > part.end:
                part.done = True
                return True

            headers = build_browser_headers(
                url,
                self.config.user_agent,
                range_header=f"bytes={range_start}-{part.end}",
                accept="*/*",
                omit_referer=self.session.should_omit_referer(url),
            )
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "no-cors"
            headers["Sec-Fetch-Site"] = "same-origin"
            headers.pop("Sec-Fetch-User", None)
            headers.pop("Upgrade-Insecure-Requests", None)

            # Take a slot in the process-wide connection budget for the life of
            # this request.  Acquired *inside* the per-download governor slot so
            # the two limits compose: the governor decides how much of this
            # download runs at once, the budget decides how much of the machine
            # is in use overall.
            self._budget.acquire()
            if meter is not None:
                meter.enter()
            try:
                with self.smart_request(url, headers=headers, stream=True,
                                        timeout=timeouts,
                                        session=self.session.probe_session
                                        if not first_byte_seen else None) as response:
                    if self._timeline is not None and not first_byte_seen:
                        self._timeline.mark("headers")
                    if response.status_code not in (200, 206):
                        raise requests.HTTPError(
                            f"HTTP {response.status_code}",
                            response=response,
                        )

                    # The resource must still be the one this transfer started
                    # on.  A publisher that replaces a file mid-download keeps
                    # the same URL and often the same length, so the validator
                    # is the only signal that the bytes changed underneath us.
                    if entity_guard is not None and not entity_guard.observe(
                        response.headers
                    ):
                        if range_state is not None:
                            range_state.mark_unusable()
                        self._print(
                            "[yellow]⚠ The file changed on the server during the "
                            "download — restarting from scratch rather than mixing "
                            "two versions.[/yellow]"
                        )
                        return False

                    # Content-Type check: only on first attempt (probe already
                    # caught the fast-path case; this is a safety net for
                    # token-expiry mid-stream).
                    if attempt == 1:
                        ct = response.headers.get("Content-Type", "")
                        if is_html_error_response(ct, ""):
                            raise requests.HTTPError(
                                "server returned an HTML page instead of the file",
                                response=response,
                            )

                    bytes_remaining = part.end - range_start + 1

                    # ---- Range honesty ------------------------------------
                    # A request counts as "ranged" unless it asks for the whole
                    # entity, so a single-part plan against a server without
                    # range support is not flagged here.
                    ranged_request = range_start > 0 or part.end < total_size - 1

                    if ranged_request and response.status_code == 200:
                        # The server ignored Range and is sending the whole body
                        # from byte 0.  Writing it here would land the wrong
                        # bytes at the wrong offset, and the old skip-and-trim
                        # fallback made EVERY part pull the entire file — an
                        # N-part download cost N× the bandwidth.  Record the
                        # verdict so the orchestrator can restart this transfer
                        # as a single stream, and stop before reading a byte.
                        if range_state is not None:
                            range_state.mark_unusable()
                        return False

                    if response.status_code == 206:
                        # ---- Prove these bytes belong at this offset ------
                        # A 206 is only meaningful if it says *which* bytes it
                        # is carrying.  Without this check a server (or an
                        # intermediary cache) that answers every range from
                        # byte 0 produced a file of exactly the right length
                        # built from the same leading bytes repeated once per
                        # part: the download reported success, the size looked
                        # right, and the media file was unplayable past the
                        # first segment.
                        span = _content_range_span(
                            response.headers.get("Content-Range", "")
                        )
                        if span is None:
                            if range_state is not None:
                                range_state.mark_unusable()
                            self._print(
                                f"[yellow]⚠ Part {part.index}: 206 without a usable "
                                "Content-Range — restarting as a single stream."
                                "[/yellow]"
                            )
                            return False

                        cr_start, cr_end, cr_total = span
                        if cr_start != range_start:
                            if range_state is not None:
                                range_state.mark_unusable()
                            self._print(
                                f"[yellow]⚠ Part {part.index}: asked for byte "
                                f"{range_start}, server sent {cr_start} — "
                                "restarting as a single stream.[/yellow]"
                            )
                            return False

                        if cr_total and cr_total != total_size:
                            # The entity changed size between the probe and the
                            # transfer, so the planned offsets no longer
                            # describe it.  Rebuilding the plan is the only safe
                            # answer.
                            if range_state is not None:
                                range_state.mark_unusable()
                            return False

                        # The announced body length must match the span it
                        # just declared.  A mismatch means the connection is
                        # going to be short (or padded) — either way the part
                        # cannot be trusted to fill its range exactly.
                        declared = response.headers.get("Content-Length")
                        if declared is not None:
                            try:
                                declared_len = int(declared)
                            except (TypeError, ValueError):
                                declared_len = -1
                            if declared_len != cr_end - cr_start + 1:
                                if range_state is not None:
                                    range_state.mark_unusable()
                                self._print(
                                    f"[yellow]⚠ Part {part.index}: Content-Length "
                                    f"{declared_len} does not match the declared "
                                    f"range {cr_start}-{cr_end} — restarting as a "
                                    "single stream.[/yellow]"
                                )
                                return False

                    if part.shared:
                        # Direct layout: write this range at its absolute
                        # offset in the destination.  The file is opened once
                        # per worker (not per chunk); several workers can hold
                        # independent handles on disjoint ranges safely on
                        # Windows because each write is explicitly positioned.
                        dest_mode = "r+b" if part.path.exists() else "w+b"
                        dest = open(
                            part.path, dest_mode, buffering=_PART_WRITE_BUFFER
                        )
                        dest.seek(range_start)
                    else:
                        mode = (
                            "wb"
                            if existing == 0 and range_start == part.start
                            else "ab"
                        )
                        dest = open(part.path, mode, buffering=_PART_WRITE_BUFFER)

                    with dest:
                        for raw_chunk in response.iter_content(chunk_size):
                            if cancel_event.is_set() or self._ctl_cancelled(control):
                                return False

                            # Pause barrier #1: stop at this chunk boundary.
                            # Blocks efficiently (event wait, no busy loop) until
                            # the task is resumed or cancelled.
                            if not self._ctl_wait(control):
                                return False

                            if not raw_chunk:
                                continue

                            # A server may overshoot the end of the requested
                            # range (it is allowed to send up to the end of the
                            # entity); trim so the part holds exactly its range.
                            if len(raw_chunk) > bytes_remaining:
                                raw_chunk = memoryview(raw_chunk)[:bytes_remaining]

                            self._throttle(len(raw_chunk), control)

                            # Pause barrier #2: a pause that arrives while the
                            # thread is inside the (sleeping) throttle must be
                            # honoured BEFORE any bytes hit the disk, so the
                            # transfer cannot keep writing megabytes while the
                            # task reports PAUSED.
                            if not self._ctl_wait(control):
                                return False

                            chunk_len = len(raw_chunk)
                            dest.write(raw_chunk)
                            if part.shared:
                                part.advance(chunk_len)
                            if not first_byte_seen:
                                first_byte_seen = True
                                # ``requests`` applies one timeout to every read
                                # on a connection, so a stall inside the *first*
                                # attempt would otherwise wait out the whole
                                # pre-first-byte budget (15 s by default) before
                                # the stall path could see it.  Now that the body
                                # has started, drop the socket to the stall
                                # timeout so a silent connection is abandoned in
                                # seconds.
                                self._tighten_read_timeout(
                                    response, self._startup_timeouts(True, None)[1]
                                )
                                self._fire_first_byte()
                            bytes_remaining -= chunk_len

                            # One counter, one delivery path.  The ledger
                            # coalesces by time and by byte threshold, and
                            # serialises delivery so the sink can never observe
                            # a value lower than one it already saw.
                            ledger.add(chunk_len)
                            speed_tracker.add(chunk_len)
                            if progress is not None and task_id is not None:
                                progress.update(task_id, advance=chunk_len)

                            if bytes_remaining <= 0:
                                break

                if part.is_complete:
                    part.done = True
                    return True

            except (requests.RequestException, OSError, ConnectionError) as exc:
                retryable, status = _is_retryable_exception(exc)
                # A 401/403 on a request that carried a Referer may be *caused*
                # by that Referer: some servers reject any request bearing one
                # while answering the identical request without it.  Disprove it
                # once per host, immediately and without spending the retry
                # ladder — the attempt counter still advances, so this can never
                # loop.
                if (
                    status in (401, 403)
                    and not self.session.should_omit_referer(url)
                    and self.session.mark_no_referer(url)
                ):
                    self._print(
                        "[yellow]Server rejected the request while a Referer "
                        "was sent; retrying without one.[/yellow]"
                    )
                    continue
                # Every retry — stall, reset, timeout — is evidence that the
                # sockets this plan holds are not being served.  The controller
                # halves on a storm; a stable-but-slow link never collapses, so
                # this is the only signal that can shrink the plan there.
                if optimizer is not None:
                    try:
                        optimizer.note_retry()
                    except Exception:  # pragma: no cover
                        pass
                # Every byte the ledger counted is already in the part file (the
                # handle closes on the way out) and the retry resumes from it,
                # so there is nothing to reconcile here any more — the old
                # "flush the local tail" step existed only because the counter
                # was batched in user space.
                #
                # A read timeout *after* the first byte means the connection
                # went silent: the server is not rejecting us, so this is not a
                # reason to halve concurrency (which on_server_error does for
                # status codes and refusals).  Re-issue the range from the
                # offset already on disk and keep the connection count.
                stalled = first_byte_seen and _is_timeout_like(exc)
                if optimizer is not None and not stalled:
                    try:
                        optimizer.on_server_error(status)
                    except Exception:
                        pass
                # Pre-first-byte attempts are capped: a server that never
                # delivers a byte within the startup budget should fail fast
                # instead of retrying for minutes.
                if not retryable or attempt >= max_retries or (
                    not first_byte_seen and attempt >= startup_attempts
                ):
                    detail = f" (HTTP {status})" if status else ""
                    self.last_error = friendly_error_message(
                        exc, status, will_retry=False
                    )
                    # Diagnostics name the exact range and the attempt budget,
                    # so a failure report is actionable rather than "part
                    # failed".  URL query strings are redacted: a signed link's
                    # authorisation lives there and must not reach the log.
                    self._print(
                        f"[red]Part {part.index} "
                        f"(bytes {range_start}-{part.end}, "
                        f"attempt {attempt}/{max_retries}){detail} failed: "
                        f"{redact_secrets(str(exc))}"
                    )
                    return False
                delay = _retry_delay(
                    attempt,
                    self.config,
                    status,
                    started=first_byte_seen,
                    retry_after=_retry_after_seconds(exc),
                )
                if stalled:
                    # A silent socket is a transport hiccup, not a rejection:
                    # do not walk the exponential ladder, and say what happened
                    # so a user who sees a dip understands it.
                    delay = min(delay, _STALL_RETRY_DELAY)
                    self._print(
                        f"[yellow]Part {part.index}: no data for "
                        f"{self._startup_timeouts(True, None)[1]:.0f}s — "
                        f"resuming from byte {part.start + part.downloaded_size}"
                        "[/yellow]"
                    )
                if not first_byte_seen:
                    # No body byte yet: these attempts are cheap, so never sleep
                    # the exponential ladder's long tail and never sleep past the
                    # phase budget.
                    delay = min(delay, _STARTUP_MAX_BACKOFF)
                    if time.monotonic() + delay > startup_deadline:
                        self.last_error = (
                            "Server accepted the connection but sent no data within "
                            f"{self._startup_budget():.0f}s. It may be down, "
                            "rate-limiting, or waiting for a browser session."
                        )
                        self._print(f"[red]{self.last_error}[/red]")
                        return False
                if not _interruptible_sleep(delay, control):
                    return False
                continue
            finally:
                if meter is not None:
                    meter.exit()
                self._budget.release()

        return False

    # ------------------------------------------------------------------ #
    # Single-thread download
    # ------------------------------------------------------------------ #
    def single_thread_download(
        self,
        url: str,
        file_path: Path,
        ledger: ProgressLedger,
        control=None,
        meter: Optional[ConnectionMeter] = None,
    ) -> bool:
        tmp_path = file_path.with_suffix(file_path.suffix + self.config.temp_extension)
        speed_tracker = SpeedTracker(
            window_size=self.config.speed_window_size,
            sample_interval=self.config.speed_sample_interval,
        )
        max_retries = max(1, self.config.max_retries)
        cancel_event = DownloadContext._cancel_event
        # Bounded read slices: the read size is the progress granularity, so a
        # 4 MB chunk left the UI blind for a whole chunk's worth of transfer.
        chunk_size = min(self.config.chunk_size, _READ_SLICE)
        startup_attempts = max(
            1,
            min(max_retries, self.config.startup_max_attempts, _STARTUP_MAX_ATTEMPTS),
        )
        # Wall-clock cap for the whole pre-first-byte phase (all attempts), so a
        # server that accepts the connection and then says nothing fails in
        # seconds with a specific error instead of stalling for ~90 s.
        startup_deadline = time.monotonic() + self._startup_budget()
        first_byte_seen = False

        for attempt in range(1, max_retries + 1):
            if cancel_event.is_set() or self._ctl_cancelled(control):
                return False
            if not self._ctl_wait(control):
                return False

            timeouts = self._startup_timeouts(first_byte_seen, startup_deadline)
            if timeouts is None:
                self.last_error = (
                    "Server accepted the connection but sent no data within "
                    f"{self._startup_budget():.0f}s. It may be down, rate-limiting, "
                    "or waiting for a browser session."
                )
                self._print(f"[red]{self.last_error}[/red]")
                log.warning(
                    "TIMELINE gave up waiting for a first byte (%s)",
                    self._timeline.summary() if self._timeline else "",
                )
                return False

            resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0
            range_header = f"bytes={resume_from}-" if resume_from > 0 else None
            headers = build_browser_headers(
                url,
                self.config.user_agent,
                range_header=range_header,
                accept="*/*",
                omit_referer=self.session.should_omit_referer(url),
            )

            self._budget.acquire()
            if meter is not None:
                meter.enter()
            try:
                with self.smart_request(url, headers=headers, stream=True,
                                        timeout=timeouts,
                                        session=self.session.probe_session
                                        if not first_byte_seen else None) as response:
                    if self._timeline is not None and not first_byte_seen:
                        self._timeline.mark("headers")
                    if response.status_code not in (200, 206):
                        response.raise_for_status()

                    ct = response.headers.get("Content-Type", "")
                    if is_html_error_response(ct, ""):
                        raise requests.HTTPError(
                            "server returned an HTML page instead of the file",
                            response=response,
                        )

                    if response.status_code == 200 and resume_from > 0:
                        tmp_path.unlink(missing_ok=True)
                        resume_from = 0

                    if response.status_code == 206 and resume_from > 0:
                        cr_start = _content_range_start(
                            response.headers.get("Content-Range", "")
                        )
                        if cr_start is not None and cr_start != resume_from:
                            # The server resumed at a different offset than the
                            # one requested.  Appending would interleave two
                            # different byte ranges into one file, so start this
                            # attempt over from zero instead.
                            self._print(
                                f"[yellow]⚠ Server returned range starting at "
                                f"{cr_start} instead of {resume_from}; "
                                "restarting this attempt.[/yellow]"
                            )
                            tmp_path.unlink(missing_ok=True)
                            resume_from = 0

                    total = int(response.headers.get("Content-Length", 0))
                    if response.status_code == 206:
                        cr = response.headers.get("Content-Range", "")
                        if "/" in cr:
                            try:
                                total = int(cr.split("/")[1])
                            except ValueError:
                                pass
                        total = max(total, resume_from)

                    progress_context = (
                        create_download_progress(speed_tracker)
                        if self.show_progress
                        else nullcontext(None)
                    )
                    with progress_context as progress:
                        task_id = (
                            progress.add_task(
                                file_path.name[:50],
                                total=total or None,
                                completed=resume_from,
                            )
                            if progress is not None
                            else None
                        )
                        downloaded = resume_from
                        mode = "ab" if resume_from > 0 else "wb"
                        # The single-stream path shares the caller's ledger so a
                        # fallback restart is an explicit, ordered reset rather
                        # than a counter that silently rewinds.
                        ledger.set_total(total)
                        with open(tmp_path, mode, buffering=_PART_WRITE_BUFFER) as dest:
                            for raw_chunk in response.iter_content(chunk_size):
                                if cancel_event.is_set() or self._ctl_cancelled(control):
                                    return False
                                # Pause barrier #1: stop promptly at this chunk
                                # boundary (event wait, no busy loop).
                                if not self._ctl_wait(control):
                                    return False
                                if not raw_chunk:
                                    continue

                                self._throttle(len(raw_chunk), control)

                                # Pause barrier #2: honour a pause that arrives
                                # during the throttle sleep before writing.
                                if not self._ctl_wait(control):
                                    return False

                                dest.write(raw_chunk)
                                chunk_len = len(raw_chunk)
                                downloaded += chunk_len
                                if not first_byte_seen:
                                    first_byte_seen = True
                                    self._fire_first_byte()

                                ledger.add(chunk_len)
                                speed_tracker.add(chunk_len)
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, advance=chunk_len)

                            # Publish the exact final count: the per-chunk
                            # reports are coalesced, so without this the last
                            # few hundred kilobytes never reach the UI.
                            ledger.report(force=True)
                            dest.flush()

                if (
                    self.config.verify_size
                    and total > 0
                    and tmp_path.stat().st_size != total
                ):
                    raise OSError(
                        f"Incomplete download: {tmp_path.stat().st_size}/{total}"
                    )

                safe_rename(tmp_path, file_path)
                self._print(f"[bold green]✓ Saved: {file_path}")
                return True

            except (requests.RequestException, OSError) as exc:
                retryable, status = _is_retryable_exception(exc)
                # Same Referer-rejection fallback as the part path.
                if (
                    status in (401, 403)
                    and not self.session.should_omit_referer(url)
                    and self.session.mark_no_referer(url)
                ):
                    self._print(
                        "[yellow]Server rejected the request while a Referer "
                        "was sent; retrying without one.[/yellow]"
                    )
                    continue
                if not retryable or attempt >= max_retries or (
                    not first_byte_seen and attempt >= startup_attempts
                ):
                    self.last_error = friendly_error_message(
                        exc, status, will_retry=False
                    )
                    detail = f" (HTTP {status})" if status else ""
                    self._print(
                        f"[red]Download failed (attempt {attempt}/{max_retries})"
                        f"{detail}: {redact_secrets(str(exc))}"
                    )
                    return False
                delay = _retry_delay(
                    attempt,
                    self.config,
                    status,
                    started=first_byte_seen,
                    retry_after=_retry_after_seconds(exc),
                )
                if not first_byte_seen:
                    # No body byte yet: these attempts are cheap, so never sleep
                    # the exponential ladder's long tail and never sleep past the
                    # phase budget.
                    delay = min(delay, _STARTUP_MAX_BACKOFF)
                    if time.monotonic() + delay > startup_deadline:
                        self.last_error = (
                            "Server accepted the connection but sent no data within "
                            f"{self._startup_budget():.0f}s. It may be down, "
                            "rate-limiting, or waiting for a browser session."
                        )
                        self._print(f"[red]{self.last_error}[/red]")
                        return False
                if not _interruptible_sleep(delay, control):
                    return False
                continue
            finally:
                if meter is not None:
                    meter.exit()
                self._budget.release()

        return False

    # ------------------------------------------------------------------ #
    # Scheduling
    def _validate_output(
        self,
        file_path: Path,
        expected_size: int,
        expected_hash: str = "",
    ) -> tuple:
        """Final gate: is the file on disk actually the file that was asked for?

        Called once, immediately before a download reports success, so the
        definition of "complete" lives in one place instead of being spread
        across every early-return on the way here.  A download becomes
        ``Completed`` only if this returns ``True``.
        """
        if not file_path.exists():
            return False, "The finished file is missing from disk."
        actual = file_path.stat().st_size
        if actual <= 0:
            return False, "The finished file is empty."
        if expected_size > 0 and actual != expected_size:
            return (
                False,
                f"Integrity check failed: the finished file is {actual} bytes "
                f"but the server declared {expected_size}.",
            )
        if expected_hash:
            try:
                algorithm = detect_hash_algorithm(expected_hash)
            except ValueError as exc:
                return False, str(exc)
            actual_hash = calculate_checksum(file_path, algorithm)
            if actual_hash.lower() != expected_hash.strip().lower():
                return (
                    False,
                    f"Checksum mismatch: expected {expected_hash}, got {actual_hash}.",
                )
        return True, ""

    # ------------------------------------------------------------------ #
    def _fetch_validators(self, url: str) -> tuple:
        """Return ``(ETag, Last-Modified)`` for *url*, or ``("", "")``.

        One byte is enough: the validators live in the response headers.  Any
        failure returns empty strings, which the caller reads as "cannot tell"
        — never as "changed", because refusing every resume after a transient
        network blip would be worse than the risk it avoids.
        """
        headers = build_browser_headers(
            url,
            self.config.user_agent,
            range_header="bytes=0-0",
            accept="*/*",
            omit_referer=self.session.should_omit_referer(url),
        )
        try:
            with self.smart_request(
                url,
                headers=headers,
                stream=True,
                timeout=(
                    float(self.config.probe_connect_timeout),
                    float(self.config.probe_read_timeout),
                ),
                session=self.session.probe_session,
            ) as response:
                return (
                    (response.headers.get("ETag") or "").strip(),
                    (response.headers.get("Last-Modified") or "").strip(),
                )
        except requests.RequestException:
            return "", ""

    def _verify_part_layout(
        self,
        url: str,
        parts: List[DownloadPart],
        total_size: int,
    ) -> tuple:
        """Cross-check the assembled byte layout against the server itself.

        Header validation proves a 206 *claims* to carry the requested range;
        it cannot prove the body is really those bytes.  A server that honours
        the header but always sends from offset 0 produces a file of exactly
        the right length made of repeated leading bytes — right size, wrong
        content, which is precisely the failure that made finished movies stop
        playing partway through.

        The check is one small ranged request straddling the first part
        boundary, compared byte-for-byte against what the parts actually hold.
        That catches a body/offset mismatch, a part written in the wrong order,
        and a gap at the seam, for the cost of ~64 bytes and one round trip.

        Deliberately best-effort: if the probe itself cannot be made (server
        refuses the range, network hiccup, rate limit) the download is *not*
        failed on suspicion — we simply cannot prove it either way, and saying
        so is better than inventing a verdict.
        """
        if len(parts) < 2 or total_size <= 0:
            return True, ""

        ordered = sorted(parts, key=lambda p: p.start)
        seam = ordered[0].end
        window = 64
        start = max(0, seam - window // 2)
        end = min(total_size - 1, start + window - 1)
        if end <= start:
            return True, ""

        headers = build_browser_headers(
            url,
            self.config.user_agent,
            range_header=f"bytes={start}-{end}",
            accept="*/*",
            omit_referer=self.session.should_omit_referer(url),
        )
        try:
            response = self.smart_request(
                url,
                headers=headers,
                stream=True,
                timeout=(
                    float(self.config.probe_connect_timeout),
                    float(self.config.probe_read_timeout),
                ),
                session=self.session.probe_session,
            )
        except requests.RequestException as exc:
            log.debug("layout probe skipped (%s)", redact_secrets(str(exc)))
            return True, ""

        try:
            if response.status_code != 206:
                # The server will not serve this range on demand; the transfer
                # itself already validated every part it did serve.
                return True, ""
            span = _content_range_span(response.headers.get("Content-Range", ""))
            if span is None or span[0] != start:
                return True, ""
            probe = response.content[: end - start + 1]
        except requests.RequestException:
            return True, ""
        finally:
            response.close()

        if len(probe) != end - start + 1:
            return True, ""

        # Compare against the bytes the parts actually hold at that offset.
        assembled = bytearray()
        position = start
        for part in ordered:
            if position > end:
                break
            if not (part.start <= position <= part.end):
                continue
            offset = position - part.start
            take = min(part.size - offset, end - position + 1)
            try:
                with open(part.path, "rb") as handle:
                    # A direct-write part shares the destination file, so its
                    # intra-part offset must be translated to the file's
                    # absolute offset.  Legacy .partN files stay at zero.
                    handle.seek(part.start + offset if part.shared else offset)
                    assembled += handle.read(take)
            except OSError:
                return True, ""
            position += take

        if len(assembled) != len(probe):
            return True, ""
        if bytes(assembled) != probe:
            return (
                False,
                "Integrity check failed: the downloaded bytes around offset "
                f"{start} do not match what the server returns for that range. "
                "The file would have been the right size but the wrong content.",
            )
        return True, ""

    def wait_for_schedule(self, control=None) -> None:
        target = self.config.get_schedule_datetime()
        if not target:
            return
        now = datetime.now()
        if target > now:
            self._print(
                f"\n[bold yellow]Scheduled download waiting until: "
                f"{target.strftime('%Y-%m-%d %H:%M:%S')}[/bold yellow]"
            )
            while datetime.now() < target:
                if self._ctl_cancelled(control):
                    return
                time.sleep(0.5)

# ------------------------------------------------------------------ #
    # Main orchestration
    # ------------------------------------------------------------------ #
    def download_file(
        self,
        url: str,
        directory: Path,
        verify_checksum: bool = False,
        expected_hash: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        control=None,
        pre_analysis=None,
        status_callback: Optional[Callable[[str], None]] = None,
        path_callback: Optional[Callable[[str], None]] = None,
        smart_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Download one URL, always leaving a diagnostics record behind.

        This is a thin wrapper so the record is produced for *every* exit —
        including the early returns (cancellation, probe failure, the
        single-stream path) that happen before the transfer's own ``try``
        block begins and would otherwise leave ``last_report`` at ``None``.
        """
        self._report = None
        # One meter per attempt.  It counts real simultaneous requests, which
        # is the honest replacement for the harness's server-side peak counter.
        self._meter = ConnectionMeter()
        self._report_state = {
            "strategy": "", "range_supported": False, "expected_length": 0,
            "length_known": False, "parts": [], "validation": "",
        }
        # Register this transfer so every download sees the same fair share of
        # the process-wide connection budget (see ConnectionBudget.fair_share).
        try:
            self._budget.enter_task()
        except Exception:  # pragma: no cover - bookkeeping must never fail a download
            pass
        # Bound before the call: if the implementation raises, the ``finally``
        # below still needs a value, and an UnboundLocalError there would mask
        # the real exception.
        outcome = False
        try:
            outcome = self._download_file_impl(
                url, directory, verify_checksum, expected_hash,
                progress_callback, control, pre_analysis, status_callback,
                path_callback, smart_callback,
            )
            return outcome
        finally:
            try:
                self._budget.exit_task()
            except Exception:  # pragma: no cover
                pass
            try:
                self._report_state["peak_connections"] = self._meter.peak
            except Exception:  # pragma: no cover - diagnostics only
                pass
            # The return value is the only authoritative success signal: the
            # timeline's "done" mark also fires on a failed single-stream
            # attempt, so inferring success from it would mislabel failures.
            self._report_state["ok"] = bool(outcome)
            try:
                self._report = self._build_report(url)
            except Exception:  # pragma: no cover - diagnostics are best-effort
                log.debug("could not build download report", exc_info=True)

    def _download_file_impl(
        self,
        url: str,
        directory: Path,
        verify_checksum: bool = False,
        expected_hash: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        control=None,
        pre_analysis=None,
        status_callback: Optional[Callable[[str], None]] = None,
        path_callback: Optional[Callable[[str], None]] = None,
        smart_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        # Per-download diagnostics and the first-byte signal start here, so a
        # reused controller instance never inherits the previous run's state.
        # (_report / _report_state are reset by the download_file wrapper.)
        self._timeline = DownloadTimeline()
        self._first_byte = threading.Event()
        self._first_byte_hook = (
            (lambda: self._notify_status(status_callback, "DOWNLOADING"))
            if status_callback is not None
            else None
        )
        self._timeline.mark("requested")
        self._startup_credit = float(
            getattr(pre_analysis, "probe_seconds", 0.0) or 0.0
        )
        self.wait_for_schedule(control)
        if self._ctl_cancelled(control):
            self._print("[yellow]Download cancelled before it started.[/yellow]")
            DownloadContext.clear()
            return False
        # An unusable destination must fail this download, not raise out of it.
        # ``mkdir`` raises NotADirectoryError when a path component is a file,
        # PermissionError for a read-only folder, and OSError for a bad drive —
        # all of which are the user's situation to fix, not a crash to surface
        # as a traceback.  ``last_error`` carries the reason to the UI.
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = (
                f"Cannot use the destination folder {directory}: {exc.strerror or exc}"
            )
            self._print(f"[red]{self.last_error}[/red]")
            DownloadContext.clear()
            return False

        # Use a fresh pre-analysis (ANALYZING step) when available to avoid a
        # second network probe; otherwise probe here as before.  A FAILED probe
        # is never a hard gate: metadata discovery must not prevent the
        # transfer from starting, so we fall back to a direct download.
        resolved = ""
        if pre_analysis is not None and getattr(pre_analysis, "ok", False):
            reachable = True
            total_size = int(pre_analysis.total_size or 0)
            supports_range = bool(pre_analysis.supports_range)
            filename = getattr(pre_analysis, "filename", "") or ""
            error = ""
            resolved = str(getattr(pre_analysis, "final_url", "") or "")
        elif pre_analysis is not None:
            # The queue's ANALYZING step already probed this URL and failed —
            # do not re-probe; proceed straight to a direct download attempt.
            reachable = False
            total_size = 0
            supports_range = False
            filename = ""
            error = str(getattr(pre_analysis, "error", "") or "")
        else:
            from core.analyzer import analyze_url as _analyze_url

            self._print("[dim]Probing URL...[/dim]")
            analysis = _analyze_url(url, self.config, self.session)
            reachable = bool(analysis.ok)
            total_size = int(analysis.total_size or 0)
            supports_range = bool(analysis.supports_range)
            filename = analysis.filename or ""
            error = analysis.error or ""
            resolved = str(getattr(analysis, "final_url", "") or "")

        # Metadata is resolved (or deliberately skipped): from here on the only
        # thing that can delay the first byte is the transfer request itself.
        self._timeline.mark("metadata")

        if not reachable:
            if self._ctl_cancelled(control):
                DownloadContext.clear()
                self._print("[yellow]Download cancelled while checking the link.[/yellow]")
                self.last_error = "Cancelled"
                return False
            # Security blocks (private/local targets etc.) are NEVER bypassed
            # by the direct-download fallback — only metadata failures are.
            from core.security import validate_download_url

            sec_ok, sec_err = validate_download_url(
                url, block_private=self.config.block_private_urls
            )
            if not sec_ok:
                self.last_error = sec_err
                self._print(f"[red]{sec_err}[/red]")
                self._log_timeline(url, "blocked")
                return False
            self._print(
                f"[yellow]Probe failed ({error or 'unreachable'}); "
                "attempting direct download.[/yellow]"
            )
            total_size = 0
            supports_range = False
            if not filename:
                filename = _fallback_filename(url)

        if self._ctl_cancelled(control):
            self._print("[yellow]Download cancelled while checking the link.[/yellow]")
            DownloadContext.clear()
            self.last_error = "Cancelled"
            self._log_timeline(url, "cancelled")
            return False

        self._report_state.update(
            range_supported=bool(supports_range),
            expected_length=int(total_size or 0),
            length_known=bool(total_size),
            strategy="ranged" if supports_range and total_size else "single-stream",
        )

        # Redirect-chain reuse: when the probe resolved the URL to a final
        # destination, transfer against THAT URL directly instead of walking
        # the redirect chain a second time.  Task-scoped and strictly
        # validated: only http/https, and the resolved destination must pass
        # the same security checks as the original.  Anything else falls back
        # to the original URL (existing behaviour).
        transfer_url = url
        if resolved and resolved != url:
            from core.security import validate_download_url

            sec_ok, _ = validate_download_url(
                resolved, block_private=self.config.block_private_urls
            )
            if sec_ok and resolved.startswith(("http://", "https://")):
                transfer_url = resolved
                self._print(f"[dim]Using resolved URL: {redact_secrets(resolved)}[/dim]")
            else:
                self._print(
                    f"[dim]Resolved URL rejected by security checks; "
                    "using original URL.[/dim]"
                )

        if filename:
            self._print(f"[green]✓ Detected file: {filename}[/green]")
        if total_size > 0:
            self._print(f"[green]✓ File size: {format_size(total_size)}[/green]")
        if supports_range:
            self._print("[green]✓ Server supports resume (range requests)[/green]")
        else:
            self._print(
                "[yellow]⚠ Server does not support range requests — "
                "single-thread mode[/yellow]"
            )

        # A direct-layout resume keeps its partial bytes in the destination
        # itself.  If the matching .dlstate exists, that file belongs to this
        # download and must be reused; the normal "avoid overwriting" rename
        # would strand it as file (1).bin and silently restart the transfer.
        resume_candidate = directory / fit_filename(directory, filename)
        resume_candidate_state = resume_candidate.with_suffix(
            resume_candidate.suffix + self.config.state_extension
        )
        if resume_candidate_state.exists():
            file_path = resume_candidate
        else:
            file_path = unique_filepath(directory, filename)
        if file_path.name != filename:
            self._print(f"[yellow]⚠ Using unique path: {file_path.name}[/yellow]")
        self._notify_status(path_callback, str(file_path))

        state_path = file_path.with_suffix(file_path.suffix + self.config.state_extension)
        can_resume = supports_range and total_size > 0

        # A direct-layout partial file can reach its full logical length once
        # the last range has written its tail, before validation has proved all
        # ranges present.  Its .dlstate is authoritative in that case, so a
        # full-sized file with a live state must never be treated as complete.
        if (
            file_path.exists()
            and total_size > 0
            and file_path.stat().st_size == total_size
            and not state_path.exists()
        ):
            self._print(f"[green]✓ Already complete: {file_path}")
            self._timeline.mark("done")
            self._log_timeline(url, "already-complete")
            return True

        # The transfer request is about to be issued.  Report CONNECTING rather
        # than DOWNLOADING: no body byte exists yet, and the task stays in
        # STARTING until one arrives, so a silent server cannot masquerade as a
        # running transfer.  (See _fire_first_byte.)
        self._notify_status(status_callback, "CONNECTING")
        self._timeline.mark("connect")

        # One progress ledger per download attempt, shared by the ranged and
        # the single-stream paths so a fallback restart is an explicit reset.
        ledger = ProgressLedger(
            total=total_size, initial=0, callback=progress_callback
        )

        if not can_resume:
            self._report_state["strategy"] = "single-stream"
            try:
                ok = self.single_thread_download(
                    transfer_url, file_path, ledger, control=control,
                    meter=self._meter,
                )
                self._timeline.mark("done")
                self._log_timeline(url, "ok" if ok else "failed")
                return ok
            finally:
                # Single-thread downloads do not create a DownloadState, but
                # cancellation and pause controls still use this shared context.
                DownloadContext.clear()

        if self._ctl_cancelled(control):
            self._print("[yellow]Download cancelled before transfer started.[/yellow]")
            DownloadContext.clear()
            self.last_error = "Cancelled"
            return False

        state_mgr = DownloadState(state_path)
        loaded = state_mgr.load()
        if loaded is not None and (
            loaded.url != url or loaded.total_size != total_size
        ):
            # A state file for a different URL or a different length describes a
            # different entity; its offsets mean nothing here.
            loaded = None
        entity_guard = _EntityGuard(
            etag=loaded.etag if loaded is not None else "",
            last_modified=loaded.last_modified if loaded is not None else "",
        )
        if loaded is not None and (loaded.etag or loaded.last_modified):
            # The resource may have been re-published since the state was
            # written.  One tiny range request answers that before a single
            # byte is appended to yesterday's data.
            current_etag, current_modified = self._fetch_validators(transfer_url)
            verdict = loaded.identity_matches(current_etag, current_modified)
            if verdict is False:
                self._print(
                    "[yellow]⚠ The file on the server has changed since this "
                    "download was paused — discarding the partial data and "
                    "starting over.[/yellow]"
                )
                for stale in loaded.parts:
                    stale.path.unlink(missing_ok=True)
                state_mgr.delete()
                loaded = None
                entity_guard = _EntityGuard()
        parts: Optional[List[DownloadPart]] = None
        effective_threads = self.config.num_threads
        optimizer = None
        governor = None
        layout = "parts"            # "parts" = .partN + merge; "direct" = final file

        # ---- Adaptive / Manual connection mode ------------------------
        # "manual" keeps the fixed thread count; every other value selects a
        # mode policy that changes how the adaptive controller behaves, not
        # just how many connections it is allowed.
        if is_adaptive(getattr(self.config, "connection_mode", MANUAL_MODE)):
            from core.optimizer import SmartOptimizer, policy_for

            mode = getattr(self.config, "connection_mode", "auto")
            optimizer = SmartOptimizer(
                max_connections=getattr(self.config, "smart_max_connections", 8),
                adaptive=getattr(self.config, "smart_adaptive", True),
                on_status=smart_callback,
                log=self._print,
                policy=policy_for(mode),
            )
            # Fair share first: the plan width and the starting connection
            # count must already respect the other downloads in flight, or the
            # first thing this transfer does is take every slot.
            try:
                optimizer.set_fair_ceiling(self._budget.fair_share())
            except Exception:  # pragma: no cover
                pass
            effective_threads, governor = optimizer.start(
                total_size, supports_range, self.config.min_part_size
            )
            if self._limiter is not None and self._limiter.enabled:
                optimizer.set_speed_limited(True)
        else:
            effective_threads = self.config.num_threads

        if loaded is not None:
            saved_threads = loaded.num_threads
            old_parts = loaded.parts
            layout = loaded.layout
            if not _parts_within_directory(old_parts, directory):
                self._print(
                    "[yellow]Resume state rejected (invalid part paths). "
                    "Starting fresh.[/yellow]"
                )
                state_mgr.delete()
                loaded = None
                parts = None
            elif layout == "direct":
                # The final file is the resume medium.  A state file written
                # before a crash can be behind what actually reached disk, but
                # never ahead if we clamp each prefix to the file's real tail;
                # re-downloading a few bytes is safe, trusting a stale offset
                # is not.
                available_tail = file_path.stat().st_size if file_path.exists() else 0
                for part in old_parts:
                    reachable = max(0, min(part.end + 1, available_tail) - part.start)
                    part.written = min(part.written, reachable)
                if saved_threads == effective_threads:
                    parts = old_parts
                    self._print("[cyan]Resuming previous download...[/cyan]")
                else:
                    self._print(
                        "[yellow]Thread count changed "
                        f"({saved_threads}→{effective_threads}); "
                        "remapping ranges...[/yellow]"
                    )
                    parts = build_direct_parts(
                        total_size, effective_threads, file_path,
                        self.config.min_part_size,
                    )
                    _carry_direct_coverage(old_parts, parts, available_tail)
            elif saved_threads == effective_threads:
                parts = old_parts
                self._print("[cyan]Resuming previous download...[/cyan]")
            else:
                self._print(
                    f"[yellow]Thread count changed ({saved_threads}→{effective_threads}); "
                    "remapping parts...[/yellow]"
                )
                parts = remap_parts_for_resume(
                    old_parts,
                    total_size,
                    effective_threads,
                    file_path,
                    self.config.min_part_size,
                )
            if parts is not None and parts is not old_parts:
                self._save_resume_state(
                    state_mgr, url, total_size, parts, effective_threads,
                    entity_guard, layout=layout,
                )
        else:
            parts = None

        if parts is None:
            use_direct = supports_range and total_size > 0 and effective_threads > 1
            if use_direct:
                # Write each range straight into its final offset.  There is no
                # whole-file merge to run afterwards, so post processing is
                # validation only.  Resume state keeps per-range prefixes.
                parts = build_direct_parts(
                    total_size, effective_threads, file_path,
                    self.config.min_part_size,
                )
                layout = "direct"
                try:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.touch(exist_ok=True)
                except OSError as exc:
                    self.last_error = (
                        f"Cannot create the destination file {file_path}: {exc}"
                    )
                    self._print(f"[red]{self.last_error}[/red]")
                    DownloadContext.clear()
                    return False
            else:
                parts = build_parts(
                    total_size,
                    effective_threads,
                    file_path,
                    self.config.min_part_size,
                )
                layout = "parts"
            effective_threads = len(parts)
            if (
                not is_adaptive(getattr(self.config, "connection_mode", MANUAL_MODE))
                and effective_threads < self.config.num_threads
            ):
                self._print(
                    f"[yellow]⚠ Reduced threads to {effective_threads} "
                    f"for file size {format_size(total_size)}[/yellow]"
                )
            self._save_resume_state(
                state_mgr, url, total_size, parts, effective_threads,
                entity_guard, layout=layout,
            )

        # Expose the segment plan to the diagnostics record while the parts are
        # still live: after a merge they are deleted, so reading their sizes
        # later would report zero.
        self._report_state["parts"] = parts

        # NOTE: DOWNLOADING is deliberately *not* announced here.  The engine
        # announces it from _fire_first_byte() when the first body byte lands,
        # so the task stays in STARTING while the parts are still connecting.
        # Announcing it here made a silent server look like a running transfer.
        DownloadContext.begin(
            state_mgr, url, total_size, parts, effective_threads, layout=layout
        )

        already_bytes = sum(p.downloaded_size for p in parts)
        pending_parts = [p for p in parts if not p.is_complete]

        speed_tracker = SpeedTracker(
            window_size=self.config.speed_window_size,
            sample_interval=self.config.speed_sample_interval,
        )
        speed_tracker.seed(already_bytes)

        # A direct layout has no .partN files whose length can recover progress
        # after SIGKILL.  Checkpoint the per-range prefixes periodically; a
        # crash can lose at most one interval, and conservative clamping on
        # resume makes even a stale snapshot safe to continue from.
        checkpoint_stop = threading.Event()
        checkpoint_thread: Optional[threading.Thread] = None

        def _stop_checkpoint() -> None:
            checkpoint_stop.set()
            if checkpoint_thread is not None:
                checkpoint_thread.join(timeout=2)

        if layout == "direct" and pending_parts:
            def _checkpoint_loop() -> None:
                while not checkpoint_stop.wait(5.0):
                    self._save_resume_state(
                        state_mgr, url, total_size, parts, effective_threads,
                        entity_guard, layout=layout,
                    )

            checkpoint_thread = threading.Thread(
                target=_checkpoint_loop, name="n13-checkpoint", daemon=True
            )
            checkpoint_thread.start()

        try:
            if pending_parts:
                progress_context = (
                    create_download_progress(speed_tracker)
                    if self.show_progress
                    else nullcontext(None)
                )
                with progress_context as progress:
                    task_id = (
                        progress.add_task(
                            file_path.name[:50],
                            total=total_size,
                            completed=already_bytes,
                        )
                        if progress is not None
                        else None
                    )

                    # Smart mode: feed the optimizer from the (coalesced)
                    # progress path so it observes aggregate throughput.
                    if optimizer is not None:
                        _base_cb = progress_callback

                        def _smart_cb(completed: int, total: int) -> None:
                            try:
                                optimizer.observe(completed, total)
                            except Exception:
                                pass
                            if _base_cb:
                                _base_cb(completed, total)

                        progress_callback = _smart_cb

                    # The single source of truth for "bytes written" during
                    # this transfer.  Monotonic by construction, coalesced, and
                    # serialised so the UI can never see it go backwards.
                    # Reuse the attempt's ledger so a later single-stream
                    # fallback resets the same counter the UI is watching.
                    ledger.set_callback(progress_callback)
                    ledger.set_total(total_size)
                    ledger.raise_to(already_bytes)
                    ledger.report(force=True)

                    failed_parts: List[DownloadPart] = []
                    pool = _new_part_pool(
                        max(len(pending_parts), self.config.num_threads)
                    )
                    range_state = _RangeState()

                    def _run_part(part: DownloadPart) -> bool:
                        if governor is not None:
                            governor.acquire()
                        try:
                            return self.download_part(
                                transfer_url,
                                part,
                                progress,
                                task_id,
                                ledger,
                                speed_tracker,
                                total_size,
                                control,
                                optimizer=optimizer,
                                range_state=range_state,
                                entity_guard=entity_guard,
                                meter=self._meter,
                            )
                        finally:
                            # Reconcile upwards against what is actually on
                            # disk: the part handles are buffered, so the sum of
                            # part sizes can lag the ledger.  Never lower it —
                            # that would make the bar jump backwards when one
                            # part finishes while another still has data in
                            # flight.
                            ledger.raise_to(sum(p.downloaded_size for p in parts))
                            if governor is not None:
                                governor.release()

                    try:
                        futures = {pool.submit(_run_part, p): p for p in pending_parts}
                        for future in as_completed(futures):
                            part = futures[future]
                            try:
                                if not future.result():
                                    failed_parts.append(part)
                            except Exception as exc:
                                self._print(f"[red]Part {part.index} error: {exc}")
                                failed_parts.append(part)
                    finally:
                        # Every future has been collected (or the transfer was
                        # abandoned); the executor has nothing left to own.
                        pool.shutdown(wait=False)

                    completed = max(
                        ledger.completed, sum(p.downloaded_size for p in parts)
                    )
                    if progress is not None and task_id is not None:
                        progress.update(
                            task_id, completed=completed, total=total_size
                        )

                    ledger.report(force=True)
                    self._timeline.mark("transferred")

                if self._ctl_cancelled(control):
                    self._save_resume_state(state_mgr, url, total_size, parts, effective_threads, entity_guard, layout=layout)
                    self.last_error = "Cancelled"
                    self._print(
                        "[yellow]Download cancelled. State saved for resume.[/yellow]"
                    )
                    self._log_timeline(url, "cancelled")
                    return False

                if range_state.unusable:
                    # The byte-range plan does not describe what the server is
                    # actually serving.  Everything derived from it — part
                    # offsets, part files, resume state — is now meaningless, so
                    # discard it and fetch the entity as one stream, which reads
                    # the authoritative size straight from the response.
                    self._print(
                        "[yellow]⚠ Server does not honour byte ranges for this "
                        "file — switching to a single stream.[/yellow]"
                    )
                    _stop_checkpoint()
                    for stale in parts:
                        stale.path.unlink(missing_ok=True)
                    state_mgr.delete()
                    # The ranged plan is void; the record must say what actually
                    # produced the file.
                    self._report_state.update(strategy="single-stream", parts=[])
                    try:
                        # The plan was abandoned: reset the shared counter so the
                        # bar restarts at zero instead of sitting at the bytes
                        # the discarded parts had collected.
                        ledger.reset(0)
                        ok = self.single_thread_download(
                            transfer_url, file_path, ledger, control=control,
                            meter=self._meter,
                        )
                        self._timeline.mark("done")
                        self._log_timeline(url, "ok (single-stream fallback)")
                        return ok
                    finally:
                        DownloadContext.clear()

                if failed_parts:
                    self._save_resume_state(state_mgr, url, total_size, parts, effective_threads, entity_guard, layout=layout)
                    self.last_error = (
                        self.last_error
                        or f"{len(failed_parts)} part(s) failed — will resume on retry"
                    )
                    self._print(
                        f"[bold red]{len(failed_parts)} part(s) failed. "
                        "Re-run to resume."
                    )
                    return False

                if not all(p.is_complete for p in parts):
                    self._save_resume_state(state_mgr, url, total_size, parts, effective_threads, entity_guard, layout=layout)
                    self.last_error = self.last_error or "Some parts incomplete — will resume on retry"
                    self._print("[bold red]Some parts incomplete. Re-run to resume.")
                    return False

            # Every part claims to be full.  Prove the layout before assembling
            # it: the parts are the only place a byte can be at the wrong
            # offset while the total still adds up.
            if self.config.verify_size and len(parts) > 1:
                self._notify_status(status_callback, "VERIFYING")
                layout_ok, layout_err = self._verify_part_layout(
                    transfer_url, parts, total_size
                )
                if not layout_ok:
                    self._save_resume_state(
                        state_mgr, url, total_size, parts, effective_threads,
                        entity_guard, layout=layout,
                    )
                    self.last_error = layout_err
                    self._print(f"[bold red]{layout_err}")
                    self._log_timeline(url, "layout-mismatch")
                    return False

            # Freeze the per-segment accounting here: after a legacy merge the
            # part files are deleted, and their sizes are the only record of
            # what each segment actually delivered.
            self._report_state["segments"] = [
                SegmentReport(
                    index=p.index, start=p.start, end=p.end,
                    expected=p.size, written=p.downloaded_size,
                    complete=p.is_complete,
                )
                for p in parts
            ]
            if layout == "direct":
                # Bytes already live at their final offsets.  The completion
                # gate below reads the destination itself, so no extra
                # full-file copy is needed.
                ok, merge_err = True, ""
            else:
                self._notify_status(status_callback, "MERGING")
                self._timeline.mark("merge")
                merge_expected = total_size if self.config.verify_size else 0
                ok, merge_err = merge_parts(
                    parts, file_path, self.config.buffer_size,
                    expected_size=merge_expected,
                )
                if not ok:
                    self._save_resume_state(
                        state_mgr, url, total_size, parts, effective_threads,
                        entity_guard, layout=layout,
                    )
                    self.last_error = merge_err
                    self._print(f"[red]{merge_err}. Parts preserved for retry.")
                    self._log_timeline(url, "merge-failed")
                    return False

            if verify_checksum and expected_hash:
                self._notify_status(status_callback, "VERIFYING")
                self._timeline.mark("verify")
                # A malformed hash is the *user's* problem, not the file's:
                # report it and leave the download in place.
                try:
                    algorithm = detect_hash_algorithm(expected_hash)
                except ValueError as exc:
                    self.last_error = str(exc)
                    self._print(f"[red]{exc}")
                    self._log_timeline(url, "hash-unsupported")
                    return False
                self._print(f"[cyan]Verifying {algorithm.upper()} checksum...")

            # Single completion gate.  Everything above proved that the parts
            # are complete and correctly placed; this proves the *file on disk*
            # is what the server declared before the download is allowed to
            # report success.  The checksum (when the user supplied one) is
            # folded into the same gate so there is exactly one place that
            # decides "trustworthy".
            output_ok, output_err = self._validate_output(
                file_path,
                total_size if self.config.verify_size else 0,
                expected_hash if (verify_checksum and expected_hash) else "",
            )
            self._report_state["validation"] = (
                "passed" if output_ok else (output_err or "rejected")
            )
            if not output_ok:
                self.last_error = output_err
                self._print(f"[bold red]{output_err}")
                # Legacy parts are still on disk, so a retry can resume.  A
                # direct file that failed the completion gate is not safe to
                # resume: its checksum or length is already known to be wrong,
                # so discard both it and its sidecar and start clean next time.
                _stop_checkpoint()
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if layout == "parts":
                    self._save_resume_state(
                        state_mgr, url, total_size, parts, effective_threads,
                        entity_guard, layout=layout,
                    )
                else:
                    state_mgr.delete()
                self._log_timeline(url, "output-invalid")
                return False
            if verify_checksum and expected_hash:
                self._print("[green]✓ Checksum verified!")

            # Stop checkpointing before the resume sidecar is removed, or a
            # timer firing in the next few milliseconds could recreate it for
            # a download that is already complete.
            _stop_checkpoint()
            if layout == "parts":
                for part in parts:
                    part.path.unlink(missing_ok=True)
            state_mgr.delete()
            self._print(f"[bold green]✓ Saved: {file_path}")
            self._timeline.mark("done")
            self._log_timeline(url, "ok")
            return True

        finally:
            _stop_checkpoint()
            # A direct layout pre-creates the destination so workers can open
            # disjoint offsets.  If no byte ever landed (disk error, immediate
            # cancellation, probe fallback), that zero-length file is a temp
            # artefact, not a download worth resuming.
            if layout == "direct":
                try:
                    if (
                        file_path.exists()
                        and file_path.stat().st_size == 0
                        and not state_path.exists()
                    ):
                        # No resume sidecar was written either.  A real failed
                        # download keeps its sidecar so Remove can clean it up.
                        file_path.unlink(missing_ok=True)
                except OSError:
                    pass
            DownloadContext.clear()


def download_file(
    url: str,
    directory: Path,
    config: AppConfig,
    session_manager: Optional[SessionManager] = None,
    console_print: Optional[Callable[..., None]] = None,
    verify_checksum: bool = False,
    expected_hash: Optional[str] = None,
    control=None,
    pre_analysis=None,
    path_callback: Optional[Callable[[str], None]] = None,
    smart_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Convenience wrapper used by the batch/CLI layers."""
    controller = DownloadController(config, session_manager, console_print)
    return controller.download_file(
        url, directory, verify_checksum, expected_hash, control=control,
        pre_analysis=pre_analysis, path_callback=path_callback,
        smart_callback=smart_callback,
    )
