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

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

import requests
from rich.progress import Progress

from config.settings import AppConfig
from core.context import DownloadContext
from core.merge import merge_parts
from core.parts import DownloadPart, build_parts, remap_parts_for_resume
from core.probe import probe_url
from core.session import SessionManager
from core.speed import SpeedTracker
from core.state import DownloadState
from core.throttle import BandwidthLimiter, get_global_limiter, sync_limiter_from_config
from core.errors import friendly_error_message
from core.utils import (
    build_browser_headers,
    calculate_checksum,
    detect_hash_algorithm,
    format_size,
    is_html_error_response,
    safe_rename,
    unique_filepath,
)
from ui.progress import create_download_progress

# ---------------------------------------------------------------------------
# Shared thread-pool — created once, resized as needed, shared across
# all downloads to avoid ThreadPoolExecutor construction/destruction
# overhead on every call to download_file().
# ---------------------------------------------------------------------------
_SHARED_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()

# Progress flush threshold: local accumulation before hitting shared
# state (reduces lock contention). 256 KB is a good balance — frequent
# enough for responsive UI, infrequent enough to avoid lock storms.
_PROGRESS_FLUSH_THRESHOLD = 256 * 1024

# Max progress callback frequency (Hz).  20 Hz = every 50 ms.
_CALLBACK_INTERVAL = 0.05


def _get_shared_pool(max_workers: int) -> ThreadPoolExecutor:
    """Return the module-level shared thread-pool, resizing if necessary."""
    global _SHARED_POOL
    with _POOL_LOCK:
        if _SHARED_POOL is None:
            _SHARED_POOL = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="n13-dl",
            )
        elif _SHARED_POOL._max_workers < max_workers:
            # Resize by replacing the pool (can't change max_workers on
            # an existing ThreadPoolExecutor).  Old pool will be GC'd
            # after its running tasks finish.
            _SHARED_POOL.shutdown(wait=False)
            _SHARED_POOL = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="n13-dl",
            )
        return _SHARED_POOL


# HTTP status codes that are worth retrying (transient/server-side).
_RETRYABLE_STATUS = frozenset(
    {408, 425, 429, 500, 502, 503, 504}
)
# Status codes that mean "do not bother retrying" (client-side/permanent).
_FATAL_STATUS = frozenset(range(400, 500)) - _RETRYABLE_STATUS

# Write-buffer flush threshold: accumulate chunks in memory and flush to disk
# in larger blocks to reduce syscall overhead on every part download.

# Pre-first-byte retry budget.  Before ANY byte has been received, retries use
# a fast, bounded schedule (0.5 / 1 / 2 s) and a hard attempt cap so a flaky
# server can never stall the download startup for tens of seconds.  Once the
# transfer has begun, the normal (more tolerant) retry behaviour applies.
_STARTUP_RETRY_BASE = 0.5
_STARTUP_RETRY_CAP = 2.0

# Pre-first-byte request timeouts (connect, read) — short so a dead-but-
# accepting endpoint fails within a bounded time.  After the first byte the
# regular (30, 120) download timeout applies.
_DOWNLOAD_TIMEOUT = (30, 120)


def _retry_delay(attempt: int, config: AppConfig, status: Optional[int] = None,
                 started: bool = False) -> float:
    """Compute the next backoff delay, capped and jittered.

    ``started=False`` (no byte received yet) uses a fast bounded schedule —
    the startup must never be gated by a long sleep chain.  ``started=True``
    keeps the configured exponential backoff for in-transfer retries.
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
    return max(0.0, delay)


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
    for server-side or explicit-retry status codes.
    """
    status: Optional[int] = None
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


def _parts_within_directory(parts: List[DownloadPart], directory: Path) -> bool:
    """Reject resume state whose part files escape the download directory."""
    try:
        base = directory.resolve()
        for part in parts:
            part.path.resolve().relative_to(base)
    except (ValueError, OSError):
        return False
    return True


def _fallback_filename(url: str) -> str:
    """Best-effort file name from a URL when the probe supplied none."""
    from urllib.parse import unquote, urlparse

    try:
        name = unquote(urlparse(url).path or "").rstrip("/").split("/")[-1]
        if name and name != "/":
            return name
    except Exception:
        pass
    return "download"


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
        if self._limiter and self._limiter.enabled:
            self._print(
                f"[dim]Speed limit: {format_size(self._limiter.max_rate)}/s[/dim]"
            )

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
        if self._limiter is None:
            return

        def _stop() -> bool:
            # Abort the throttle sleep when paused or cancelled so pause and
            # shutdown stay responsive even mid-throttle.
            if DownloadContext._cancel_event.is_set():
                return True
            if control is not None:
                return bool(control.paused or control.cancelled)
            return False

        self._limiter.consume(chunk_len, should_stop=_stop)

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
        progress_lock: threading.Lock,
        speed_tracker: SpeedTracker,
        total_size: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        shared_progress: Optional[dict] = None,
        control=None,
        optimizer=None,
    ) -> bool:
        """Download one byte-range part with retry, rate-limiting, and perf-optimised progress.

        Hot-path optimisations
        ----------------------
        *   **Lock-free cancellation** — ``DownloadContext._cancel_event.is_set()``
            instead of ``is_cancelled()`` (which acquired a class-level mutex).
        *   **Local byte accumulation** — each thread stores bytes internally and
            flushes to shared progress + speed state only after every
            ``_PROGRESS_FLUSH_THRESHOLD`` bytes (~256 KB).
        *   **Throttled callbacks** — the external ``progress_callback`` is called
            at most 20 times per second regardless of chunk size.
        *   **Redundant Content-Type check** — reserved for the *first* attempt
            only; subsequent retries skip it because the probe already validated
            the response type.
        """
        max_retries = max(1, self.config.max_retries)
        chunk_size = self.config.chunk_size
        cancel_event = DownloadContext._cancel_event
        startup_attempts = max(1, min(max_retries, self.config.startup_max_attempts))
        startup_timeout = (
            self.config.startup_connect_timeout,
            self.config.startup_read_timeout,
        )
        first_byte_seen = False

        for attempt in range(1, max_retries + 1):
            if cancel_event.is_set() or self._ctl_cancelled(control):
                return False
            if not self._ctl_wait(control):
                return False

            if part.is_complete:
                part.done = True
                return True

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
            )
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "no-cors"
            headers["Sec-Fetch-Site"] = "same-origin"
            headers.pop("Sec-Fetch-User", None)
            headers.pop("Upgrade-Insecure-Requests", None)

            try:
                with self.smart_request(url, headers=headers, stream=True,
                                        timeout=startup_timeout if not first_byte_seen
                                        else _DOWNLOAD_TIMEOUT,
                                        session=self.session.probe_session
                                        if not first_byte_seen else None) as response:
                    if response.status_code not in (200, 206):
                        raise requests.HTTPError(
                            f"HTTP {response.status_code}",
                            response=response,
                        )

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
                    skip_bytes = 0

                    # The server ignored our Range header and returned the whole
                    # body starting at byte 0 (a 200 to a ranged request).
                    # Reset any partial data and recompute the skip AFTER all
                    # state is finalised so every part lands on its own range.
                    if response.status_code == 200 and existing > 0:
                        part.path.unlink(missing_ok=True)
                        with progress_lock:
                            if shared_progress is not None:
                                shared_progress["completed"] = max(
                                    0, shared_progress["completed"] - existing
                                )
                        existing = 0
                        range_start = part.start
                        bytes_remaining = part.size
                        skip_bytes = 0

                    if response.status_code == 200 and range_start > 0:
                        # Skip the leading prefix of the full-body response so
                        # this part writes exactly [range_start, end).
                        skip_bytes = range_start
                        bytes_remaining = part.size

                    mode = "wb" if existing == 0 and range_start == part.start else "ab"

                    with open(part.path, mode, buffering=8 * 1024 * 1024) as dest:
                        # Local byte accumulator — reduces lock frequency
                        # by batching small writes into larger flushes.
                        local_bytes = 0
                        last_cb_time = 0.0

                        for raw_chunk in response.iter_content(chunk_size):
                            if cancel_event.is_set() or self._ctl_cancelled(control):
                                # Flush remaining local progress before exit.
                                if local_bytes > 0 and shared_progress is not None:
                                    with progress_lock:
                                        shared_progress["completed"] += local_bytes
                                return False

                            # Pause barrier #1: stop at this chunk boundary.
                            # Blocks efficiently (event wait, no busy loop) until
                            # the task is resumed or cancelled.
                            if not self._ctl_wait(control):
                                # Flush remaining local progress before exit.
                                if local_bytes > 0 and shared_progress is not None:
                                    with progress_lock:
                                        shared_progress["completed"] += local_bytes
                                return False

                            if not raw_chunk:
                                continue

                            # --- skip / trim (handled inline to avoid copies) ---
                            if skip_bytes > 0:
                                if len(raw_chunk) <= skip_bytes:
                                    skip_bytes -= len(raw_chunk)
                                    continue
                                raw_chunk = memoryview(raw_chunk)[skip_bytes:]
                                skip_bytes = 0

                            if len(raw_chunk) > bytes_remaining:
                                raw_chunk = memoryview(raw_chunk)[:bytes_remaining]

                            self._throttle(len(raw_chunk), control)

                            # Pause barrier #2: a pause that arrives while the
                            # thread is inside the (sleeping) throttle must be
                            # honoured BEFORE any bytes hit the disk, so the
                            # transfer cannot keep writing megabytes while the
                            # task reports PAUSED.
                            if not self._ctl_wait(control):
                                # Flush remaining local progress before exit.
                                if local_bytes > 0 and shared_progress is not None:
                                    with progress_lock:
                                        shared_progress["completed"] += local_bytes
                                return False

                            chunk_len = len(raw_chunk)
                            dest.write(raw_chunk)
                            if not first_byte_seen:
                                first_byte_seen = True
                            bytes_remaining -= chunk_len
                            local_bytes += chunk_len

                            # Flush local state to shared counters periodically.
                            if local_bytes >= _PROGRESS_FLUSH_THRESHOLD:
                                with progress_lock:
                                    if progress is not None and task_id is not None:
                                        progress.update(task_id, advance=local_bytes)
                                    if shared_progress is not None:
                                        shared_progress["completed"] += local_bytes
                                        completed_val = shared_progress["completed"]
                                speed_tracker.add(local_bytes)
                                local_bytes = 0

                                # Throttled progress callback (max ~20 Hz).
                                if progress_callback and shared_progress is not None:
                                    now = time.monotonic()
                                    if now - last_cb_time >= _CALLBACK_INTERVAL:
                                        self._notify_progress(
                                            progress_callback, completed_val, total_size
                                        )
                                        last_cb_time = now

                            if bytes_remaining <= 0:
                                break

                        # Final flush of any remaining local bytes.
                        if local_bytes > 0:
                            with progress_lock:
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, advance=local_bytes)
                                if shared_progress is not None:
                                    shared_progress["completed"] += local_bytes
                                    completed_val = shared_progress["completed"]
                            speed_tracker.add(local_bytes)
                            if progress_callback and shared_progress is not None:
                                self._notify_progress(
                                    progress_callback, completed_val, total_size
                                )

                if part.is_complete:
                    part.done = True
                    return True

            except (requests.RequestException, OSError, ConnectionError) as exc:
                retryable, status = _is_retryable_exception(exc)
                if optimizer is not None:
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
                    self.last_error = friendly_error_message(exc, status)
                    self._print(
                        f"[red]Part {part.index} failed after {attempt} "
                        f"attempt(s){detail}: {exc}"
                    )
                    return False
                delay = _retry_delay(attempt, self.config, status,
                                     started=first_byte_seen)
                if not _interruptible_sleep(delay, control):
                    return False
                continue

        return False

    # ------------------------------------------------------------------ #
    # Single-thread download
    # ------------------------------------------------------------------ #
    def single_thread_download(
        self,
        url: str,
        file_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        control=None,
    ) -> bool:
        tmp_path = file_path.with_suffix(file_path.suffix + self.config.temp_extension)
        speed_tracker = SpeedTracker(
            window_size=self.config.speed_window_size,
            sample_interval=self.config.speed_sample_interval,
        )
        max_retries = max(1, self.config.max_retries)
        cancel_event = DownloadContext._cancel_event
        chunk_size = self.config.chunk_size
        startup_attempts = max(1, min(max_retries, self.config.startup_max_attempts))
        startup_timeout = (
            self.config.startup_connect_timeout,
            self.config.startup_read_timeout,
        )
        first_byte_seen = False

        for attempt in range(1, max_retries + 1):
            if cancel_event.is_set() or self._ctl_cancelled(control):
                return False
            if not self._ctl_wait(control):
                return False

            resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0
            range_header = f"bytes={resume_from}-" if resume_from > 0 else None
            headers = build_browser_headers(
                url,
                self.config.user_agent,
                range_header=range_header,
                accept="*/*",
            )

            try:
                with self.smart_request(url, headers=headers, stream=True,
                                        timeout=startup_timeout if not first_byte_seen
                                        else _DOWNLOAD_TIMEOUT,
                                        session=self.session.probe_session
                                        if not first_byte_seen else None) as response:
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
                        with open(tmp_path, mode, buffering=8 * 1024 * 1024) as dest:
                            # Local byte accumulator for batched progress updates.
                            local_bytes = 0
                            last_cb_time = 0.0

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
                                downloaded += len(raw_chunk)
                                local_bytes += len(raw_chunk)
                                if not first_byte_seen:
                                    first_byte_seen = True

                                if local_bytes >= _PROGRESS_FLUSH_THRESHOLD:
                                    if progress is not None and task_id is not None:
                                        progress.update(
                                            task_id, advance=local_bytes
                                        )
                                    speed_tracker.add(local_bytes)
                                    local_bytes = 0

                                    if progress_callback:
                                        now = time.monotonic()
                                        if now - last_cb_time >= _CALLBACK_INTERVAL:
                                            self._notify_progress(
                                                progress_callback, downloaded, total
                                            )
                                            last_cb_time = now

                            # Final flush.
                            if local_bytes > 0:
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, advance=local_bytes)
                                speed_tracker.add(local_bytes)
                                if progress_callback:
                                    self._notify_progress(
                                        progress_callback, downloaded, total
                                    )

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
                if not retryable or attempt >= max_retries or (
                    not first_byte_seen and attempt >= startup_attempts
                ):
                    self.last_error = friendly_error_message(exc, status)
                    self._print(
                        f"[red]Download failed after {attempt} attempt(s): {exc}"
                    )
                    return False
                delay = _retry_delay(attempt, self.config, status,
                                     started=first_byte_seen)
                if not _interruptible_sleep(delay, control):
                    return False
                continue

        return False

    # ------------------------------------------------------------------ #
    # Scheduling
    # ------------------------------------------------------------------ #
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
        self.wait_for_schedule(control)
        if self._ctl_cancelled(control):
            self._print("[yellow]Download cancelled before it started.[/yellow]")
            DownloadContext.clear()
            return False
        directory.mkdir(parents=True, exist_ok=True)

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
            return False

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
                self._print(f"[dim]Using resolved URL: {resolved}[/dim]")
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

        file_path = unique_filepath(directory, filename)
        if file_path.name != filename:
            self._print(f"[yellow]⚠ Using unique path: {file_path.name}[/yellow]")
        self._notify_status(path_callback, str(file_path))

        state_path = file_path.with_suffix(file_path.suffix + self.config.state_extension)
        can_resume = supports_range and total_size > 0

        if file_path.exists() and total_size > 0 and file_path.stat().st_size == total_size:
            self._print(f"[green]✓ Already complete: {file_path}")
            return True

        self._notify_status(status_callback, "DOWNLOADING")

        if not can_resume:
            try:
                return self.single_thread_download(
                    transfer_url, file_path, progress_callback, control=control
                )
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
        parts: Optional[List[DownloadPart]] = None
        effective_threads = self.config.num_threads
        optimizer = None
        governor = None

        # ---- Smart / Manual connection mode ---------------------------
        if getattr(self.config, "connection_mode", "manual") == "smart":
            from core.optimizer import SmartOptimizer

            optimizer = SmartOptimizer(
                max_connections=getattr(self.config, "smart_max_connections", 8),
                adaptive=getattr(self.config, "smart_adaptive", True),
                on_status=smart_callback,
                log=self._print,
            )
            effective_threads, governor = optimizer.start(total_size, supports_range)
            if self._limiter is not None and self._limiter.enabled:
                optimizer.set_speed_limited(True)
        else:
            effective_threads = self.config.num_threads

        if loaded and loaded[0] == url and loaded[1] == total_size:
            _, _, saved_threads, old_parts = loaded
            if not _parts_within_directory(old_parts, directory):
                self._print(
                    "[yellow]Resume state rejected (invalid part paths). "
                    "Starting fresh.[/yellow]"
                )
                state_mgr.delete()
                loaded = None
                parts = None
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
                state_mgr.save(url, total_size, parts, effective_threads)
        else:
            parts = None

        if parts is None:
            parts = build_parts(
                total_size,
                effective_threads,
                file_path,
                self.config.min_part_size,
            )
            effective_threads = len(parts)
            if (
                getattr(self.config, "connection_mode", "manual") != "smart"
                and effective_threads < self.config.num_threads
            ):
                self._print(
                    f"[yellow]⚠ Reduced threads to {effective_threads} "
                    f"for file size {format_size(total_size)}[/yellow]"
                )
            state_mgr.save(url, total_size, parts, effective_threads)

        self._notify_status(status_callback, "DOWNLOADING")
        DownloadContext.begin(state_mgr, url, total_size, parts, effective_threads)

        already_bytes = sum(p.downloaded_size for p in parts)
        pending_parts = [p for p in parts if not p.is_complete]

        speed_tracker = SpeedTracker(
            window_size=self.config.speed_window_size,
            sample_interval=self.config.speed_sample_interval,
        )
        speed_tracker.seed(already_bytes)

        progress_lock = threading.Lock()

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
                    shared_progress = {"completed": already_bytes}

                    # Smart mode: feed the optimizer from the throttled progress
                    # path and (when adaptive) pace segment concurrency.
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

                    self._notify_progress(progress_callback, already_bytes, total_size)

                    failed_parts: List[DownloadPart] = []
                    pool = _get_shared_pool(max(len(pending_parts), self.config.num_threads))

                    def _run_part(part: DownloadPart) -> bool:
                        if governor is not None:
                            governor.acquire()
                        try:
                            return self.download_part(
                                transfer_url,
                                part,
                                progress,
                                task_id,
                                progress_lock,
                                speed_tracker,
                                total_size,
                                progress_callback,
                                shared_progress,
                                control,
                                optimizer=optimizer,
                            )
                        finally:
                            if governor is not None:
                                governor.release()

                    futures = {pool.submit(_run_part, p): p for p in pending_parts}
                    for future in as_completed(futures):
                        part = futures[future]
                        try:
                            if not future.result():
                                failed_parts.append(part)
                        except Exception as exc:
                            self._print(f"[red]Part {part.index} error: {exc}")
                            failed_parts.append(part)

                    completed = sum(p.downloaded_size for p in parts)
                    with progress_lock:
                        if progress is not None and task_id is not None:
                            progress.update(
                                task_id, completed=completed, total=total_size
                            )

                    self._notify_progress(progress_callback, completed, total_size)

                if self._ctl_cancelled(control):
                    state_mgr.save(url, total_size, parts, effective_threads)
                    self.last_error = "Cancelled"
                    self._print(
                        "[yellow]Download cancelled. State saved for resume.[/yellow]"
                    )
                    return False

                if failed_parts:
                    state_mgr.save(url, total_size, parts, effective_threads)
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
                    state_mgr.save(url, total_size, parts, effective_threads)
                    self.last_error = self.last_error or "Some parts incomplete — will resume on retry"
                    self._print("[bold red]Some parts incomplete. Re-run to resume.")
                    return False

            self._notify_status(status_callback, "MERGING")
            merge_expected = total_size if self.config.verify_size else 0
            ok, merge_err = merge_parts(
                parts, file_path, self.config.buffer_size, expected_size=merge_expected
            )
            if not ok:
                state_mgr.save(url, total_size, parts, effective_threads)
                self.last_error = merge_err
                self._print(f"[red]{merge_err}. Parts preserved for retry.")
                return False

            if verify_checksum and expected_hash:
                self._notify_status(status_callback, "VERIFYING")
                try:
                    algorithm = detect_hash_algorithm(expected_hash)
                except ValueError as exc:
                    self.last_error = str(exc)
                    self._print(f"[red]{exc}")
                    return False
                self._print(f"[cyan]Verifying {algorithm.upper()} checksum...")
                actual_hash = calculate_checksum(file_path, algorithm)
                if actual_hash.lower() != expected_hash.strip().lower():
                    self.last_error = "Checksum mismatch — downloaded file is corrupted"
                    self._print(
                        f"[bold red]Checksum mismatch! Expected {expected_hash}, "
                        f"got {actual_hash}"
                    )
                    file_path.unlink(missing_ok=True)
                    return False
                self._print("[green]✓ Checksum verified!")

            for part in parts:
                part.path.unlink(missing_ok=True)
            state_mgr.delete()
            self._print(f"[bold green]✓ Saved: {file_path}")
            return True

        finally:
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
