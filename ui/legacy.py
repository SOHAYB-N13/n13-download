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

import time
from pathlib import Path
from typing import Any, Callable, Optional

from config.settings import AppConfig
from core.control import TaskCancelled, TaskControl
from core.download import DownloadController
from core.session import SessionManager
from core.throttle import sync_limiter_from_config

from ui.common import DownloadRequest, ProgressCallback

# How long a handed-off probe result stays valid (seconds).  After this the
# analyzer performs its normal probe again.
_HANDOFF_TTL = 60.0


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

    def _handoff_valid(self, analysis: Any, url: str) -> bool:
        """Whether a handed-off probe result may be reused for *url*.

        Fast path is strictly validated — a result that is not OK, does not
        belong to this exact URL, is stale, or fails the SSRF check for the
        task URL is rejected so the normal probe runs instead.
        """
        try:
            if analysis is None or not getattr(analysis, "ok", False):
                return False
            if not getattr(analysis, "url", "") or not getattr(analysis, "probed_at", 0):
                return False
            if time.time() - float(analysis.probed_at) > _HANDOFF_TTL:
                return False
            from core.security import validate_download_url
            from core.utils import normalize_url

            if normalize_url(analysis.url) != normalize_url(url):
                return False
            ok, _ = validate_download_url(
                url, block_private=self._config.block_private_urls
            )
            return ok
        except Exception:
            return False

    def analyze(self, task_id: str, request: DownloadRequest, control: TaskControl) -> Any:
        """Probe the URL and return an :class:`core.analyzer.Analysis`.

        Reuses a task-scoped probe hand-off (from the UI's probe step) when it
        is still valid for this exact URL — eliminating the duplicate network
        probe — and falls back to the normal probe otherwise.
        """
        if control is not None and control.cancelled:
            raise TaskCancelled()
        self._prepare()
        handed = getattr(request, "probe_analysis", None)
        if handed is not None and self._handoff_valid(handed, request.url):
            if self._log:
                try:
                    self._log("Using handed-off probe result (no re-probe).")
                except Exception:
                    pass
            return handed
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
        smart_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Execute one download. Returns True on success."""
        if control is not None and control.cancelled:
            raise TaskCancelled()
        self._prepare()

        # Per-task connection override (set by download rules).  A copy of the
        # config lets a rule switch this one download to Smart/Manual without
        # touching the global settings.
        cfg = self._config
        if request.connection_mode:
            cfg = self._config.copy()
            cfg.connection_mode = request.connection_mode
            if request.connection_mode == "manual" and request.num_threads:
                cfg.num_threads = max(1, int(request.num_threads))

        controller = DownloadController(
            cfg,
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

        def _on_smart(text: str) -> None:
            if smart_callback is not None:
                try:
                    smart_callback(text)
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
                    smart_callback=_on_smart,
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
