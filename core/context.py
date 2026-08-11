"""Active download context for graceful shutdown."""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from core.parts import DownloadPart
from core.state import DownloadState


class DownloadContext:
    """Thread-safe holder for in-progress download state (SIGINT save).

    Hot-path optimisations
    ======================
    - ``_cancel_event`` (:class:`threading.Event`) provides lock-free
      cancellation checks in the download hot loop.
    - ``_pause_blocker`` is a separate Event so pause/resume does not
      race with cancellation signalling.
    - The ``_cancelled`` bool backing field is only used for state
      queries that already hold the lock; the hot path uses the Event.
    """

    _lock = threading.Lock()
    _cancel_event = threading.Event()
    # A fresh context is NOT paused.  ``threading.Event()`` starts *unset*, so
    # it must be explicitly set here — otherwise the very first single-thread
    # (non-Range) download in a process would block forever in
    # ``wait_if_paused()``.
    _pause_blocker = threading.Event()
    _pause_blocker.set()
    _state_mgr: Optional[DownloadState] = None
    _url: str = ""
    _total_size: int = 0
    _parts: List[DownloadPart] = []
    _num_threads: int = 1
    _cancelled: bool = False
    _paused: bool = False
    _on_interrupt: Optional[Callable[[], None]] = None

    @classmethod
    def begin(
        cls,
        state_mgr: DownloadState,
        url: str,
        total_size: int,
        parts: List[DownloadPart],
        num_threads: int,
    ) -> None:
        with cls._lock:
            pause_requested = cls._paused
            cls._state_mgr = state_mgr
            cls._url = url
            cls._total_size = total_size
            cls._parts = parts
            cls._num_threads = num_threads
            cls._cancelled = False
            cls._cancel_event.clear()
            cls._paused = pause_requested
            if pause_requested:
                cls._pause_blocker.clear()
            else:
                cls._pause_blocker.set()

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._state_mgr = None
            cls._url = ""
            cls._total_size = 0
            cls._parts = []
            cls._cancelled = False
            cls._cancel_event.clear()
            cls._paused = False
            cls._pause_blocker.set()

    @classmethod
    def save_now(cls) -> bool:
        with cls._lock:
            if cls._state_mgr and cls._url and cls._parts:
                cls._state_mgr.save(cls._url, cls._total_size, cls._parts, cls._num_threads)
                return True
        return False

    @classmethod
    def request_cancel(cls) -> None:
        with cls._lock:
            cls._cancelled = True
        # Set both events so paused threads wake up and see cancel.
        cls._cancel_event.set()
        cls._pause_blocker.set()

    @classmethod
    def pause(cls) -> None:
        with cls._lock:
            cls._paused = True
            cls._pause_blocker.clear()

    @classmethod
    def resume(cls) -> None:
        with cls._lock:
            cls._paused = False
            cls._pause_blocker.set()

    @classmethod
    def is_cancelled(cls) -> bool:
        """Lock-free cancellation check for hot-path use.
        
        Uses ``threading.Event.is_set()`` which is atomic and does not
        acquire any Python-level mutex in the hot path.
        """
        return cls._cancel_event.is_set()

    @classmethod
    def wait_if_paused(cls) -> bool:
        """Block the calling thread while paused; return False if cancelled.

        When not paused: returns ``True`` immediately via the event fast-path.
        When paused: blocks until resumed or cancelled.

        Cancellation is checked via ``_cancel_event.is_set()`` (lock-free)
        in the fast path, falling back to the lock only when paused state
        needs to be re-read after a timeout.
        """
        while True:
            if cls._cancel_event.is_set():
                return False
            if cls._pause_blocker.is_set():
                return True
            # Paused — block until _pause_blocker is set (resume or cancel).
            # _cancel_event has a small timeout so we can re-check both flags
            # without waiting indefinitely if both events are set.
            cls._pause_blocker.wait(timeout=0.25)

    @classmethod
    def set_interrupt_handler(cls, handler: Callable[[], None]) -> None:
        cls._on_interrupt = handler

    @classmethod
    def handle_interrupt(cls) -> None:
        cls.save_now()
        if cls._on_interrupt:
            cls._on_interrupt()
