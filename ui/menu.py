"""Interactive terminal UI — fully responsive."""

from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pyfiglet
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from batch.pattern import batch_download_by_pattern, batch_download_urls
from batch.sources import import_urls_from_file
from browser.protocol import browser_integration_setup, is_protocol_registered
from config.loader import save_config
from config.settings import AppConfig
from core.download import DownloadController
from core.session import SessionManager
from core.utils import detect_hash_algorithm, format_size
from ui.prompts import (
    get_valid_directory,
    get_valid_url,
    read_multiline_urls,
    toggle_ssl_verification,
)

try:
    import winreg
    WINDOWS = True
except ImportError:
    WINDOWS = False

# ── module-level console (used only for prompts / non-layout output) ─────────
console = Console()

# ── palette ──────────────────────────────────────────────────────────────────
_C = {
    "key":        "bold yellow",
    "ok":         "bold green",
    "warn":       "bold yellow",
    "err":        "bold red",
    "border":     "blue",
    "sub_border": "cyan",
}


# ─────────────────────────────────────────────────────────────────────────────
# Live-width console factory
# Every render call gets a fresh Console so it always sees the current terminal
# width — resizing the window takes effect on the very next menu redraw.
# ─────────────────────────────────────────────────────────────────────────────

def _con() -> Console:
    """Return a Console sized to the *current* terminal width."""
    return Console()


def _term_width() -> int:
    """Current terminal column count (floor 40, ceiling 220)."""
    try:
        w = shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        w = 80
    return max(40, min(w, 220))


# ─────────────────────────────────────────────────────────────────────────────
# Tiny helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clear() -> None:
    print("\033[2J\033[H", end="")


def _rule(title: str = "", style: str = "blue") -> None:
    _con().print(Rule(title, style=style))


from ui.common import _ok, _warn, _err, _info


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

def print_banner() -> None:
    _clear()
    c = _con()
    try:
        art = pyfiglet.figlet_format("N  1  3", font="slant")
    except Exception:
        art = "N13"
    c.print(Align.center(f"[bold cyan]{art}[/bold cyan]"))
    c.print(Align.center(
        "[dim]Professional Download Manager  ·  fast · secure · resumable[/dim]\n"
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Status bar — adapts to terminal width
# Wide  (≥ 100 cols): 6 cards in one row via Columns
# Narrow (<  100 cols): compact 2-column key/value table
# ─────────────────────────────────────────────────────────────────────────────

def _status_bar(c: Console, config: AppConfig, auto_shutdown: bool) -> None:
    schedule = config.get_schedule_datetime()
    proto    = "✔ Ready" if (WINDOWS and is_protocol_registered()) else "—"
    width    = _term_width()

    items = [
        ("Connections",   str(config.num_threads),                             None),
        ("SSL",           "ON" if config.verify_ssl else "OFF",                config.verify_ssl),
        ("Speed limit",   format_size(config.max_speed_bps) + "/s"
                          if config.max_speed_bps else "Unlimited",            None),
        ("Schedule",      schedule.strftime("%H:%M") if schedule else "OFF",   None),
        ("Auto-shutdown", "ON" if auto_shutdown else "OFF",                    auto_shutdown or None),
        ("Browser",       proto,                                                None),
    ]

    if width >= 100:
        # ── card layout ──────────────────────────────────────────────────────
        def _card(label: str, value: str, ok) -> Panel:
            colour = "green" if ok is True else "red" if ok is False else "cyan"
            return Panel(
                Align.center(f"[{colour}]{value}[/{colour}]"),
                title=f"[dim]{label}[/dim]",
                border_style="grey30",
                padding=(0, 1),
            )
        c.print(Columns(
            [_card(l, v, ok) for l, v, ok in items],
            equal=True,
            expand=True,
        ))
    else:
        # ── compact table ────────────────────────────────────────────────────
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim",  min_width=14)
        t.add_column(style="cyan", min_width=10)
        t.add_column(style="dim",  min_width=14)
        t.add_column(style="cyan", min_width=10)
        # pair items two-per-row
        pairs = [(items[i], items[i + 1] if i + 1 < len(items) else ("", "", None))
                 for i in range(0, len(items), 2)]
        for (l1, v1, _), (l2, v2, _) in pairs:
            t.add_row(l1, v1, l2, v2)
        c.print(t)

    c.print()


# ─────────────────────────────────────────────────────────────────────────────
# Menu table — hint column hidden when terminal is narrow
# ─────────────────────────────────────────────────────────────────────────────

def _menu_table(items: list[tuple[str, str, str]], width: int) -> Table:
    show_hint = width >= 72
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", style="bold yellow", width=4)
    t.add_column(style="white",   min_width=24)
    if show_hint:
        t.add_column(style="dim")
    for key, label, hint in items:
        row = [f"[bold yellow]{key}[/bold yellow]", label]
        if show_hint:
            row.append(hint)
        t.add_row(*row)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────────────────────────────────────

def _render_main_menu(config: AppConfig, auto_shutdown: bool) -> None:
    _clear()
    c     = _con()
    width = _term_width()

    # ── header ───────────────────────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M  %d %b %Y")
    if width >= 60:
        hdr = Table.grid(expand=True)
        hdr.add_column()
        hdr.add_column(justify="right")
        hdr.add_row(
            "[bold cyan]N13 Download Manager[/bold cyan]  [dim]v2[/dim]",
            f"[dim]{now}[/dim]",
        )
        c.print(hdr)
    else:
        c.print("[bold cyan]N13 Download Manager[/bold cyan]")
    c.print(Rule(style="grey30"))

    # ── status ───────────────────────────────────────────────────────────────
    _status_bar(c, config, auto_shutdown)

    # ── menu ─────────────────────────────────────────────────────────────────
    items = [
        ("1", "⬇  Single download",    "One URL, full speed"),
        ("2", "⬇  Batch download",      "Multiple links at once"),
        ("3", "⬇  Verified download",   "MD5 / SHA256 checksum"),
        ("4", "⬇  Pattern batch",       "Scan numbered series"),
        ("5", "🌐  Browser integration", "Extension & protocol"),
        ("s", "⚙  Settings",            "All configuration"),
        ("q", "✕  Quit",                ""),
    ]
    c.print(Panel(
        _menu_table(items, width),
        title="[bold]Downloads[/bold]",
        border_style=_C["border"],
        padding=(1, 2),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Settings menu
# ─────────────────────────────────────────────────────────────────────────────

def _render_settings_menu(config: AppConfig, auto_shutdown: bool) -> None:
    c     = _con()
    width = _term_width()
    schedule = config.get_schedule_datetime()
    limit    = format_size(config.max_speed_bps) + "/s" if config.max_speed_bps else "Unlimited"

    items = [
        ("1", "Connections",      f"{config.num_threads} threads"),
        ("2", "Speed limit",      limit),
        ("3", "SSL",              "[green]ON[/green]" if config.verify_ssl else "[red]OFF[/red]"),
        ("4", "Auto-shutdown",    "[green]ON[/green]" if auto_shutdown else "OFF"),
        ("5", "Schedule",         schedule.strftime("%Y-%m-%d %H:%M") if schedule else "OFF"),
        ("6", "Proxy",            _trim(config.proxy_url or "—", width - 30)),
        ("7", "Download folder",  _trim(config.download_dir, width - 30)),
        ("8", "Browser",          ""),
        ("9", "Save to disk",     ""),
        ("b", "← Back",          ""),
    ]
    c.print(Panel(
        _menu_table(items, width),
        title="[bold cyan]⚙  Settings[/bold cyan]",
        border_style=_C["sub_border"],
        padding=(1, 2),
    ))


def _trim(s: str, max_len: int) -> str:
    max_len = max(10, max_len)
    return s if len(s) <= max_len else "…" + s[-(max_len - 1):]


# ─────────────────────────────────────────────────────────────────────────────
# Settings logic
# ─────────────────────────────────────────────────────────────────────────────

def _settings_mode(
    config: AppConfig,
    session: SessionManager,
    auto_shutdown: bool,
    controller_ref: list,
) -> bool:
    while True:
        _clear()
        _render_settings_menu(config, auto_shutdown)

        try:
            choice = Prompt.ask(
                "\n[bold cyan]Settings[/bold cyan]",
                choices=["1","2","3","4","5","6","7","8","9","b"],
                default="b",
            )
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "b":
            break

        elif choice == "1":
            _rule("Connections", "cyan")
            _info(f"Current: [cyan]{config.num_threads}[/cyan]  (1–64)")
            try:
                v = int(Prompt.ask("  New value", default=str(config.num_threads)))
                if not 1 <= v <= 64:
                    _err("Must be 1–64.")
                else:
                    config.num_threads = v
                    controller_ref[0] = DownloadController(config, session, console.print)
                    _ok(f"Connections → {v}")
            except (ValueError, KeyboardInterrupt, EOFError):
                _warn("No change.")

        elif choice == "2":
            _rule("Speed Limit", "cyan")
            cur = format_size(config.max_speed_bps) + "/s" if config.max_speed_bps else "Unlimited"
            _info(f"Current: [cyan]{cur}[/cyan]")
            _info("Examples: [bold]5MB[/bold]  [bold]500KB[/bold]  [bold]2GB[/bold]  [bold]0[/bold]=unlimited")
            try:
                raw = Prompt.ask("  Limit", default="0").strip().upper()
                bps = _parse_speed(raw)
                if bps is None:
                    _err("Invalid format.")
                else:
                    config.max_speed_bps = bps
                    _ok(f"Speed limit → {format_size(bps) + '/s' if bps else 'Unlimited'}")
            except (KeyboardInterrupt, EOFError):
                _warn("No change.")

        elif choice == "3":
            _rule("SSL Verification", "cyan")
            toggle_ssl_verification(config)

        elif choice == "4":
            auto_shutdown = not auto_shutdown
            _ok(f"Auto-shutdown → {'[green]ON[/green]' if auto_shutdown else 'OFF'}")

        elif choice == "5":
            _rule("Schedule", "cyan")
            _info("Format [bold]HH:MM[/bold] — blank to disable.")
            try:
                raw = Prompt.ask("  Time", default="").strip()
            except (KeyboardInterrupt, EOFError):
                _warn("Unchanged."); continue
            if not raw:
                config.set_schedule_datetime(None)
                _ok("Schedule disabled.")
            else:
                try:
                    h, m = map(int, raw.split(":"))
                    now = datetime.now()
                    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if t <= now:
                        t += timedelta(days=1)
                    config.set_schedule_datetime(t)
                    _ok(f"Scheduled for {t:%Y-%m-%d %H:%M}")
                except (ValueError, TypeError):
                    _err("Use HH:MM, e.g. 21:30")

        elif choice == "6":
            _rule("Proxy", "cyan")
            cur = config.proxy_url or ""
            _info(f"Current: [cyan]{cur or '—'}[/cyan]")
            _info("Format: [bold]http://host:port[/bold]  or blank to disable.")
            try:
                raw = Prompt.ask("  Proxy URL", default=cur).strip()
                config.proxy_url = raw or None
                session.configure(config)
                _ok(f"Proxy → {raw or 'disabled'}")
            except (KeyboardInterrupt, EOFError):
                _warn("No change.")

        elif choice == "7":
            _rule("Download Folder", "cyan")
            _info(f"Current: [cyan]{config.download_dir}[/cyan]")
            try:
                raw = Prompt.ask("  New path", default=config.download_dir).strip()
                p = Path(raw).expanduser()
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    config.download_dir = str(p)
                    _ok(f"Folder → {p}")
                except OSError as exc:
                    _err(f"Cannot create: {exc}")
            except (KeyboardInterrupt, EOFError):
                _warn("No change.")

        elif choice == "8":
            _rule("Browser Integration", "cyan")
            browser_integration_setup(config, session)

        elif choice == "9":
            save_config(config)
            _ok("Saved.")

        console.print()
        try:
            Prompt.ask("  [dim]Enter to continue…[/dim]", default="")
        except (KeyboardInterrupt, EOFError):
            pass

    return auto_shutdown


def _parse_speed(raw: str) -> int | None:
    raw = raw.strip().upper().replace(" ", "")
    if raw in ("0", ""):
        return 0
    for suffix, mult in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1)):
        if raw.endswith(suffix):
            try:
                return int(float(raw[:-len(suffix)]) * mult)
            except ValueError:
                return None
    try:
        return int(raw)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown
# ─────────────────────────────────────────────────────────────────────────────

def shutdown_computer(delay_seconds: int = 60) -> bool:
    from rich.prompt import Confirm
    if not Confirm.ask("\n  [yellow]Are you sure you want to shut down?[/yellow]", default=False):
        _info("Canceled.")
        return False
    os_name = platform.system()
    try:
        if os_name == "Windows":
            subprocess.run(["shutdown", "/s", "/t", str(delay_seconds)], check=True)
        elif os_name in ("Linux", "Darwin"):
            subprocess.run(["shutdown", "-h", f"+{max(1, delay_seconds // 60)}"], check=True)
        else:
            _err(f"Unsupported OS: {os_name}"); return False
        _warn(f"Shutdown in {delay_seconds}s.")
        return True
    except subprocess.CalledProcessError as exc:
        _err(f"Shutdown failed: {exc}"); return False


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def interactive_mode(config: AppConfig, session: SessionManager, base_dir: Path) -> None:
    controller_ref = [DownloadController(config, session, console.print)]
    auto_shutdown  = False

    while True:
        _render_main_menu(config, auto_shutdown)

        try:
            choice = Prompt.ask(
                "\n[bold cyan]›[/bold cyan]",
                choices=["1", "2", "3", "4", "5", "s", "q"],
                default="q",
            )
        except (KeyboardInterrupt, EOFError):
            _ok("Goodbye!"); break

        if choice == "q":
            _ok("Goodbye!"); break

        elif choice == "1":
            _clear(); _rule("Single Download", "cyan"); console.print()
            url = get_valid_url("URL", config, session)
            if not url: continue
            dl_dir = get_valid_directory("Destination", config.download_dir)
            console.print()
            if controller_ref[0].download_file(url, dl_dir) and auto_shutdown:
                shutdown_computer(30)
            _pause()

        elif choice == "2":
            _clear(); _rule("Batch Download", "cyan"); console.print()
            console.print(Panel(
                "  [bold yellow]1[/bold yellow]  Enter links manually\n"
                "  [bold yellow]2[/bold yellow]  Import from file  [dim](JSON / CSV / TXT)[/dim]",
                border_style="cyan", padding=(1, 2),
            ))
            try:
                sub = Prompt.ask("  Source", choices=["1", "2"], default="1")
            except (KeyboardInterrupt, EOFError):
                continue
            urls = import_urls_from_file(base_dir) if sub == "2" else read_multiline_urls()
            if not urls:
                _warn("No URLs."); _pause(); continue
            _info(f"{len(urls)} URL(s) ready.")
            dl_dir = get_valid_directory("Destination", config.download_dir)
            console.print()
            ok_n, total = batch_download_urls(urls, config, session, dl_dir)
            console.print(); _rule(style="grey30")
            _ok(f"Done — {ok_n}/{total} succeeded.")
            if auto_shutdown and ok_n > 0:
                shutdown_computer(30)
            _pause()

        elif choice == "3":
            _clear(); _rule("Verified Download", "cyan"); console.print()
            url = get_valid_url("URL", config, session)
            if not url: continue
            try:
                hash_val = Prompt.ask("  Expected hash  [dim](MD5 or SHA256)[/dim]").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            try:
                algo = detect_hash_algorithm(hash_val)
                _info(f"Algorithm: [cyan]{algo.upper()}[/cyan]")
            except ValueError as exc:
                _err(str(exc)); _pause(); continue
            dl_dir = get_valid_directory("Destination", config.download_dir)
            console.print()
            if controller_ref[0].download_file(
                url, dl_dir, verify_checksum=True, expected_hash=hash_val
            ) and auto_shutdown:
                shutdown_computer(30)
            _pause()

        elif choice == "4":
            result = batch_download_by_pattern(config, session, base_dir)
            if auto_shutdown and result and result[0] > 0:
                shutdown_computer(30)
            _pause()

        elif choice == "5":
            _clear(); _rule("Browser Integration", "cyan"); console.print()
            browser_integration_setup(config, session)
            _pause()

        elif choice == "s":
            auto_shutdown = _settings_mode(config, session, auto_shutdown, controller_ref)


def _pause() -> None:
    console.print()
    try:
        Prompt.ask("[dim]  Enter to return…[/dim]", default="")
    except (KeyboardInterrupt, EOFError):
        pass
