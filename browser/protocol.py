"""Cross-platform browser protocol handler registration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from config.settings import AppConfig
from browser.live_server import run_live_server
from browser.icons import ensure_extension_icons

console = Console()

try:
    import winreg

    WINDOWS = True
except ImportError:
    WINDOWS = False


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _handler_script_path() -> Path:
    return _project_root() / "browser" / "dldm_handler.py"


def _main_script_path() -> Path:
    return _project_root() / "d.py"


def create_chrome_extension(dst: Optional[Path] = None) -> Path:
    """Copy extension template to chrome_extension/ with icons and token placeholder."""
    src = _project_root() / "extension"
    dst = dst or (_project_root() / "chrome_extension")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ensure_extension_icons(dst)
    return dst


def token_file_payload(config: AppConfig) -> str:
    """The exact token.json content the extension expects for *config*."""
    return json.dumps({
        "live_server_url": f"http://127.0.0.1:{config.live_server_port}/download",
        "token": config.live_server_token,
    })


def sync_extension_token(config: AppConfig, ext_dir: Optional[Path] = None) -> Optional[Path]:
    """Ensure the installable ``chrome_extension/`` exists and has the right token.

    The token is stable in ``config.json`` (``config/loader._ensure_token``), but
    the extension's ``token.json`` is a snapshot created by
    ``create_chrome_extension``.  If the two ever drift (a regenerated or
    foreign config), the browser extension reports "authorization failed".

    This idempotent sync is the reliable recovery mechanism: it is run every
    time the Live Server starts.  If the extension copy does not exist yet it is
    generated from the bundled template first, so a fresh install always ends up
    with a loadable, correctly-authenticated extension (no manual "Create
    Chrome extension copy" step required).  It never invents or rotates the
    token — it only mirrors the existing credential.
    """
    ext_dir = ext_dir or (_project_root() / "chrome_extension")
    if not ext_dir.is_dir():
        try:
            ext_dir = create_chrome_extension(ext_dir)
        except Exception:
            return None
    try:
        token_path = ext_dir / "token.json"
        token_path.write_text(token_file_payload(config), encoding="utf-8")
        return token_path
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Native Messaging host (silent extension → app launch, no Chrome dialog)      #
# --------------------------------------------------------------------------- #

NATIVE_HOST_NAME = "com.n13.download_manager"


def _native_host_dir() -> Path:
    return _project_root() / "build" / "native_host"


def _unpacked_extension_ids(ext_dir: Path) -> list[str]:
    """Candidate extension IDs Chrome derives for an *unpacked* extension.

    Chrome computes the ID as the first 32 hex chars of SHA-256 over the
    absolute path, mapped 0-9a-f → a-p.  Case handling differs across
    platforms/versions, so we register both the native-case and lower-case
    variants — extra origins in allowed_origins are harmless.
    """
    import hashlib

    try:
        native = str(ext_dir.resolve())
    except OSError:
        return []

    def to_id(path: str) -> str:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]
        return "".join(chr(ord("a") + int(c, 16)) for c in digest)

    ids = [to_id(native)]
    lowered = native.lower()
    if lowered != native:
        ids.append(to_id(lowered))
    return ids


def _discover_loaded_extension_ids() -> list[str]:
    """IDs of N13 unpacked extensions actually loaded in Chrome/Edge profiles.

    Modern Chrome builds no longer derive unpacked IDs from the folder path in
    a predictable way, so the only reliable source is the browser's own
    Preferences / Secure Preferences.  We scan every profile for unpacked
    extensions whose path or manifest name looks like the N13 extension and
    return their IDs.  Read-only; missing browsers are simply skipped.
    """
    ids: list[str] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ids
    browsers = (
        os.path.join(local, "Google", "Chrome", "User Data"),
        os.path.join(local, "Microsoft", "Edge", "User Data"),
    )
    for browser_dir in browsers:
        if not os.path.isdir(browser_dir):
            continue
        pref_files: list[str] = []
        for profile in os.listdir(browser_dir):
            for name in ("Secure Preferences", "Preferences"):
                candidate = os.path.join(browser_dir, profile, name)
                if os.path.isfile(candidate):
                    pref_files.append(candidate)
        for pref_file in pref_files:
            try:
                with open(pref_file, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            settings = (data.get("extensions") or {}).get("settings") or {}
            for ext_id, info in settings.items():
                if not isinstance(info, dict) or not info.get("path"):
                    continue
                manifest = info.get("manifest") or {}
                name = str(manifest.get("name", ""))
                path_l = str(info.get("path", "")).lower()
                if (
                    path_l.endswith("chrome_extension")
                    or "n13" in name.lower()
                    or "download manager" in name.lower()
                ):
                    if ext_id and ext_id not in ids:
                        ids.append(ext_id)
    return ids


def register_native_host() -> bool:
    """Register the native messaging host for the current user (HKCU only).

    Writes the host manifest + launcher .bat under ``build/native_host`` and
    points Chrome (and Edge) at it via the registry.  This lets the extension
    start the N13 GUI silently — no "Open N13 Download Manager?" dialog.
    Idempotent: safe to call on every app startup.
    """
    host_dir = _native_host_dir()
    host_script = _project_root() / "browser" / "native_host.py"
    if not host_script.is_file():
        console.print(f"[red]Native host script missing: {host_script}[/red]")
        return False

    try:
        host_dir.mkdir(parents=True, exist_ok=True)

        # Prefer pythonw (no console flash when Chrome spawns the host).
        python_exe = Path(sys.executable)
        pythonw = python_exe.with_name("pythonw.exe")
        runner = pythonw if pythonw.is_file() else python_exe

        bat_path = host_dir / "n13_native_host.bat"
        bat_path.write_text(
            "@echo off\n"
            f'"{runner}" "{host_script}"\n',
            encoding="utf-8",
        )

        # Register for every known unpacked-extension location plus the IDs
        # actually loaded in installed browsers (the reliable source).
        origins: list[str] = []
        candidates: list[str] = []
        for ext_dir in (_project_root() / "chrome_extension", _project_root() / "extension"):
            candidates.extend(_unpacked_extension_ids(ext_dir))
        candidates.extend(_discover_loaded_extension_ids())
        for ext_id in candidates:
            origin = f"chrome-extension://{ext_id}/"
            if origin not in origins:
                origins.append(origin)

        manifest = {
            "name": NATIVE_HOST_NAME,
            "description": "N13 Download Manager silent launcher",
            "path": str(bat_path),
            "type": "stdio",
            "allowed_origins": origins,
        }
        manifest_path = host_dir / f"{NATIVE_HOST_NAME}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if not WINDOWS:
            console.print("[yellow]Native messaging registry setup is Windows-only; "
                          f"manifest written to {manifest_path}[/yellow]")
            return True

        for reg_path in (
            "Software\\Google\\Chrome\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
            "Software\\Microsoft\\Edge\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        ):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))

        console.print(f"[green]Native messaging host registered ({len(origins)} extension origin(s)).[/green]")
        return True
    except OSError as exc:
        console.print(f"[red]Failed to register native messaging host: {exc}[/red]")
        return False


def register_protocol() -> bool:
    if WINDOWS:
        return _register_protocol_windows()
    if sys.platform == "darwin":
        return _register_protocol_macos()
    if sys.platform.startswith("linux"):
        return _register_protocol_linux()
    console.print("[red]Protocol registration not supported on this OS.[/red]")
    return False


def _register_protocol_windows() -> bool:
    script_path = _main_script_path()
    handler_path = _handler_script_path()
    python_exe = sys.executable

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\dldm") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Terminal Download Manager Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{python_exe}",0')

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command") as key:
            cmd = f'"{python_exe}" "{handler_path}" "%1"'
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)

        return True
    except OSError as exc:
        console.print(f"[red]Failed to register protocol: {exc}[/red]")
        return False


def _register_protocol_linux() -> bool:
    handler = _handler_script_path()
    desktop = Path.home() / ".local" / "share" / "applications" / "dldm-handler.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    content = f"""[Desktop Entry]
Name=Terminal Download Manager
Exec={sys.executable} {handler} %u
Type=Application
Terminal=false
MimeType=x-scheme-handler/dldm;
"""
    desktop.write_text(content, encoding="utf-8")
    subprocess.run(["xdg-mime", "default", "dldm-handler.desktop", "x-scheme-handler/dldm"], check=False)
    return True


def _register_protocol_macos() -> bool:
    console.print("[yellow]On macOS, use Live Server mode or create a custom URL scheme via Automator.[/yellow]")
    return False


def unregister_protocol() -> bool:
    if not WINDOWS:
        console.print("[yellow]Manual unregister may be required on this platform.[/yellow]")
        return False
    try:
        def delete_key(root, path):
            try:
                with winreg.OpenKey(root, path) as key:
                    while True:
                        try:
                            subkey = winreg.EnumKey(key, 0)
                            delete_key(root, f"{path}\\{subkey}")
                        except OSError:
                            break
                winreg.DeleteKey(root, path)
            except FileNotFoundError:
                pass

        delete_key(winreg.HKEY_CURRENT_USER, r"Software\Classes\dldm")
        return True
    except OSError as exc:
        console.print(f"[red]Failed to unregister: {exc}[/red]")
        return False


def is_protocol_registered() -> bool:
    if not WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command"):
            return True
    except FileNotFoundError:
        return False


def test_protocol_handler(require_confirm: bool = True) -> None:
    test_url = "https://www.example.com"
    if require_confirm:
        console.print("[yellow]This will launch a download via the protocol handler.[/yellow]")
        if not Confirm.ask("Continue with test?", default=False):
            return
    encoded = urllib.parse.quote(test_url, safe="")
    webbrowser.open(f"dldm://{encoded}")


def browser_integration_setup(config: AppConfig, session) -> None:
    console.print("\n[bold cyan]Browser Integration Setup[/bold cyan]")
    registered = is_protocol_registered()
    protocol_line = (
        f"Protocol handler (dldm://): {'Registered' if registered else 'Not registered'}"
        if WINDOWS
        else "Protocol handler: use Live Server on this platform"
    )

    console.print(
        Panel(
            "[bold]Connect Chrome to this app:[/bold]\n"
            "• Live Server — cross-platform, app must stay open (authenticated)\n"
            "• Protocol handler — optional on Windows/Linux\n\n"
            f"{protocol_line}",
            title="Browser Integration",
            border_style="cyan",
        )
    )

    menu = [
        ("1", "Register protocol"),
        ("2", "Create Chrome extension copy"),
        ("3", "Test protocol handler"),
        ("4", "Show installation instructions"),
        ("5", "Unregister protocol"),
        ("6", "Start Live Server"),
        ("b", "Back"),
    ]
    for key, label in menu:
        if key == "1" and not WINDOWS:
            continue
        if key == "5" and not WINDOWS:
            continue
        console.print(f"[yellow]{key}[/yellow] {label}")

    choice = Prompt.ask("Choose", default="6")

    if choice == "1" and register_protocol():
        console.print("[bold green]Protocol registered.[/bold green]")
    elif choice == "2":
        ext_dir = create_chrome_extension()
        token_path = ext_dir / "token.json"
        token_path.write_text(
            f'{{"live_server_url":"http://127.0.0.1:{config.live_server_port}/download",'
            f'"token":"{config.live_server_token}"}}',
            encoding="utf-8",
        )
        console.print(f"[green]Extension copied to {ext_dir}[/green]")
        console.print("[dim]Load unpacked in chrome://extensions/[/dim]")
    elif choice == "3":
        test_protocol_handler(config.require_protocol_confirm)
    elif choice == "4":
        ext_dir = _project_root() / "extension"
        console.print(
            Panel(
                f"1. Copy extension from {ext_dir}\n"
                "2. Load unpacked in Chrome\n"
                f"3. Start Live Server (option 6) — token saved in config\n"
                "4. Right-click links → Download with TDM",
                title="Instructions",
                border_style="green",
            )
        )
    elif choice == "5":
        unregister_protocol()
    elif choice == "6":
        run_live_server(config, session)
