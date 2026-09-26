"""Cross-platform browser protocol handler registration.

``dldm://`` is an *application* protocol: the operating system must launch the
N13 executable and hand it the raw URL as an ordinary command-line argument::

    dldm://<url>  ->  Windows protocol handler  ->  "<install dir>\\N13.exe" "%1"
                                                          |
                                            N13.exe parses dldm:// itself
                                                          |
                                                  N13 Download Manager

The install directory is resolved at registration time on the machine that is
registering — never hardcoded.  ``browser/dldm_handler.py`` is an internal
helper module (reused by ``browser/native_host.py``) and must never be
registered as an external protocol launcher.
"""

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


def _dev_entry_script() -> Path:
    """Entry script used when running from a source checkout.

    ``build/n13_entry.py`` is preferred: it is the very same entry point the
    frozen application uses, so a source checkout registers the identical
    command *shape* (``"<launcher>" "<entry>" "%1"``) instead of a private
    handler script.  ``d.py`` is only a fallback for very old checkouts.
    """
    root = _project_root()
    entry = root / "build" / "n13_entry.py"
    return entry if entry.is_file() else root / "d.py"


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
    """Writable directory for the native-host manifest + launcher.

    Installed builds must never write inside the installation directory (it can
    be read-only, e.g. ``C:\\Program Files``), so the per-user data directory is
    used there.  Source checkouts keep the historical ``build/native_host``.
    """
    if is_frozen():
        try:
            from core.paths import user_data_dir

            return user_data_dir() / "native_host"
        except Exception:
            pass
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

    The launcher is built the same way as the ``dldm://`` command: from the
    *running* application (``N13.exe --native-host`` when installed, the
    Python interpreter + host script in a source checkout).  A packaged build
    must never spawn ``python.exe`` or a bundled ``.py`` path.

    Idempotent: safe to call on every app startup.
    """
    if os_integration_disabled():
        return False
    host_dir = _native_host_dir()
    host_script = _project_root() / "browser" / "native_host.py"
    if not host_script.is_file():
        console.print(f"[red]Native host script missing: {host_script}[/red]")
        return False

    try:
        host_dir.mkdir(parents=True, exist_ok=True)

        if is_frozen():
            # Installed build: Chrome runs the application itself in native-host
            # mode.  The path is this installation's real location.
            launcher = f'"{installed_exe()}" --native-host'
        else:
            # Source checkout: prefer pythonw (no console flash when Chrome
            # spawns the host).
            python_exe = Path(sys.executable)
            pythonw = python_exe.with_name("pythonw.exe")
            runner = pythonw if pythonw.is_file() else python_exe
            launcher = f'"{runner}" "{host_script}"'

        bat_path = host_dir / "n13_native_host.bat"
        bat_path.write_text(
            "@echo off\n"
            f"{launcher}\n",
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


# --------------------------------------------------------------------------- #
# dldm:// protocol registration                                                #
# --------------------------------------------------------------------------- #

PROTOCOL_SCHEME = "dldm"
PROTOCOL_KEY = r"Software\Classes\dldm"
PROTOCOL_COMMAND_KEY = PROTOCOL_KEY + r"\shell\open\command"
PROTOCOL_ICON_KEY = PROTOCOL_KEY + r"\DefaultIcon"
PROTOCOL_DESCRIPTION = "URL:N13 Download Manager Protocol"
PRODUCTION_EXE_NAME = "N13.exe"

# Tokens that must NEVER appear in a *production* (installed) protocol command.
# Any of them means an interpreter/console/development launcher would be
# started instead of the GUI application.
FORBIDDEN_IN_PRODUCTION_COMMAND = (
    "dldm_handler",
    "python.exe",
    "pythonw.exe",
    "py.exe",
    "pyw.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "\\d.py",
    "/d.py",
    "n13_entry.py",
)


def is_frozen() -> bool:
    """True when running from a packaged build (PyInstaller one-dir/one-file)."""
    return bool(getattr(sys, "frozen", False))


#: Set ``N13_SKIP_OS_INTEGRATION=1`` to stop the process from writing any OS
#: integration (``dldm://`` protocol handler, Chrome/Edge native-messaging
#: keys).  Used by the test suite, headless/CI runs and managed deployments
#: that do not want the application to touch the registry.
OS_INTEGRATION_ENV = "N13_SKIP_OS_INTEGRATION"


def os_integration_disabled() -> bool:
    """True when this process must not modify the operating system."""
    value = os.environ.get(OS_INTEGRATION_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def installed_exe() -> Path:
    """Absolute path of the executable that should own ``dldm://``.

    In a packaged build ``sys.executable`` *is* ``N13.exe``, located in
    whatever directory this user installed to (``C:\\Program Files\\...``,
    ``D:\\Apps\\N13``, a portable folder, a per-user install, ...).  Nothing
    about the path is hardcoded anywhere.
    """
    exe = Path(sys.executable)
    if exe.name.lower() != PRODUCTION_EXE_NAME.lower():
        # Defensive: a renamed/relocated launcher still resolves to the
        # application executable sitting next to it.
        sibling = exe.with_name(PRODUCTION_EXE_NAME)
        if sibling.is_file():
            return sibling
    return exe


def protocol_launcher() -> tuple[str, Optional[str]]:
    """``(launcher, entry_script)`` used for the OS command.

    * installed build → ``("<install dir>\\N13.exe", None)``
    * source checkout → ``(python.exe, build/n13_entry.py)``
    """
    if is_frozen():
        return str(installed_exe()), None
    return sys.executable, str(_dev_entry_script())


def protocol_launch_command() -> str:
    """The exact command the OS must run for ``dldm://...``.

    Production form, with the path resolved per installation::

        "<install dir>\\N13.exe" "%1"

    The raw URL is passed to N13.exe as a normal command-line argument and is
    parsed by the application itself (``build/n13_entry.py``) — no Python
    handler script, no interpreter, no console window.
    """
    launcher, entry = protocol_launcher()
    if entry:
        return f'"{launcher}" "{entry}" "%1"'
    return f'"{launcher}" "%1"'


def protocol_icon_command() -> str:
    """``DefaultIcon`` value — the real application icon, never an interpreter."""
    launcher, _entry = protocol_launcher()
    return f'"{launcher}",0'


def _normalise_command(command: str) -> str:
    """Case/separator-insensitive form of a registry command line."""
    return " ".join((command or "").strip().lower().replace("/", "\\").split())


def _command_matches(current: str, expected: str) -> bool:
    return _normalise_command(current) == _normalise_command(expected)


def _is_production_safe_command(command: str) -> bool:
    """True when *command* launches the installed application (not an interpreter)."""
    lowered = (command or "").lower()
    if not lowered.strip():
        return False
    if any(token in lowered for token in FORBIDDEN_IN_PRODUCTION_COMMAND):
        return False
    return PRODUCTION_EXE_NAME.lower() in lowered


def read_protocol_command() -> Optional[str]:
    """Current per-user ``dldm://`` command line, or ``None`` if unregistered."""
    if not WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL_COMMAND_KEY) as key:
            value, _type = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return None


def protocol_registration_status() -> dict:
    """Diagnostics for the current ``dldm://`` registration (per-user)."""
    expected = protocol_launch_command()
    current = read_protocol_command()
    return {
        "scheme": PROTOCOL_SCHEME,
        "registered": current is not None,
        "current": current or "",
        "expected": expected,
        "current_ok": bool(current) and _command_matches(current, expected),
        "legacy": bool(current)
        and is_frozen()
        and not _is_production_safe_command(current or ""),
    }


def register_protocol(force: bool = False) -> bool:
    """Register ``dldm://`` for the current user.

    With ``force=False`` (default) an already-correct registration is left
    completely untouched — no registry write at all.
    """
    if WINDOWS:
        return ensure_protocol_registration(force=force)
    if sys.platform == "darwin":
        return _register_protocol_macos()
    if sys.platform.startswith("linux"):
        return _register_protocol_linux()
    console.print("[red]Protocol registration not supported on this OS.[/red]")
    return False


def ensure_protocol_registration(force: bool = False) -> bool:
    """Idempotently (re)assert the correct per-user ``dldm://`` registration.

    Called on every application start, so it also *repairs* installations that
    an older build left broken (for example ``N13.exe <...>\\dldm_handler.py
    "%1"`` or a command pointing at a previous install directory):

    * missing            → register
    * stale / legacy     → rewrite with this installation's real path
    * already correct    → no write

    Only ``HKCU\\Software\\Classes\\dldm`` is ever touched, so other
    applications' protocol registrations are never affected.
    """
    if not WINDOWS:
        return False
    if os_integration_disabled():
        return False
    try:
        if not force:
            current = read_protocol_command()
            if current and _command_matches(current, protocol_launch_command()):
                return True
        return _register_protocol_windows()
    except OSError:
        return False


def _register_protocol_windows() -> bool:
    """Write the production registration for the current user (HKCU only)."""
    command = protocol_launch_command()
    icon = protocol_icon_command()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROTOCOL_DESCRIPTION)
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_ICON_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_COMMAND_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)

        return True
    except OSError as exc:
        console.print(f"[red]Failed to register protocol: {exc}[/red]")
        return False


def _register_protocol_linux() -> bool:
    launcher, entry = protocol_launcher()
    exec_line = f'"{launcher}" "{entry}" %u' if entry else f'"{launcher}" %u'
    desktop = Path.home() / ".local" / "share" / "applications" / "dldm-handler.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    content = f"""[Desktop Entry]
Name=N13 Download Manager
Exec={exec_line}
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
    if os_integration_disabled():
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

        delete_key(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY)
        return True
    except OSError as exc:
        console.print(f"[red]Failed to unregister: {exc}[/red]")
        return False


def is_protocol_registered() -> bool:
    if not WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL_COMMAND_KEY):
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
