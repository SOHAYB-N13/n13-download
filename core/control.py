"""Per-task cooperative pause/cancel signalling.

The engine used a process-global :class:`core.context.DownloadContext`, which
made pause/cancel bleed across concurrent downloads.  This is the per-task
replacement: a set of ``threading.Event`` objects that the queue worker thread
controls and the download hot-loop checks.

Pause/cancel responsiveness
===========================
``wait_if_paused`` blocks the calling thread while paused using an event wait,
so a paused segment stops promptly at the next chunk boundary *without* a busy
loop or CPU spin: the thread sleeps until the next pause/resume/cancel
transition.

``ui.common.TaskControl`` re-exports this class so there is exactly one
implementation shared by the engine, the queue and the UI.
"""

from __future__ import annotations

import threading


class TaskCancelled(Exception):
    """Raised by runners when a task is cancelled."""


class TaskControl:
    """Cooperative pause/cancel signalling for one download task."""

    def __init__(self) -> None:
        self._pause = threading.Event()
        self._cancel = threading.Event()
        # Set on every pause/resume/cancel transition so waiters can sleep on it
        # instead of polling.  Pre-set so the first wait is non-blocking when
        # the control is not paused.
        self._changed = threading.Event()
        self._changed.set()

    def pause(self) -> None:
        self._pause.set()
        self._changed.set()

    def resume(self) -> None:
        self._pause.clear()
        self._changed.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()  # unblock any waiting pause
        self._changed.set()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def wait_if_paused(self, timeout: float = 0.25) -> None:
        """Block while paused; returns once resumed or cancelled.

        Efficient: the thread sleeps on an event that is signalled on every
        pause/resume/cancel transition, waking at most ``timeout`` seconds
        later as a safety net against rare lost-wakeup races.  This is a very
        low-frequency wake (4 Hz), not a busy loop, so no excessive CPU is used
        while paused.
        """
        while self._pause.is_set() and not self._cancel.is_set():
            self._changed.clear()
            # Re-check after clearing: pause/resume may have raced with clear().
            if not (self._pause.is_set() and not self._cancel.is_set()):
                break
            self._changed.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise TaskCancelled()
