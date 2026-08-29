"""Google Chrome automation for the N13 extension installer.

Each function implements one stage of the "Load unpacked" workflow:

    Stage 2  find/launch Chrome, bring the real window to the foreground
    Stage 3  open chrome://extensions/  (Ctrl+L → URL → Enter, polled)
    Stage 4  enable Developer mode (state-aware, verified via "Load unpacked")
    Stage 5  locate + activate the real "Load unpacked" button (UIA Invoke)
    Stage 6  detect the Windows folder-picker dialog
    Stage 7  enter the N13 extension path into the picker (verified)
    Stage 8  activate the real "Select Folder" button
    Stage 9  verify the N13 extension is present and enabled in Chrome

No hardcoded install paths, no blind TAB sequences, no fixed coordinates.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from browser.windows_ui import (
    ControlRef,
    desktop,
    focus_window,
    is_foreground,
    key_combo,
    poll_until,
    process_exe,
    process_of,
    search_control,
    send_keys,
    window_text,
)

log = logging.getLogger("n13")

TAG = "[N13 Extension]"

CHROME_MAIN_CLASS = "Chrome_WidgetWin_1"
CHROME_RENDER_CLASS = "Chrome_RenderWidgetHostHWND"
EXTENSIONS_PAGE = "chrome://extensions/"
PICKER_TITLE_MARKERS = ("extension directory", "select folder", "browse for folder")


class ChromeAutomationError(Exception):
    """A Chrome automation stage failed (message is user-presentable)."""


def _log(emit: Optional[Callable[[str], None]], message: str) -> None:
    line = f"{TAG} {message}"
    if emit is not None:
        try:
            emit(line)
        except Exception:
            pass
    log.info("%s %s", TAG, message)


# --------------------------------------------------------------------------- #
# Stage 2 — Chrome detection / launch                                         #
# --------------------------------------------------------------------------- #

def find_chrome_exe() -> Path:
    """Locate chrome.exe without assuming an installation directory."""
    import winreg

    # 1) Windows App Paths registry (user + machine, 64/32-bit views).
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value and Path(value).is_file():
                        return Path(value)
            except OSError:
                continue

    # 2) Well-known per-user / program-files locations (env-derived only).
    env_candidates = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            env_candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in env_candidates:
        if candidate.is_file():
            return candidate

    # 3) PATH lookup.
    found = shutil.which("chrome")
    if found:
        return Path(found)

    raise ChromeAutomationError("Google Chrome could not be found.")


def chrome_main_windows() -> list[int]:
    """Visible Chrome main windows (class Chrome_WidgetWin_1) with a title."""
    from browser.windows_ui import find_window_handles

    wins: list[int] = []
    for hwnd in find_window_handles(CHROME_MAIN_CLASS):
        if not window_text(hwnd).strip():
            continue
        pid = process_of(hwnd)
        if pid and process_exe(pid) == "chrome.exe":
            wins.append(hwnd)
    return wins


def bring_chrome_to_front() -> int:
    """Bring the most likely user-facing Chrome window to the foreground."""
    wins = chrome_main_windows()
    if not wins:
        raise ChromeAutomationError("Google Chrome could not be found.")
    # Prefer the window with the longest title (a real page, not a shell).
    wins.sort(key=lambda h: len(window_text(h)), reverse=True)
    hwnd = wins[0]
    focus_window(hwnd)
    return hwnd


def find_extensions_window() -> Optional[int]:
    """The Chrome window currently showing the Extensions page, if any."""
    for hwnd in chrome_main_windows():
        if "extensions" in window_text(hwnd).lower():
            return hwnd
    return None


def _focus_verified(hwnd: int, attempts: int = 10) -> bool:
    """Bring *hwnd* to the foreground and confirm it actually is."""
    for _ in range(attempts):
        if is_foreground(hwnd):
            return True
        focus_window(hwnd)
        time.sleep(0.25)
    return is_foreground(hwnd)


def ensure_chrome_running(emit: Optional[Callable[[str], None]] = None) -> int:
    """Stage 2: attach to a running Chrome or launch it. Returns main HWND."""
    _log(emit, "Stage 2: opening Chrome...")
    existing = chrome_main_windows()
    if existing:
# Reuse the Extensions window if one is already open (stable target).
        hwnd = find_extensions_window() or existing[0]
        _focus_verified(hwnd)
        _log(emit, f"Chrome already running; reusing window (hwnd={hwnd}).")
        return hwnd

    chrome = find_chrome_exe()
    try:
        subprocess.Popen([str(chrome)], close_fds=True)
    except OSError as exc:
        raise ChromeAutomationError("Google Chrome could not be found.") from exc

    def _first_window() -> Optional[int]:
        wins = chrome_main_windows()
        return wins[0] if wins else None

    hwnd = poll_until(_first_window, timeout=25.0)
    if hwnd is None:
        raise ChromeAutomationError("Google Chrome could not be found.")
    _focus_verified(hwnd)
    _log(emit, "Chrome detected.")
    return hwnd


# --------------------------------------------------------------------------- #
# Stage 3 — chrome://extensions/                                              #
# --------------------------------------------------------------------------- #

def chrome_content_roots(hwnd: int) -> list[ControlRef]:
    """All render-widget host roots (one per tab) of the Chrome window."""
    import win32gui

    from browser.windows_ui import _uia

    children: list[int] = []

    def cb(child, _):
        children.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, cb, None)
    render_hosts = [c for c in children
                    if win32gui.GetClassName(c) == CHROME_RENDER_CLASS]
    if not render_hosts:
        raise ChromeAutomationError("Could not inspect the Chrome page content.")
    return [ControlRef(_uia().ControlFromHandle(h)) for h in render_hosts]


def chrome_window_ref(hwnd: int) -> ControlRef:
    """Primary UI Automation root for the web content of the Chrome window.

    Chrome exposes page content through child render-widget host windows
    (``Chrome_RenderWidgetHostHWND``), not through the main frame.  The root
    showing the Extensions page is preferred; Chrome builds accessibility
    trees lazily, so the probe needs a real (multi-second) timeout.
    """
    roots = chrome_content_roots(hwnd)
    for root in roots:
        if search_control(root, automation_id="devMode", timeout=2.0) is not None:
            return root
        if search_control(root, automation_id="loadUnpacked", timeout=0.5) is not None:
            return root
    return roots[0]


def extensions_page_ready(hwnd: int) -> bool:
    """True when the visible Extensions page has actually loaded."""
    title = window_text(hwnd)
    if "extensions" in title.lower():
        return True
    try:
        roots = chrome_content_roots(hwnd)
    except ChromeAutomationError:
        return False
    return _search_all_roots(roots, name="Developer mode", timeout=1.5) is not None


def open_extensions_page(hwnd: int, emit: Optional[Callable[[str], None]] = None) -> None:
    """Stage 3: navigate the focused Chrome window to chrome://extensions/."""
    _log(emit, "Stage 3: opening chrome://extensions/...")
    if extensions_page_ready(hwnd):
        _log(emit, "Extensions page ready.")
        return
    _focus_verified(hwnd)
    time.sleep(0.2)
    key_combo("{Ctrl}l")
    time.sleep(0.2)
    send_keys(EXTENSIONS_PAGE)
    time.sleep(0.2)
    key_combo("{Enter}")

    if poll_until(lambda: extensions_page_ready(hwnd), timeout=20.0) is None:
        raise ChromeAutomationError("Could not open Chrome Extensions page.")
    # The page may load while the window is not foreground; re-focus so the
    # accessibility tree (needed by the following stages) actually builds.
    if not is_foreground(hwnd):
        _focus_verified(hwnd)
    if poll_until(lambda: _search_all_roots(chrome_content_roots(hwnd), name="Developer mode", timeout=1.0) is not None, timeout=15.0) is None:
        raise ChromeAutomationError("Could not open Chrome Extensions page.")
    _log(emit, "Extensions page ready.")


# --------------------------------------------------------------------------- #
# Stage 4 — Developer mode                                                    #
# --------------------------------------------------------------------------- #

def _search_all_roots(roots: list[ControlRef], *args, **kwargs) -> Optional[ControlRef]:
    """Search every tab root; returns the first match across the window."""
    for root in roots:
        found = search_control(root, *args, **kwargs)
        if found is not None:
            return found
    return None


def find_developer_mode_switch(roots: list[ControlRef], timeout: float = 8.0) -> Optional[ControlRef]:
    """The real "Developer mode" control on the Extensions page."""
    found = _search_all_roots(
        roots, automation_id="devMode",
        type_names=("SwitchControl", "ToggleButtonControl", "ButtonControl", "CheckBoxControl", "CustomControl"),
        timeout=timeout,
    )
    if found is None:
        found = _search_all_roots(
            roots, name="Developer mode",
            type_names=("SwitchControl", "ToggleButtonControl", "ButtonControl", "CheckBoxControl", "CustomControl"),
            timeout=timeout,
        )
    return found


def load_unpacked_button(roots: list[ControlRef], timeout: float = 8.0) -> Optional[ControlRef]:
    """The real "Load unpacked" button (only exposed in Developer mode)."""
    found = _search_all_roots(
        roots, automation_id="loadUnpacked",
        type_names=("ButtonControl", "CustomControl", "HyperlinkControl"),
        timeout=timeout,
    )
    if found is None:
        found = _search_all_roots(
            roots, name="Load unpacked",
            type_names=("ButtonControl", "CustomControl", "HyperlinkControl"),
            timeout=timeout,
        )
    return found


def _activate_verified(control: ControlRef,
                       verify: Callable[[], Optional[object]],
                       failure: str,
                       timeout: float = 10.0,
                       emit: Optional[Callable[[str], None]] = None) -> None:
    """DETECT → VERIFY → ACTIVATE → VERIFY RESULT.

    Activation order (never a blind mouse click on web content):
    1. native TogglePattern / InvokePattern
    2. keyboard activation of the confirmed focused control (Enter/Space)
    """
    control.focus()
    label = control.name or repr(control.type_name)
    if control.toggle():
        _log(emit, f"Toggled {label!r}.")
        if poll_until(verify, timeout=timeout) is not None:
            return
    if control.invoke():
        _log(emit, f"Invoked {label!r}.")
        if poll_until(verify, timeout=timeout) is not None:
            return
    if control.activate_keyboard("{Enter}"):
        _log(emit, f"Activated {label!r} via Enter.")
        if poll_until(verify, timeout=timeout) is not None:
            return
    if control.activate_keyboard("{Space}"):
        _log(emit, f"Activated {label!r} via Space.")
        if poll_until(verify, timeout=timeout) is not None:
            return
    raise ChromeAutomationError(failure)


def _page_roots_with_focus(hwnd: int, predicate: Callable[[list[ControlRef]], Optional[ControlRef]],
                           emit: Optional[Callable[[str], None]] = None,
                           attempts: int = 3) -> Optional[ControlRef]:
    """Search the window's tab roots, re-focusing between attempts.

    Chrome exposes a page's accessibility tree only while its window is in
    the foreground; the window may also be busy building the tree after a
    navigation, so a failed search triggers focus + retry instead of failing.
    """
    for attempt in range(attempts):
        try:
            roots = chrome_content_roots(hwnd)
        except ChromeAutomationError:
            roots = []
        found = predicate(roots) if roots else None
        if found is not None:
            return found
        if attempt < attempts - 1:
            _focus_verified(hwnd)
            time.sleep(0.3)
    return None


def ensure_developer_mode(hwnd: int, emit: Optional[Callable[[str], None]] = None) -> None:
    """Stage 4: Developer mode ON (state-aware, verified by "Load unpacked")."""
    _log(emit, "Stage 4: checking Developer Mode...")

    dev = _page_roots_with_focus(
        hwnd, lambda roots: find_developer_mode_switch(roots, timeout=6.0), emit)
    if dev is not None:
        state = dev.toggled_on()
        if state is True:
            _log(emit, "Developer Mode already enabled.")
        else:
            _log(emit, "Enabling Developer Mode...")

            def _dev_on() -> Optional[bool]:
                found = _page_roots_with_focus(
                    hwnd, lambda roots: load_unpacked_button(roots, timeout=0.5), emit=None,
                    attempts=1)
                return True if found is not None else None

            try:
                _activate_verified(dev, _dev_on, "Could not enable Developer Mode.", emit=emit)
            except ChromeAutomationError:
                # Before failing, give Chrome a moment to rebuild the page tree.
                if _page_roots_with_focus(
                        hwnd, lambda roots: load_unpacked_button(roots, timeout=5.0),
                        emit=None, attempts=1) is None:
                    raise
    else:
        # Control not exposed: rely on the Load unpacked signal.
        if _page_roots_with_focus(
                hwnd, lambda roots: load_unpacked_button(roots, timeout=5.0),
                emit=None) is None:
            raise ChromeAutomationError("Could not enable Developer Mode.")
        _log(emit, "Developer Mode already enabled.")

    if _page_roots_with_focus(
            hwnd, lambda roots: load_unpacked_button(roots, timeout=10.0),
            emit=None) is None:
        raise ChromeAutomationError("Could not enable Developer Mode.")
    _log(emit, "Developer Mode is ON (Load unpacked exposed).")


# --------------------------------------------------------------------------- #
# Stage 5 — Load unpacked                                                     #
# --------------------------------------------------------------------------- #

def click_load_unpacked(hwnd: int, emit: Optional[Callable[[str], None]] = None) -> None:
    """Stage 5: find the real "Load unpacked" button and activate it.

    DETECT → VERIFY → ACTIVATE → VERIFY RESULT (folder picker appears).
    """
    _log(emit, "Stage 5: locating Load unpacked...")
    button = _page_roots_with_focus(
        hwnd, lambda roots: load_unpacked_button(roots), emit)
    if button is None:
        raise ChromeAutomationError("Could not find Load unpacked.")
    if not button.visible:
        raise ChromeAutomationError("Could not find Load unpacked.")
    _log(emit, "Load unpacked found.")

    picker_seen: list[ControlRef] = []

    def _picker_open() -> Optional[bool]:
        try:
            dialog = detect_folder_picker(timeout=0.5, fast=True)
            if dialog is not None:
                picker_seen.append(dialog)
                return True
        except ChromeAutomationError:
            pass
        return None

    _activate_verified(button, _picker_open, "Could not activate Load unpacked.", emit=emit)
    _log(emit, "Load unpacked activated.")
    return picker_seen[0] if picker_seen else None


# --------------------------------------------------------------------------- #
# Stage 6 — folder picker detection                                           #
# --------------------------------------------------------------------------- #

def detect_folder_picker(emit: Optional[Callable[[str], None]] = None,
                         timeout: float = 15.0,
                         fast: bool = False) -> ControlRef:
    """Stage 6: detect Chrome's Windows folder-picker dialog.

    Strategy order:
    1. UIA scan of the desktop for a dialog matching the picker title.
    2. pywinauto desktop enumeration (fallback for exotic UIA states;
       skipped in ``fast`` mode used for quick result checks).
    3. Walk up from the focused control to its owning window.
    """
    if not fast:
        _log(emit, "Stage 6: waiting for the folder picker...")

    def _class_search() -> Optional[ControlRef]:
        """Deep search for the classic file-dialog window (class #32770).

        Chrome's picker is an *owned* dialog: in some process types it is
        nested under the Chrome window instead of the desktop root, so a
        top-level-only scan misses it.  The class name is unambiguous.
        """
        return search_control(
            desktop(), class_name="#32770",
            type_names=("WindowControl", "PaneControl"),
            timeout=0.4,
        )

    def _top_level_picker() -> Optional[ControlRef]:
        """Match only TOP-LEVEL windows by title — deep panes can false-positive."""
        root = desktop()._c
        try:
            windows = root.GetChildren()
        except Exception:
            return None
        for win in windows:
            try:
                ref = ControlRef(win)
                name = ref.name.lower()
                if any(m in name for m in PICKER_TITLE_MARKERS):
                    if ref.type_name in ("WindowControl", "PaneControl"):
                        return ref
            except Exception:
                continue
        return None

    deadline = time.time() + timeout
    picker: Optional[ControlRef] = None
    while time.time() < deadline and picker is None:
        picker = _class_search()
        if picker is None:
            picker = _top_level_picker()
        if picker is None:
            time.sleep(0.1)
    if picker is None and not fast:
        # Fallback 1: pywinauto desktop window enumeration.
        try:
            import warnings

            warnings.filterwarnings("ignore", message="Revert to STA COM threading mode")
            from pywinauto import Desktop

            found = None
            while time.time() < deadline and found is None:
                for win in Desktop(backend="uia").windows():
                    title = (win.window_text() or "").lower()
                    if any(m in title for m in PICKER_TITLE_MARKERS):
                        found = ControlRef(win)
                        break
                if found is None:
                    time.sleep(0.1)
            picker = found
        except Exception:
            picker = None
    if picker is None:
        # Fallback 2: walk up from the focused element — but only accept a
        # result that looks like a real folder-picker dialog.
        from browser.windows_ui import window_containing_focus

        def _focused_dialog() -> Optional[ControlRef]:
            candidate = window_containing_focus()
            if candidate is None:
                return None
            name = candidate.name.lower()
            if candidate.class_name == "#32770" or any(m in name for m in PICKER_TITLE_MARKERS):
                return candidate
            return None

        picker = poll_until(_focused_dialog, timeout=max(0.5, deadline - time.time()))
    if picker is None:
        raise ChromeAutomationError("Could not detect Chrome folder picker.")
    if not fast:
        _log(emit, "Folder picker detected.")
    return picker


# --------------------------------------------------------------------------- #
# Stage 7 — enter the extension path                                          #
# --------------------------------------------------------------------------- #

def _address_edit(dialog: ControlRef, timeout: float = 5.0) -> Optional[ControlRef]:
    """The picker's path/address edit control.

    Classic dialog: the "Folder:" edit shows the current folder NAME once
    navigation has completed (and the full typed path while navigating).
    Modern picker: the "Address" edit shows the full path.
    """
    for name in ("Folder:", "Folder"):
        found = search_control(
            dialog, name=name,
            type_names=("EditControl",),
            timeout=timeout / 4,
        )
        if found is not None:
            return found
    for name in ("Address:", "Address"):
        found = search_control(
            dialog, name=name,
            type_names=("EditControl", "ComboBoxControl", "CustomControl"),
            timeout=timeout / 4,
        )
        if found is not None:
            return found
    return None


def enter_extension_path(dialog: ControlRef, ext_dir: Path,
                         emit: Optional[Callable[[str], None]] = None) -> None:
    """Stage 7: point the picker at *ext_dir* and verify acceptance.

    Verification waits for the navigation to COMPLETE (the folder edit shows
    the extension folder name); the on-disk manifest check is only a final
    fallback.  Never accepts the pre-navigation full-path state.
    """
    _log(emit, "Stage 7: selecting extension directory...")
    path_text = str(ext_dir)
    dialog.focus()

    edit = _address_edit(dialog)
    if edit is not None:
        if not edit.set_value(path_text):
            edit.focus()
            key_combo("{Ctrl}a")
            send_keys(path_text)
    else:
        # Classic picker: Ctrl+L opens the address bar, then type the path.
        key_combo("{Ctrl}l")
        time.sleep(0.2)
        key_combo("{Ctrl}a")
        send_keys(path_text)
    key_combo("{Enter}")

    folder = ext_dir.name

    def _navigated() -> Optional[bool]:
        edit = _address_edit(dialog, timeout=0.3)
        if edit is not None:
            current = edit.value().strip().replace("/", "\\")
            if current == folder or current.lower().endswith("\\" + folder.lower()):
                return True
        return None

    if poll_until(_navigated, timeout=15.0) is None:
        # Final fallback: the manifest must exist at the exact target.
        if not (ext_dir / "manifest.json").is_file():
            raise ChromeAutomationError("Could not select N13 extension directory.")
        _log(emit, "Navigation not observed; accepted via manifest check.")
    _log(emit, f"Extension directory selected: {ext_dir}")


# --------------------------------------------------------------------------- #
# Stage 8 — Select Folder                                                     #
# --------------------------------------------------------------------------- #

def click_select_folder(dialog: ControlRef, emit: Optional[Callable[[str], None]] = None) -> None:
    """Stage 8: find the real "Select Folder" button, verify, then activate.

    The dialog may still be settling after navigation, so a busy/ignored
    activation is retried (never blindly) before failing.
    """
    _log(emit, "Stage 8: locating Select Folder...")

    def _find_button() -> Optional[ControlRef]:
        button = search_control(
            dialog, name="Select Folder",
            type_names=("ButtonControl", "CustomControl"),
            timeout=6.0,
        )
        if button is None:
            # Fallback: standard file-dialog OK button (AutomationId "1").
            button = search_control(dialog, automation_id="1",
                                    type_names=("ButtonControl", "CustomControl"),
                                    timeout=3.0)
        return button

    button = _find_button()
    if button is None:
        raise ChromeAutomationError("Could not activate Select Folder.")
    if not button.visible:
        raise ChromeAutomationError("Could not activate Select Folder.")
    _log(emit, "Select Folder found.")

    # VERIFY RESULT: the picker must disappear after activation.
    def _picker_closed() -> Optional[bool]:
        import time as _time

        _time.sleep(0.4)
        return True if _picker_visible() is None else None

    for attempt in range(3):
        try:
            _activate_verified(button, _picker_closed,
                               "Could not activate Select Folder.",
                               timeout=8.0, emit=emit)
            break
        except ChromeAutomationError:
            if attempt == 2:
                raise
            _log(emit, "Select Folder did not close the picker; retrying...")
            time.sleep(1.0)
            dialog.focus()
            button = _find_button()
            if button is None or not button.visible:
                raise ChromeAutomationError("Could not activate Select Folder.")
    _log(emit, "Select Folder activated.")


def _picker_visible() -> Optional[ControlRef]:
    """Whether a folder picker dialog is currently on screen (best-effort)."""
    try:
        return detect_folder_picker(timeout=0.3, fast=True)
    except ChromeAutomationError:
        return None


# --------------------------------------------------------------------------- #
# Stage 9 — installation verification                                         #
# --------------------------------------------------------------------------- #

def _preferences_files() -> list[Path]:
    """Chrome profile Preferences / Secure Preferences files (read-only scan)."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if not base.is_dir():
        return []
    files: list[Path] = []
    for profile in base.iterdir():
        if not profile.is_dir():
            continue
        for name in ("Secure Preferences", "Preferences"):
            candidate = profile / name
            if candidate.is_file():
                files.append(candidate)
    return files


def extension_in_preferences(ext_dir: Path, name: str) -> Optional[dict]:
    """Scan Chrome's own profile data for the installed N13 extension.

    Returns {"extension_id":..., "state":...} or None.  This is Chrome's own
    persistent record — the UI itself is confirmed separately via UIA.
    """
    target = str(ext_dir.resolve()).lower().rstrip("\\/")
    for pref_file in _preferences_files():
        try:
            data = json.loads(pref_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        settings = (data.get("extensions") or {}).get("settings") or {}
        for ext_id, info in settings.items():
            if not isinstance(info, dict):
                continue
            path = str(info.get("path", "")).lower().rstrip("\\/")
            manifest = info.get("manifest") or {}
            if path and path == target:
                return {
                    "extension_id": ext_id,
                    "state": info.get("state"),
                    "path": info.get("path"),
                    "name": manifest.get("name", ""),
                    "disable_reasons": info.get("disable_reasons"),
                }
        # Also match by manifest name (covers a copy at another path).
        for ext_id, info in settings.items():
            if not isinstance(info, dict):
                continue
            manifest = info.get("manifest") or {}
            manifest_name = str(manifest.get("name", ""))
            if manifest_name and name.lower() in manifest_name.lower():
                return {
                    "extension_id": ext_id,
                    "state": info.get("state"),
                    "path": info.get("path"),
                    "name": manifest_name,
                    "disable_reasons": info.get("disable_reasons"),
                }
    return None


def verify_extension_installed(hwnd: int, ext_dir: Path, name: str,
                               emit: Optional[Callable[[str], None]] = None) -> dict:
    """Stage 9: confirm the N13 extension is present and enabled.

    Combines two independent confirmations:
    * Chrome's own Preferences record (path matches what we selected)
* the visible chrome://extensions/ page (UIA text search)
    """
    _log(emit, "Stage 9: verifying installation...")

    def _pref() -> Optional[dict]:
        return extension_in_preferences(ext_dir, name)

    record = poll_until(_pref, timeout=20.0)
    if record is None:
        raise ChromeAutomationError("Chrome did not install the N13 extension.")

    try:
        roots = chrome_content_roots(hwnd)
    except ChromeAutomationError:
        roots = []
    ui_visible = _search_all_roots(roots, name=name, timeout=8.0) is not None
    if not ui_visible:
        # The tree may not be exposed while the window is background.
        ui_visible = _page_roots_with_focus(
            hwnd, lambda roots: _search_all_roots(roots, name=name, timeout=6.0),
            emit, attempts=2) is not None
    if not ui_visible:
        raise ChromeAutomationError("Chrome did not install the N13 extension.")

    # Unpacked extensions carry no explicit state (None == enabled by
    # default); state 0 or a non-empty disable_reasons means disabled.
    state = record.get("state")
    reasons = record.get("disable_reasons") or []
    enabled = int(state or 1) != 0 and not reasons
    _log(emit, f"Extension installed successfully (id={record['extension_id']}, enabled={enabled}).")
    return {"extension_id": record["extension_id"], "enabled": enabled, "path": record["path"]}
