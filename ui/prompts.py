"""User prompts and input helpers — redesigned."""

from pathlib import Path
from typing import List, Optional

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from config.settings import AppConfig
from core.probe import probe_url
from core.security import validate_download_url
from core.session import SessionManager
from core.utils import format_size, is_valid_directory, normalize_url, validate_url

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

from ui.common import _ok, _warn, _err, _info


# ─────────────────────────────────────────────────────────────────────────────
# File-info card shown after a successful probe
# ─────────────────────────────────────────────────────────────────────────────

def _show_file_info(
    url: str,
    size: int,
    supports_range: bool,
    filename: str,
) -> None:
    """Render a compact info card for the probed file."""

    def _card(label: str, value: str, colour: str = "cyan") -> Panel:
        return Panel(
            f"[{colour}]{value}[/{colour}]",
            title=f"[dim]{label}[/dim]",
            border_style="grey30",
            padding=(0, 2),
        )

    cards = [
        _card("File",    filename or "unknown"),
        _card("Size",    format_size(size) if size else "unknown"),
        _card("Resume",  "✔ yes" if supports_range else "✖ no",
              "green" if supports_range else "yellow"),
    ]
    console.print()
    console.print(Columns(cards, equal=True, expand=True))
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# URL input
# ─────────────────────────────────────────────────────────────────────────────

def get_valid_url(
    prompt_text: str,
    config: AppConfig,
    session: SessionManager,
) -> Optional[str]:
    """Ask for a URL, validate it, probe it, show an info card, return it."""
    while True:
        try:
            raw = Prompt.ask(f"  [cyan]{prompt_text}[/cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print()
            _warn("Cancelled.")
            return None

        url = normalize_url(raw.strip())

        if not url:
            _err("URL cannot be empty.")
            continue

        if not validate_url(url):
            _err("Invalid URL — must start with http:// or https://")
            continue

        ok, err = validate_download_url(url, block_private=config.block_private_urls)
        if not ok:
            _err(f"Blocked: {err}")
            try:
                if not Confirm.ask("  Try a different URL?", default=True):
                    return None
            except (KeyboardInterrupt, EOFError):
                return None
            continue

        _info("Probing link …")
        reachable, size, supports_range, filename, error_msg = probe_url(
            url, config, session, timeout=10
        )

        if reachable:
            _ok("Link is reachable.")
            _show_file_info(url, size, supports_range, filename)
            return url

        _err(f"Cannot reach server: {error_msg}")
        try:
            if not Confirm.ask("  Try a different URL?", default=True):
                return None
        except (KeyboardInterrupt, EOFError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Directory input
# ─────────────────────────────────────────────────────────────────────────────

def get_valid_directory(prompt_text: str, default_dir: str) -> Path:
    """Ask for a destination directory; fall back to default on any error."""
    while True:
        try:
            raw = Prompt.ask(
                f"  [cyan]{prompt_text}[/cyan]",
                default=default_dir,
            ).strip() or default_dir
        except (KeyboardInterrupt, EOFError):
            return Path(default_dir)

        if is_valid_directory(raw):
            _ok(f"Destination: [cyan]{raw}[/cyan]")
            return Path(raw)

        _err(f"Cannot create or access: {raw}")
        try:
            if Confirm.ask("  Use the default directory instead?", default=True):
                return Path(default_dir)
        except (KeyboardInterrupt, EOFError):
            return Path(default_dir)


# ─────────────────────────────────────────────────────────────────────────────
# SSL toggle
# ─────────────────────────────────────────────────────────────────────────────

def toggle_ssl_verification(config: AppConfig) -> None:
    if config.verify_ssl:
        _warn("Disabling SSL verification makes connections insecure.")
        try:
            if not Confirm.ask("  Confirm — disable SSL verification?", default=False):
                _info("SSL unchanged.")
                return
        except (KeyboardInterrupt, EOFError):
            _info("SSL unchanged.")
            return
        config.allow_insecure_ssl = True
        config.verify_ssl = False
        _warn("SSL verification is now [bold red]OFF[/bold red] (insecure).")
    else:
        config.verify_ssl = True
        config.allow_insecure_ssl = False
        _ok("SSL verification is now [bold green]ON[/bold green].")


# ─────────────────────────────────────────────────────────────────────────────
# Multi-line URL entry
# ─────────────────────────────────────────────────────────────────────────────

def read_multiline_urls() -> List[str]:
    """Interactive multi-line URL collector with live validation feedback."""
    console.print()
    console.print(Panel(
        "  Paste or type one URL per line.\n"
        "  Submit an [bold]empty line[/bold] when done.",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()

    urls: List[str] = []
    seen: set[str] = set()
    index = 1

    while True:
        try:
            raw = input(f"  [{index:>2}]  ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            _warn("Input stopped.")
            break

        if not raw:
            break

        url = normalize_url(raw)
        if not validate_url(url):
            _err(f"Invalid URL skipped: {raw}")
            continue

        if url in seen:
            _warn(f"Duplicate skipped: {url}")
            continue

        urls.append(url)
        seen.add(url)
        _ok(f"[dim]{url}[/dim]")
        index += 1

    console.print()
    if urls:
        _ok(f"{len(urls)} URL(s) collected.")
    else:
        _warn("No URLs collected.")
    return urls
