"""Runner that bridges the queue's task lifecycle to the download engine.

The runner implements the :class:`ui.common.DownloadRunner` protocol:
``analyze`` performs the pre-download URL analysis (ANALYZING state) and
``download`` drives the engine with per-task pause/cancel control, so
concurrent tasks never interfere with each other's state.

This replaces the old monitor-thread approach that pushed ``TaskControl``
signals into the process-global :class:`core.context.DownloadContext`; per-task
control is now passed straight into :class:`core.download.DownloadController`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from config.settings import AppConfig
from core.control import TaskCancelled, TaskControl
from core.download import DownloadController
from core.session import SessionManager
from core.throttle import sync_limiter_from_config

from ui.common import DownloadRequest, ProgressCallback


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

    def _prepare(self) -> None:
        """Apply current settings to the session and global limiter."""
        sync_limiter_from_config(self._config)
        try:
            self._session.configure(self._config)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Analyze phase (ANALYZING)
    # ------------------------------------------------------------------

    def analyze(self, task_id: str, request: DownloadRequest, control: TaskControl) -> Any:
        """Probe the URL and return an :class:`core.analyzer.Analysis`."""
        if control is not None and control.cancelled:
            raise TaskCancelled()
        self._prepare()
        from core.analyzer import analyze_url

        return analyze_url(request.url, self._config, self._session)

    # ------------------------------------------------------------------
    # Download phase (STARTING → …)
    # ------------------------------------------------------------------

    def download(
        self,
        task_id: str,
        request: DownloadRequest,
        analysis: Any,
        progress: ProgressCallback,
        control: TaskControl,
        status_callback: Optional[Callable[[str], None]] = None,
        path_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Execute one download. Returns True on success."""
        if control is not None and control.cancelled:
            raise TaskCancelled()
        self._prepare()

        controller = DownloadController(
            self._config,
            self._session,
            self._log,
            show_progress=False,
        )
        self.last_error = ""

        def _on_status(name: str) -> None:
            if status_callback is not None:
                try:
                    status_callback(name)
                except Exception:
                    pass

        def _on_path(path: str) -> None:
            if path_callback is not None:
                try:
                    path_callback(path)
                except Exception:
                    pass

        try:
            ok = bool(
                controller.download_file(
                    request.url,
                    Path(request.directory),
                    verify_checksum=bool(request.checksum),
                    expected_hash=request.checksum or None,
                    progress_callback=progress,
                    control=control,
                    pre_analysis=analysis,
                    status_callback=_on_status,
                    path_callback=_on_path,
                )
            )
            if not ok and controller.last_error:
                self.last_error = controller.last_error
            return ok
        except TaskCancelled:
            raise
        except Exception as exc:
            from core.errors import friendly_error_message

            self.last_error = friendly_error_message(exc)
            self._log(f"[red]Download error: {exc}[/red]")
            return False

    # ------------------------------------------------------------------
    # Backward-compatible wrapper
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: str,
        request: DownloadRequest,
        progress: ProgressCallback,
        control: TaskControl,
    ) -> bool:
        """Analyse then download (legacy single-call entry point)."""
        analysis = self.analyze(task_id, request, control)
        return self.download(task_id, request, analysis, progress, control)
