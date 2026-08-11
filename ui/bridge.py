"""WebView application bridge — launches the native window."""

from __future__ import annotations

import os
import webview
from ui.api import Api

_window = None


def launch_app(config, session) -> None:
    """Create and start the WebView GUI. Called from d.py --gui."""
    api = Api(config, session)

    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
    index_path = os.path.join(frontend_dir, "index.html")

    global _window
    _window = webview.create_window(
        title="N13 Download Manager",
        url=index_path,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1120, 680),
        resizable=True,
        frameless=True,
        easy_drag=False,
        text_select=True,
        confirm_close=False,
    )

    api.set_window(_window)

    def _on_startup() -> None:
        if getattr(config, "start_minimized", False):
            try:
                _window.minimize()
            except Exception:
                pass

    webview.start(_on_startup, debug=False, gui="edgechromium")
