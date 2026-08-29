"""N13 Chrome extension installer — one-click automated "Load unpacked".

Orchestrates the nine stages:

    Stage 1  discover the real N13 extension directory (dynamic, validated)
    Stage 2  open / reuse Google Chrome
    Stage 3  open chrome://extensions/
    Stage 4  enable Developer Mode (state-aware)
    Stage 5  activate the real "Load unpacked" button
    Stage 6  detect the Windows folder picker
    Stage 7  enter the discovered extension path (verified)
    Stage 8  activate "Select Folder"
    Stage 9  verify the extension is installed and enabled

CLI (useful for incremental real-Windows testing):

    python -m browser.extension_installer            # full install
    python -m browser.extension_installer 1          # run one stage
    python -m browser.extension_installer 2..9
"""

from __future__ import annotations

import argparse
from typing import Callable, Optional

from browser.chrome_automation import (
    ChromeAutomationError,
    click_load_unpacked,
    click_select_folder,
    detect_folder_picker,
    ensure_chrome_running,
    ensure_developer_mode,
    enter_extension_path,
    open_extensions_page,
    verify_extension_installed,
)
from browser.extension_locator import ExtensionLocatorError, discover_extension_dir

TAG = "[N13 Extension]"

EXTENSION_DISPLAY_NAME = "N13 Download Manager"


def install_extension(emit: Optional[Callable[[str], None]] = None,
                      progress: Optional[Callable[[str], None]] = None) -> dict:
    """Install the N13 Chrome extension end-to-end.

    *emit*      receives every stage log line.
    *progress*  receives a short human-readable stage name (UI status text).

    Returns {"ok": True, "extension_dir":..., "extension_id":..., "enabled":...}
    or {"ok": False, "error": <clear message>}.
    """
    def stage(name: str) -> None:
        if progress is not None:
            try:
                progress(name)
            except Exception:
                pass

    try:
        stage("Locating extension...")
        ext_dir = discover_extension_dir(emit)

        stage("Opening Chrome...")
        hwnd = ensure_chrome_running(emit)

        stage("Opening chrome://extensions/...")
        open_extensions_page(hwnd, emit)

        stage("Checking Developer Mode...")
        ensure_developer_mode(hwnd, emit)

        stage("Clicking Load unpacked...")
        click_load_unpacked(hwnd, emit)

        stage("Waiting for folder picker...")
        dialog = detect_folder_picker(emit)

        stage("Selecting extension directory...")
        enter_extension_path(dialog, ext_dir, emit)

        stage("Clicking Select Folder...")
        click_select_folder(dialog, emit)

        stage("Verifying installation...")
        result = verify_extension_installed(hwnd, ext_dir, EXTENSION_DISPLAY_NAME, emit)

        return {
            "ok": True,
            "extension_dir": str(ext_dir),
            "extension_id": result.get("extension_id", ""),
            "enabled": bool(result.get("enabled")),
        }
    except (ExtensionLocatorError, ChromeAutomationError) as exc:
        message = str(exc).replace(f"{TAG} ", "")
        if emit is not None:
            try:
                emit(f"{TAG} FAILED: {message}")
            except Exception:
                pass
        return {"ok": False, "error": message}
    except Exception as exc:  # never hide the real exception from the log
        if emit is not None:
            try:
                emit(f"{TAG} FAILED (unexpected): {exc!r}")
            except Exception:
                pass
        return {"ok": False, "error": f"Unexpected error: {exc}"}


# --------------------------------------------------------------------------- #
# CLI — incremental real-Windows testing                                      #
# --------------------------------------------------------------------------- #

_STAGE_RUNNERS = {
    "1": lambda emit: {"ok": True, "extension_dir": str(discover_extension_dir(emit))},
    "2": lambda emit: {"ok": True, "hwnd": ensure_chrome_running(emit)},
    "3": lambda emit: (
        (lambda hwnd: (open_extensions_page(hwnd, emit), {"ok": True, "hwnd": hwnd})[1])(
            ensure_chrome_running(emit))),
    "4": lambda emit: (
        (lambda hwnd: (ensure_developer_mode(hwnd, emit), {"ok": True})[1])(
            ensure_chrome_running(emit))),
    "5": lambda emit: (
        (lambda hwnd: (click_load_unpacked(hwnd, emit), {"ok": True})[1])(
            ensure_chrome_running(emit))),
    "6": lambda emit: {"ok": True, "picker": str(detect_folder_picker(emit)._c)},
    "7": lambda emit: (
        (lambda d: (enter_extension_path(d, discover_extension_dir(emit), emit), {"ok": True})[1])(
            detect_folder_picker(emit))),
    "8": lambda emit: (
        (lambda d: (click_select_folder(d, emit), {"ok": True})[1])(
            detect_folder_picker(emit))),
    "9": lambda emit: (
        (lambda hwnd: (
            verify_extension_installed(hwnd, discover_extension_dir(emit), EXTENSION_DISPLAY_NAME, emit),
            {"ok": True})[1])(
            ensure_chrome_running(emit))),
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="N13 Chrome extension installer")
    parser.add_argument("stage", nargs="?", default="all",
                        help="stage number 1..9 (default: full install)")
    args = parser.parse_args(argv)

    emit = lambda line: print(line)  # noqa: E731

    if args.stage == "all":
        result = install_extension(emit=emit, progress=lambda s: print(f"{TAG} progress: {s}"))
    else:
        runner = _STAGE_RUNNERS.get(args.stage)
        if runner is None:
            print(f"{TAG} Unknown stage: {args.stage}")
            return 2
        print(f"{TAG} Running stage {args.stage} only")
        try:
            result = runner(emit)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
    if result.get("ok"):
        print(f"{TAG} OK: {result}")
        return 0
    print(f"{TAG} FAILED: {result.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())