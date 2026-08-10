#!/usr/bin/env python3
"""
Terminal Download Manager (TDM)
Multi-threaded download manager with resume, batch, browser integration.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

import urllib3
from colorama import init
from rich.console import Console

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.loader import load_config, save_config
from config.settings import AppConfig
from core.context import DownloadContext
from core.download import DownloadController
from core.security import validate_download_url
from core.session import SessionManager
from core.utils import normalize_url, validate_url
from browser.protocol import (
    create_chrome_extension,
    register_protocol,
    unregister_protocol,
)
from ui.menu import interactive_mode, print_banner
from ui.bridge import launch_app

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
init(autoreset=True)
console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal Download Manager")
    parser.add_argument("url", nargs="?", help="Download URL")
    parser.add_argument("-d", "--dir", help="Download directory")
    parser.add_argument("-t", "--threads", type=int, help="Number of threads")
    parser.add_argument("--checksum", help="Expected MD5 or SHA256 hash")
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable SSL verification (also set TDM_INSECURE_SSL=1)",
    )
    parser.add_argument("--from-browser", action="store_true")
    parser.add_argument("--url-file", help="Read URL from file")
    parser.add_argument("--register", action="store_true", help="Register dldm:// protocol")
    parser.add_argument("--unregister", action="store_true", help="Unregister dldm:// protocol")
    parser.add_argument("--create-extension", action="store_true", help="Copy Chrome extension")
    parser.add_argument("--gui", action="store_true", help="Launch the graphical interface")
    return parser.parse_args()


def apply_args(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    if args.threads:
        config.num_threads = max(1, args.threads)
    if args.dir:
        config.download_dir = args.dir
    if args.insecure_ssl:
        if os.environ.get("TDM_INSECURE_SSL") != "1":
            console.print(
                "[red]Refusing insecure SSL without TDM_INSECURE_SSL=1 environment variable.[/red]"
            )
            console.print("[dim]Example: set TDM_INSECURE_SSL=1 && python d.py --insecure-ssl URL[/dim]")
            sys.exit(2)
        config.verify_ssl = False
        config.allow_insecure_ssl = True
    return config


def setup_signal_handlers(session: SessionManager) -> None:
    def handler(sig, frame):
        saved = DownloadContext.save_now()
        session.close()
        if saved:
            console.print("\n[yellow]Interrupted — download state saved for resume.[/yellow]")
        else:
            console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)


def _write_token_file(token_path: Path, config: AppConfig) -> None:
    import tempfile, os
    token_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({
        "live_server_url": f"http://127.0.0.1:{config.live_server_port}/download",
        "token": config.live_server_token,
    })
    fd, tmp = tempfile.mkstemp(
        prefix=token_path.name + ".", suffix=".tmp", dir=str(token_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, token_path)
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> None:
    config = load_config()
    args = parse_args()
    config = apply_args(config, args)
    session = SessionManager()
    setup_signal_handlers(session)

    if args.register:
        sys.exit(0 if register_protocol() else 1)
    if args.unregister:
        sys.exit(0 if unregister_protocol() else 1)
    if args.create_extension:
        ext = create_chrome_extension()
        token_path = ext / "token.json"
        _write_token_file(token_path, config)
        console.print(f"[green]Extension ready at {ext}[/green]")
        sys.exit(0)

    if args.gui:
        launch_app(config, session)
        session.close()
        return

    if args.url_file:
        try:
            args.url = Path(args.url_file).read_text(encoding="utf-8").strip()
            Path(args.url_file).unlink(missing_ok=True)
        except OSError as exc:
            console.print(f"[red]Failed to read URL file: {exc}[/red]")
            sys.exit(1)

    print_banner()
    console.print(
        f"[dim]Threads: {config.num_threads} | SSL: {'ON' if config.verify_ssl else 'OFF'} | "
        f"Dir: {config.download_dir}[/dim]\n"
    )

    if args.url:
        args.url = normalize_url(args.url)
        if not validate_url(args.url):
            console.print(f"[red]Invalid URL: {args.url}[/red]")
            sys.exit(1)
        ok, err = validate_download_url(args.url, block_private=config.block_private_urls)
        if not ok:
            console.print(f"[red]Blocked URL: {err}[/red]")
            sys.exit(1)

        if args.from_browser:
            console.print(f"[cyan]Browser download: {args.url}[/cyan]\n")

        controller = DownloadController(config, session, console.print)
        download_dir = Path(args.dir) if args.dir else Path(config.download_dir)
        success = controller.download_file(
            args.url,
            download_dir,
            verify_checksum=bool(args.checksum),
            expected_hash=args.checksum,
        )
        session.close()
        sys.exit(0 if success else 1)

    try:
        interactive_mode(config, session, ROOT)
    except (KeyboardInterrupt, EOFError):
        # Prompts from optional flows (file import, browser setup, etc.) may
        # raise outside the menu loop.  Treat them as a normal clean exit.
        console.print("\n[yellow]Exited safely. Settings were preserved.[/yellow]")
    finally:
        save_config(config)
        session.close()


if __name__ == "__main__":
    main()
