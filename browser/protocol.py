"""Cross-platform browser protocol handler registration."""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

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


def create_chrome_extension() -> Path:
    """Copy extension template to chrome_extension/ with icons and token placeholder."""
    src = _project_root() / "extension"
    dst = _project_root() / "chrome_extension"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ensure_extension_icons(dst)
    return dst


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
