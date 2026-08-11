"""Optional clipboard monitor (Phase 15).

Polls the clipboard for something that looks like a download URL and reports
it via a callback.  The feature is strictly opt-in (``clipboard_monitor``) and
never reads more than one clipboard snapshot per tick, so it cannot "steal"
clipboard content or interrupt the user.

On Windows the poll uses ``tkinter``'s clipboard (pure-stdlib); a failure to
read the clipboard (empty, or the app that owns it is blocking) is treated as
a transient "no new content" and never raises.
"""

from __future__ import annotations

import threading
import tkinter
from typing import Callable, Optional

_POLL_INTERVAL = 3.0


class ClipboardMonitor:
    def __init__(
        self,
        on_url: Callable[[str], None],
        logger: Optional[object] = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._on_url = on_url
        self._logger = logger
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_seen: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="n13-clipboard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    def _read_clipboard(self) -> Optional[str]:
        """Read the clipboard; returns ``None`` when unavailable/empty."""
        try:
            root = tkinter.Tk()
            root.withdraw()
            try:
                value = root.clipboard_get()
                return (value or "").strip() or None
            finally:
                try:
                    root.destroy()
                except tkinter.TclError:
                    pass
        except Exception:
            return None

    @staticmethod
    def _looks_like_url(value: str) -> Optional[str]:
        """Return the first http(s) URL found in *value*, else ``None``."""
        if not value:
            return None
        for token in value.replace("\r", "\n").split("\n"):
            token = token.strip()
            if token.lower().startswith(("http://", "https://")):
                return token
        return None

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self._tick()
            except Exception as exc:
                if self._logger is not None:
                    try:
                        self._logger.warning("Clipboard monitor error: %s", exc)
                    except Exception:
                        pass

    def _tick(self) -> None:
        value = self._read_clipboard()
        url = self._looks_like_url(value)
        if url and url != self._last_seen:
            self._last_seen = url
            try:
                self._on_url(url)
            except Exception:
                pass
        elif value != self._last_seen:
            # Remember non-URL clipboard content so copying a URL twice in a
            # row still triggers (first copy is a URL, second is a URL again).
            self._last_seen = value
