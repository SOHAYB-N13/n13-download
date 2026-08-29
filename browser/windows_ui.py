"""Windows-native UI automation helpers.

Primary engine: ``uiautomation`` (COM / UI Automation).  ``pywinauto`` is used
only as a fallback for desktop window enumeration (e.g. detecting Chrome's
folder-picker dialog when the UIA search path fails).

All controls are exposed through the tiny :class:`ControlRef` wrapper so call
sites never depend on a specific automation library.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

log = logging.getLogger("n13")

TAG = "[N13 Extension]"

POLL_INTERVAL = 0.1  # seconds — short intelligent polling, never blind sleeps
DEFAULT_TIMEOUT = 20.0

# Win32 window classes
CHROME_MAIN_CLASS = "Chrome_WidgetWin_1"


class UIAError(Exception):
    """UI automation failure."""


def ensure_com() -> None:
    """Initialize COM for the calling thread (idempotent, best-effort)."""
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass


def _uia():
    """Lazy import of the primary automation engine."""
    if _uia._mod is None:
        import uiautomation as mod

        _uia._mod = mod
    return _uia._mod


_uia._mod = None  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# ControlRef — thin wrapper around a uiautomation Control                     #
# --------------------------------------------------------------------------- #

class ControlRef:
    """Normalized view of one UI Automation element."""

    def __init__(self, control) -> None:
        self._c = control

    # ── properties ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        try:
            return self._c.Name or ""
        except Exception:
            return ""

    @property
    def type_name(self) -> str:
        try:
            return self._c.ControlTypeName or ""
        except Exception:
            return ""

    @property
    def class_name(self) -> str:
        try:
            return self._c.ClassName or ""
        except Exception:
            return ""

    @property
    def automation_id(self) -> str:
        try:
            return self._c.AutomationId or ""
        except Exception:
            return ""

    @property
    def is_enabled(self) -> bool:
        try:
            return bool(self._c.IsEnabled)
        except Exception:
            return False

    @property
    def is_offscreen(self) -> bool:
        try:
            return bool(self._c.IsOffscreen)
        except Exception:
            return False

    @property
    def visible(self) -> bool:
        return self.is_enabled and not self.is_offscreen

    def is_type(self, *type_names: str) -> bool:
        return self.type_name in type_names

    # ── actions ───────────────────────────────────────────────────

    def focus(self) -> bool:
        try:
            self._c.SetFocus()
            return True
        except Exception as exc:
            log.info("%s SetFocus failed: %s", TAG, exc)
            return False

    def invoke(self) -> bool:
        try:
            pat = self._c.GetInvokePattern()
            pat.Invoke()
            return True
        except Exception as exc:
            log.info("%s InvokePattern unavailable: %s", TAG, exc)
            return False

    def toggle(self) -> bool:
        """Toggle a switch/checkbox; returns True on success."""
        try:
            pat = self._c.GetTogglePattern()
            pat.Toggle()
            return True
        except Exception as exc:
            log.info("%s TogglePattern unavailable: %s", TAG, exc)
            return False

    def toggled_on(self) -> Optional[bool]:
        """Current toggle state: True=on, False=off, None=not readable."""
        try:
            pat = self._c.GetTogglePattern()
            state = int(pat.ToggleState)
            return state in (1, 2)  # ToggleState_On / Indeterminate count as on
        except Exception:
            return None

    def click(self) -> bool:
        try:
            self._c.Click()
            return True
        except Exception as exc:
            log.info("%s click failed: %s", TAG, exc)
            return False

    def identity_key(self) -> tuple:
        """Stable (name, type, automation id, class) tuple for element matching."""
        return (self.name.lower(), self.type_name, self.automation_id.lower(), self.class_name)

    def focused_now(self) -> bool:
        """True when this element currently owns keyboard focus."""
        try:
            focused = _uia().GetFocusedControl()
            if focused is None:
                return False
            return ControlRef(focused).identity_key() == self.identity_key()
        except Exception:
            return False

    def activate_keyboard(self, keys: str = "{Enter}") -> bool:
        """Keyboard activation: focus, confirm focus, then press *keys*.

        Never blindly pressed — the element must actually be focused first.
        """
        if not self.focus():
            return False
        if not self.focused_now():
            # Some hosts need a moment to deliver focus.
            for _ in range(10):
                time.sleep(0.05)
                if self.focused_now():
                    break
            else:
                return False
        key_combo(keys)
        return True

    def value(self) -> str:
        try:
            return self._c.GetValuePattern().Value or ""
        except Exception:
            return ""

    def set_value(self, text: str) -> bool:
        try:
            self._c.GetValuePattern().SetValue(text)
            return True
        except Exception as exc:
            log.info("%s ValuePattern.SetValue failed: %s", TAG, exc)
            return False

    def hwnd(self) -> Optional[int]:
        try:
            return self._c.NativeWindowHandle
        except Exception:
            return None

    # ── tree ──────────────────────────────────────────────────────

    def parent(self) -> Optional["ControlRef"]:
        try:
            p = self._c.GetParentControl()
            return ControlRef(p) if p else None
        except Exception:
            return None

    def walk_up(self) -> list["ControlRef"]:
        """Ancestor chain, outermost last."""
        chain: list[ControlRef] = []
        seen = 0
        node = self.parent()
        while node is not None and seen < 64:
            chain.append(node)
            node = node.parent()
            seen += 1
        return chain

    def window(self) -> Optional["ControlRef"]:
        """The top-level window element containing this control."""
        for node in self.walk_up():
            if node.is_type("WindowControl", "PaneControl"):
                return node
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ControlRef {self.type_name} name={self.name!r} id={self.automation_id!r}>"


# --------------------------------------------------------------------------- #
# Searching                                                                   #
# --------------------------------------------------------------------------- #

def desktop() -> ControlRef:
    ensure_com()
    return ControlRef(_uia().GetRootControl())


def _search(root: ControlRef, name: Optional[str] = None,
            type_names: Optional[tuple[str, ...]] = None,
            automation_id: Optional[str] = None,
            class_name: Optional[str] = None,
            depth: int = 0xFFFFFFFF) -> Optional[ControlRef]:
    """Single best-match search below *root* (case-insensitive substring).

    Never raises: UIA COM errors (e.g. element destroyed mid-walk) yield None.
    """
    try:
        controls = root._c.GetChildren()
    except Exception:
        return None
    stack = list(controls)
    seen = 0
    while stack and seen < 5000:
        seen += 1
        node = stack.pop(0)
        try:
            ref = ControlRef(node)
        except Exception:
            continue
        try:
            if type_names and not ref.is_type(*type_names):
                pass
            elif class_name:
                if ref.class_name.lower() == class_name.lower():
                    return ref
            elif automation_id:
                if ref.automation_id.lower() == automation_id.lower():
                    return ref
            elif name:
                if name.lower() in ref.name.lower():
                    return ref
            else:
                return ref
            children = node.GetChildren()
        except Exception:
            children = []
        stack.extend(children)
    return None


def search_control(root: Optional[ControlRef] = None,
                   name: Optional[str] = None,
                   type_names: Optional[tuple[str, ...]] = None,
                   automation_id: Optional[str] = None,
                   class_name: Optional[str] = None,
                   timeout: float = DEFAULT_TIMEOUT) -> Optional[ControlRef]:
    """Poll *root*'s subtree until a matching control appears.

    Returns the first match or None.  Used for every stage transition — the
    automation continues the instant the expected element appears.
    """
    root = root or desktop()
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = _search(root, name=name, type_names=type_names,
                        automation_id=automation_id, class_name=class_name)
        if found is not None:
            return found
        time.sleep(POLL_INTERVAL)
    return None


def focused_control() -> Optional[ControlRef]:
    ensure_com()
    try:
        fc = _uia().GetFocusedControl()
        return ControlRef(fc) if fc else None
    except Exception:
        return None


def window_containing_focus() -> Optional[ControlRef]:
    """Top-level dialog/window owning the focused element (folder-picker fallback)."""
    fc = focused_control()
    if fc is None:
        return None
    return fc.window()


def focus_window(hwnd: Optional[int]) -> bool:
    """Bring a window to the foreground (Win32, DPI-safe, no coordinates).

    Uses the AttachThreadInput trick plus an Alt-key nudge to defeat the
    Windows foreground lock (a background process cannot normally steal
    focus, which is exactly our situation when automating Chrome).
    """
    if not hwnd:
        return False
    import win32api
    import win32con
    import win32gui
    import win32process

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg = win32gui.GetForegroundWindow()
        try:
            fg_thread = win32process.GetWindowThreadProcessId(fg)[0]
        except Exception:
            fg_thread = 0
        my_thread = win32api.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != my_thread:
            try:
                win32process.AttachThreadInput(my_thread, fg_thread, True)
                attached = True
            except Exception:
                pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(my_thread, fg_thread, False)
                except Exception:
                    pass
        # Alt-key nudge: releases the foreground lock for stubborn cases.
        if win32gui.GetForegroundWindow() != hwnd:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
        # Fallback A: UIA SetFocus on the window element.
        if win32gui.GetForegroundWindow() != hwnd:
            try:
                ctrl = _uia().ControlFromHandle(hwnd)
                ctrl.SetFocus()
            except Exception:
                pass
        # Fallback B: SwitchToThisWindow (activates without the full lock).
        if win32gui.GetForegroundWindow() != hwnd:
            try:
                import ctypes

                ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass
        win32gui.BringWindowToTop(hwnd)
        return True
    except Exception as exc:
        log.info("%s focus_window(%s) failed: %s", TAG, hwnd, exc)
        return False


def send_keys(text: str, with_modifiers: str = "") -> None:
    """Type text into the focused control.

    ``with_modifiers`` may be e.g. ``"control"``, ``"shift"`` — applied as
    ``<ctrl>+<shift>`` style chord around the text (uiautomation syntax).
    """
    ensure_com()
    uia = _uia()
    # Literal braces must be escaped for uiautomation's SendKeys.
    safe = text.replace("{", "{{").replace("}", "}}")
    try:
        if with_modifiers:
            uia.SendKeys(f"<{with_modifiers}>{safe}</{with_modifiers}>")
        else:
            uia.SendKeys(safe)
    except Exception as exc:
        raise UIAError(f"SendKeys failed: {exc}") from exc


def key_combo(keys: str) -> None:
    """Send a chord like '{Ctrl}l' or '{Enter}' to the focused control."""
    ensure_com()
    uia = _uia()
    try:
        uia.SendKeys(keys)
    except Exception as exc:
        raise UIAError(f"key_combo({keys!r}) failed: {exc}") from exc


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard write (used as a last-resort paste fallback)."""
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def poll_until(predicate: Callable[[], Optional[object]],
               timeout: float = DEFAULT_TIMEOUT,
               interval: float = POLL_INTERVAL) -> Optional[object]:
    """Run *predicate* every *interval* seconds until it returns a truthy value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None


def find_window_handles(class_name: str) -> list[int]:
    """All top-level window handles with *class_name* (visible or not)."""
    import win32gui

    out: list[int] = []

    def cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == class_name:
            out.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return out


def find_window_by_title(substring: str, class_name: Optional[str] = None) -> Optional[int]:
    """First visible top-level window whose title contains *substring*."""
    import win32gui

    result: list[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if class_name and win32gui.GetClassName(hwnd) != class_name:
            return True
        if substring.lower() in win32gui.GetWindowText(hwnd).lower():
            result.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def is_foreground(hwnd: Optional[int]) -> bool:
    import win32gui

    try:
        return bool(hwnd) and win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def window_text(hwnd: int) -> str:
    import win32gui

    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def process_of(hwnd: int) -> Optional[int]:
    import win32process

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def process_exe(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name().lower()
    except Exception:
        try:
            import win32process
            import win32api

            h = win32api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            try:
                return win32process.GetModuleFileNameEx(h, 0).lower()
            finally:
                win32api.CloseHandle(h)
        except Exception:
            return ""