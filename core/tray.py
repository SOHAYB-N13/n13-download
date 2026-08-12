"""Windows system tray integration.

Uses pywin32 (already a dependency of the project via ``browser-cookie3``), so
no new runtime dependency is required.  If pywin32 is unavailable the tray
simply does not start and the app behaves exactly as before.

The tray runs its own hidden message window on a daemon thread and provides:

* single / double left-click → Show N13
* right-click → context menu: Show N13 · Pause all · Resume all ·
  Open downloads folder · Settings · Exit
* a throttled tooltip (N13 · N active · speed)

"Exit" routes through the existing graceful shutdown (never bypasses it).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

try:
    import win32api
    import win32con
    import win32gui

    HAVE_WIN32 = True
except Exception:  # pragma: no cover - non-Windows / missing pywin32
    HAVE_WIN32 = False

# NOTIFYICONDATA constants (exposed on win32gui in this build).
_NIM_ADD = getattr(win32gui, "NIM_ADD", 0) if HAVE_WIN32 else 0
_NIM_MODIFY = getattr(win32gui, "NIM_MODIFY", 1) if HAVE_WIN32 else 1
_NIM_DELETE = getattr(win32gui, "NIM_DELETE", 2) if HAVE_WIN32 else 2
_NIF_MESSAGE = getattr(win32gui, "NIF_MESSAGE", 1) if HAVE_WIN32 else 1
_NIF_ICON = getattr(win32gui, "NIF_ICON", 2) if HAVE_WIN32 else 2
_NIF_TIP = getattr(win32gui, "NIF_TIP", 4) if HAVE_WIN32 else 4
_NIF_INFO = getattr(win32gui, "NIF_INFO", 16) if HAVE_WIN32 else 16

_TRAY_MSG = win32con.WM_USER + 20
_CLASS_NAME = "N13TrayWindow"
# Throttle tooltip updates (avoid excessive CPU / flicker).
_MIN_TOOLTIP_INTERVAL = 2.0


class SystemTray:
    """A minimal, optional Windows tray icon + menu."""

    def __init__(
        self,
        on_show: Optional[Callable[[], None]] = None,
        on_pause_all: Optional[Callable[[], None]] = None,
        on_resume_all: Optional[Callable[[], None]] = None,
        on_open_folder: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
    ) -> None:
        self._cb = {
            "show": on_show,
            "pause_all": on_pause_all,
            "resume_all": on_resume_all,
            "open_folder": on_open_folder,
            "settings": on_settings,
            "exit": on_exit,
        }
        self._hwnd = None
        self._icon_id = 0
        self._thread: Optional[threading.Thread] = None
        self._tooltip = "N13"
        self._last_tip_at = 0.0
        self._cmd_map: dict = {}
        self._labels: dict[str, str] = {}
        self._menu_items = []

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> bool:
        """Create the tray icon + message loop on a daemon thread."""
        if not HAVE_WIN32:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True, name="n13-tray")
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    def set_labels(self, labels: dict[str, str]) -> None:
        """Update menu labels from the frontend translation dictionary."""
        self._labels = dict(labels)
        self._menu_items = [
            (self._labels.get("tray.show", "Show N13"), "show"),
            (self._labels.get("tray.pause_all", "Pause all"), "pause_all"),
            (self._labels.get("tray.resume_all", "Resume all"), "resume_all"),
            (self._labels.get("tray.open_folder", "Open downloads folder"), "open_folder"),
            (self._labels.get("tray.settings", "Settings"), "settings"),
            None,
            (self._labels.get("tray.exit", "Exit"), "exit"),
        ]

    def set_tooltip(self, text: str) -> None:
        """Throttled tooltip update — never more than once per interval."""
        now = time.monotonic()
        if now - self._last_tip_at < _MIN_TOOLTIP_INTERVAL:
            return
        self._last_tip_at = now
        self._tooltip = text
        if self._hwnd:
            try:
                nid = (self._hwnd, self._icon_id, _NIF_TIP, 0, 0, text)
                win32gui.Shell_NotifyIcon(_NIM_MODIFY, nid)
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        """Show a Windows balloon notification via the tray icon.

        Field layout (this pywin32 build): ``(hwnd, id, flags, cbmsg, hIcon,
        tip, szInfo, uTimeout, szInfoTitle, dwInfoFlags)`` — ``szInfo`` at
        index 6, ``szInfoTitle`` at index 8.
        """
        if not self._hwnd:
            return
        try:
            nid = (
                self._hwnd, self._icon_id, _NIF_INFO,
                0, 0, "", (message or "")[:255], 0, (title or "")[:64], 0,
            )
            win32gui.Shell_NotifyIcon(_NIM_MODIFY, nid)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        hinst = win32api.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = _CLASS_NAME
        wc.lpfnWndProc = self._wnd_proc
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass  # already registered
        self._hwnd = win32gui.CreateWindow(
            _CLASS_NAME, "N13Tray", win32con.WS_OVERLAPPED, 0, 0, 0, 0, 0, 0, hinst, None
        )
        icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        nid = (
            self._hwnd, self._icon_id,
            _NIF_MESSAGE | _NIF_ICON | _NIF_TIP,
            _TRAY_MSG, icon, self._tooltip,
        )
        win32gui.Shell_NotifyIcon(_NIM_ADD, nid)
        win32gui.PumpMessages()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == _TRAY_MSG:
            if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                self._call("show")
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
        elif msg == win32con.WM_COMMAND:
            self._handle_menu(wparam & 0xFFFF)
        elif msg == win32con.WM_DESTROY:
            try:
                win32gui.Shell_NotifyIcon(_NIM_DELETE, (hwnd, self._icon_id))
            except Exception:
                pass
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _call(self, name: str) -> None:
        fn = self._cb.get(name)
        if fn:
            try:
                fn()
            except Exception:
                pass

    def _show_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        self._cmd_map = {}
        for idx, item in enumerate(self._menu_items):
            if item is None:
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
                continue
            cmd_id = idx + 1
            self._cmd_map[cmd_id] = item[1]
            win32gui.AppendMenu(menu, win32con.MF_STRING, cmd_id, item[0])
        try:
            pos = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self._hwnd)
            win32gui.TrackPopupMenu(
                menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                pos[0], pos[1], 0, self._hwnd, None,
            )
        finally:
            try:
                win32gui.DestroyMenu(menu)
            except Exception:
                pass

    def _handle_menu(self, cmd_id: int) -> None:
        name = self._cmd_map.get(cmd_id)
        if name:
            self._call(name)
