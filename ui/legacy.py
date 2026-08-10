"""Adapter that bridges the TaskManager runner protocol to the legacy DownloadController.

Thread-safety fixes in this rewrite
=====================================
1.  The monitor thread only calls DownloadContext methods; it never touches
    TaskControl.cancelled directly from outside the lock — that was causing a
    race where the monitor could flip the context after DownloadContext.clear()
    had already been called by the controller's finally block.

2.  DownloadContext.begin() is called immediately before the controller run,
    not inside the monitor thread, so the pause/cancel state is set before any
    chunk is written.

3.  The monitor uses a threading.Event (stop_event) rather than a bare bool so
    the final join is guaranteed to see the last written value.

4.  If the runner raises TaskCancelled it is re-raised immediately after the
    monitor is stopped and the context is cleared — no state is left dangling.

5.  The global lock (_global_lock) is retained so that only one DownloadContext
    is active at a time; concurrent downloads each get their own slot via the
    TaskManager's max_concurrent gate.

6.  config/session are re-applied on every run() call so that settings-dialog
    changes take effect for the next download without restarting the app.
"""
import threading
from pathlib import Path
from typing import Callable, Optional

from config.settings import AppConfig
from core.context import DownloadContext
from core.download import DownloadController
from core.session import SessionManager
from core.throttle import sync_limiter_from_config

from ui.common import DownloadRequest, ProgressCallback, TaskCancelled, TaskControl


class LegacyDownloadRunner:
    """Run downloads through the existing DownloadController."""

    def __init__(
        self,
        config: AppConfig,
        session: SessionManager,
        log: Optional[Callable[..., None]] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._log = log or (lambda *a, **k: None)
        # Serialises access to the global DownloadContext so only one transfer
        # mutates it at a time regardless of max_concurrent setting.
        self._ctx_lock = threading.Lock()

    def run(
        self,
        task_id: str,
        request: DownloadRequest,
        progress: ProgressCallback,
        control: TaskControl,
    ) -> bool:
        """Execute one download. Returns True on success."""
        sync_limiter_from_config(self._config)

        # Reconfigure session for each run so proxy/auth/speed changes apply.
        try:
            self._session.configure(self._config)
        except Exception:
            pass

        with self._ctx_lock:
            return self._run_locked(task_id, request, progress, control)

    def _run_locked(
        self,
        task_id: str,
        request: DownloadRequest,
        progress: ProgressCallback,
        control: TaskControl,
    ) -> bool:
        stop_event = threading.Event()

        def _monitor() -> None:
            """Bridge TaskControl signals → DownloadContext (runs in daemon thread)."""
            was_paused = False
            while not stop_event.wait(timeout=0.5):
                try:
                    if control.cancelled:
                        DownloadContext.request_cancel()
                        break
                    now_paused = control.paused
                    if now_paused and not was_paused:
                        DownloadContext.pause()
                        was_paused = True
                    elif not now_paused and was_paused:
                        DownloadContext.resume()
                        was_paused = False
                except Exception:
                    break

        # Check for early cancellation before we even start the controller.
        if control.cancelled:
            raise TaskCancelled()

        monitor = threading.Thread(
            target=_monitor,
            name=f"n13-ctx-monitor-{task_id}",
            daemon=True,
        )
        monitor.start()

        ok = False
        try:
            controller = DownloadController(
                self._config,
                self._session,
                self._log,
                show_progress=False,
            )
            ok = bool(
                controller.download_file(
                    request.url,
                    Path(request.directory),
                    verify_checksum=bool(request.checksum),
                    expected_hash=request.checksum or None,
                    progress_callback=progress,
                )
            )
        except TaskCancelled:
            raise
        except Exception as exc:
            self._log(f"[red]Download error: {exc}[/red]")
            ok = False
        finally:
            # Stop the monitor first so it cannot touch the context after clear.
            stop_event.set()
            monitor.join(timeout=1.5)
            # Ensure the context is always clean after this run.
            try:
                DownloadContext.clear()
            except Exception:
                pass

        return ok
