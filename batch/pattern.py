"""Pattern-based batch scanning and download — redesigned UI."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable, List, Optional, Tuple

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

from config.loader import save_config
from config.settings import AppConfig
from core.download import DownloadController
from core.probe import probe_url
from core.session import SessionManager
from core.utils import format_size, validate_url
from batch.sources import get_saved_links_dir, import_urls_from_file, save_links_to_file
from ui.progress import create_batch_progress, create_scan_progress
from ui.prompts import get_valid_directory

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Small UI helpers
# ─────────────────────────────────────────────────────────────────────────────

from ui.common import _ok, _warn, _err, _info

def _rule(title: str = "", style: str = "cyan") -> None:
    console.print(Rule(title, style=style))


# ─────────────────────────────────────────────────────────────────────────────
# Probe-error classifier
# ─────────────────────────────────────────────────────────────────────────────

class _Kind:
    NOT_FOUND = "not_found"
    TIMEOUT   = "timeout"
    SERVER    = "server"
    OTHER     = "other"


def _classify(message: str) -> str:
    msg = message.lower()
    if "404" in msg or "not found" in msg:
        return _Kind.NOT_FOUND
    if "timeout" in msg:
        return _Kind.TIMEOUT
    if msg.startswith("http 5") or "server" in msg:
        return _Kind.SERVER
    return _Kind.OTHER


# ─────────────────────────────────────────────────────────────────────────────
# Scan — sequential
# ─────────────────────────────────────────────────────────────────────────────

def scan_pattern_urls(
    pattern: str,
    config: AppConfig,
    session: SessionManager,
    start_num: int = 1,
    padding: int = 2,
    max_consecutive_404: int = 5,
    max_attempts: int = 200,
    max_server_errors: int = 10,
    quiet: bool = False,
) -> List[str]:
    """Probe a numbered URL pattern sequentially.

    ``quiet=True`` disables the rich console progress UI — required when the
    scan runs on a worker thread inside the GUI (no usable stdout there).
    """
    found: List[str] = []
    failures_404 = 0
    server_errors = 0
    num = start_num
    attempts = 0

    if quiet:
        while failures_404 < max_consecutive_404 and attempts < max_attempts:
            attempts += 1
            num_str = f"{num:0{padding}d}" if padding > 0 else str(num)
            test_url = pattern.replace("*", num_str)
            reachable, _, _, _, err = probe_url(test_url, config, session, timeout=8)
            if reachable:
                found.append(test_url)
                failures_404 = 0
                server_errors = 0
            else:
                kind = _classify(err)
                if kind == _Kind.NOT_FOUND:
                    failures_404 += 1
                elif kind == _Kind.SERVER:
                    server_errors += 1
                    if server_errors >= max_server_errors:
                        break
            num += 1
        return found

    with create_scan_progress() as progress:
        task = progress.add_task("Scanning…", total=None)

        while failures_404 < max_consecutive_404 and attempts < max_attempts:
            attempts += 1
            num_str = f"{num:0{padding}d}" if padding > 0 else str(num)
            test_url = pattern.replace("*", num_str)

            progress.update(task, description=f"Probing [{num_str}] …")

            reachable, size, _, _, err = probe_url(test_url, config, session, timeout=8)

            if reachable:
                found.append(test_url)
                failures_404 = 0
                server_errors = 0
                size_str = f"  [dim]{format_size(size)}[/dim]" if size else ""
                progress.console.print(
                    f"  [bold green]✔[/bold green]  [cyan]{num_str}[/cyan]{size_str}  "
                    f"[dim]{test_url}[/dim]"
                )
            else:
                kind = _classify(err)
                if kind == _Kind.NOT_FOUND:
                    failures_404 += 1
                    if failures_404 <= 2:
                        progress.console.print(
                            f"  [dim]✖  {num_str}  404 not found[/dim]"
                        )
                elif kind == _Kind.TIMEOUT:
                    progress.console.print(
                        f"  [yellow]⏱  {num_str}  timeout[/yellow]"
                    )
                elif kind == _Kind.SERVER:
                    server_errors += 1
                    progress.console.print(
                        f"  [red]✖  {num_str}  server error — {err}[/red]"
                    )
                    if server_errors >= max_server_errors:
                        progress.console.print(
                            "  [bold red]Too many server errors — scan stopped.[/bold red]"
                        )
                        break
                else:
                    progress.console.print(
                        f"  [red]✖  {num_str}  {err}[/red]"
                    )
            num += 1

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Scan — regex / range
# ─────────────────────────────────────────────────────────────────────────────

def scan_regex_urls(
    template: str,
    config: AppConfig,
    session: SessionManager,
    start_num: int = 1,
    end_num: int = 100,
    quiet: bool = False,
) -> List[str]:
    if end_num < start_num:
        return []
    found: List[str] = []
    total = end_num - start_num + 1

    if quiet:
        for num in range(start_num, end_num + 1):
            test_url = template.replace("*", str(num))
            if not validate_url(test_url):
                continue
            reachable, _, _, _, _ = probe_url(test_url, config, session, timeout=8)
            if reachable:
                found.append(test_url)
        return found

    with create_scan_progress() as progress:
        task = progress.add_task(f"Scanning {start_num}–{end_num} …", total=total)
        for num in range(start_num, end_num + 1):
            test_url = template.replace("*", str(num))
            if not validate_url(test_url):
                progress.advance(task)
                continue
            progress.update(task, description=f"Probing [{num}] …")
            reachable, size, _, _, _ = probe_url(test_url, config, session, timeout=8)
            if reachable:
                found.append(test_url)
                size_str = f"  [dim]{format_size(size)}[/dim]" if size else ""
                progress.console.print(
                    f"  [bold green]✔[/bold green]  [cyan]{num}[/cyan]{size_str}  "
                    f"[dim]{test_url}[/dim]"
                )
            progress.advance(task)

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Batch state persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_batch_state(path: Path) -> dict:
    if not path.exists():
        return {"completed": [], "failed": [], "pending": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {"completed": [], "failed": [], "pending": []}
    except (OSError, json.JSONDecodeError):
        return {"completed": [], "failed": [], "pending": []}


def _save_batch_state(path: Path, state: dict) -> None:
    """Atomically persist batch state so a crash mid-write never corrupts it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        import os as _os
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            try:
                _os.fsync(f.fileno())
            except OSError:
                pass
        _os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Batch download — single unified progress display
# ─────────────────────────────────────────────────────────────────────────────

def batch_download_urls(
    urls: List[str],
    config: AppConfig,
    session: SessionManager,
    download_dir: Path,
    batch_state_path: Optional[Path] = None,
    on_item: Optional[Callable[[int, int, str, bool], None]] = None,
) -> Tuple[int, int]:
    """Download a list of URLs one by one inside a single clean progress UI.

    Two rows are shown at all times:
      • Overall  — N / M files + overall ETA
      • Current  — filename + byte bar + speed + ETA
    """
    state_path = batch_state_path or (
        get_saved_links_dir(Path.cwd()) / "batch_resume.json"
    )
    unique_urls = list(dict.fromkeys(urls))
    if not unique_urls:
        return 0, 0

    state = _load_batch_state(state_path)
    prev_done     = state.get("completed", [])
    completed_set = {u for u in prev_done if isinstance(u, str) and u in unique_urls}
    pending       = [u for u in unique_urls if u not in completed_set]
    total_count   = len(unique_urls)
    success_count = len(completed_set)

    with create_batch_progress() as progress:

        overall_task = progress.add_task(
            _overall_label(success_count, total_count),
            total=total_count,
            completed=success_count,
        )
        file_task = progress.add_task("Waiting…", total=None, completed=0)

        def _on_progress(done: int, total: int) -> None:
            progress.update(
                file_task,
                completed=done,
                total=total if total > 0 else None,
            )

        controller = DownloadController(
            config,
            session,
            console_print=progress.console.print,
            show_progress=False,
        )

        for url in pending:
            file_label = (urlparse(url).path.split("/")[-1] or url)[:50]

            progress.reset(file_task, total=None)
            progress.update(
                file_task,
                description=f"[cyan]{file_label}[/cyan]",
                completed=0,
                total=None,
            )
            progress.update(
                overall_task,
                description=_overall_label(success_count, total_count),
            )

            ok = controller.download_file(
                url, download_dir, progress_callback=_on_progress
            )

            if ok:
                success_count += 1
                completed_set.add(url)
                state["completed"] = list(completed_set)

                # mark file row done
                _task_obj = next(
                    (t for t in progress.tasks if t.id == file_task), None
                )
                _done_bytes = int(_task_obj.total or 0) if _task_obj else 0
                progress.update(
                    file_task,
                    description=f"[bold green]✔[/bold green] {file_label}",
                    completed=_done_bytes,
                )
                progress.update(overall_task, advance=1)
            else:
                failed = {u for u in state.get("failed", []) if isinstance(u, str)}
                failed.add(url)
                state["failed"] = list(failed)
                progress.update(
                    file_task,
                    description=f"[bold red]✖[/bold red] {file_label}",
                )

            state["pending"] = [u for u in unique_urls if u not in completed_set]
            _save_batch_state(state_path, state)

            if on_item:
                on_item(success_count, total_count, url, ok)

        # final labels
        progress.update(
            overall_task,
            description=_overall_label(success_count, total_count),
        )
        progress.update(file_task, description="")

    return success_count, total_count


def _overall_label(done: int, total: int) -> str:
    pct = int(done / total * 100) if total else 0
    return (
        f"[bold white]Overall[/bold white]  "
        f"[cyan]{done}[/cyan][dim]/{total}[/dim] files  "
        f"[dim]({pct}%)[/dim]"
    )


# ─────────────────────────────────────────────────────────────────────────────
# URL preview table
# ─────────────────────────────────────────────────────────────────────────────

def _preview_urls(urls: List[str], max_shown: int = 6) -> None:
    """Print a tidy numbered preview of the found URLs."""
    t = Table(
        show_header=True,
        header_style="bold dim",
        box=None,
        padding=(0, 2),
    )
    t.add_column("#",    style="dim",        justify="right", width=4)
    t.add_column("File", style="cyan",       no_wrap=True)
    t.add_column("URL",  style="dim white",  no_wrap=True)

    shown = urls[:max_shown]
    for i, u in enumerate(shown, 1):
        name = urlparse(u).path.split("/")[-1] or u
        t.add_row(str(i), name[:48], u[:60])

    console.print(t)
    if len(urls) > max_shown:
        _info(f"… and {len(urls) - max_shown} more.")


# ─────────────────────────────────────────────────────────────────────────────
# Scan stats card
# ─────────────────────────────────────────────────────────────────────────────

def _scan_summary(found: List[str]) -> None:
    """Show a small summary panel after a scan completes."""
    console.print()
    if not found:
        console.print(Panel(
            "[yellow]No files were found.[/yellow]",
            border_style="yellow",
            padding=(0, 2),
        ))
        return

    console.print(Panel(
        f"  [bold green]✔  {len(found)} file(s) found[/bold green]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()
    _preview_urls(found)
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Pattern batch entry point
# ─────────────────────────────────────────────────────────────────────────────

def batch_download_by_pattern(
    config: AppConfig,
    session: SessionManager,
    base_dir: Path,
) -> Optional[Tuple[int, int]]:
    """Interactive pattern-batch flow with improved UI."""
    print("\033[2J\033[H", end="")

    _rule("Pattern Batch Download", "cyan")
    console.print()

    # ── mode selection ───────────────────────────────────────────────────────
    console.print(Panel(
        "  [bold yellow]1[/bold yellow]  Scan a URL pattern   "
        "[dim](e.g. https://site.com/file*.rar)[/dim]\n"
        "  [bold yellow]2[/bold yellow]  Import a link list   "
        "[dim](JSON / CSV / TXT)[/dim]\n"
        "  [bold yellow]3[/bold yellow]  Resume previous batch",
        title="[bold]Source[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))

    try:
        mode = Prompt.ask("  Choose", choices=["1", "2", "3"], default="1")
    except (KeyboardInterrupt, EOFError):
        return None

    found_urls: List[str] = []
    pattern_used = ""

    # ── 2: import ────────────────────────────────────────────────────────────
    if mode == "2":
        imported = import_urls_from_file(base_dir)
        if not imported:
            return None
        found_urls = imported
        _ok(f"Imported {len(found_urls)} URL(s).")

    # ── 3: resume ────────────────────────────────────────────────────────────
    elif mode == "3":
        state_path = get_saved_links_dir(base_dir) / "batch_resume.json"
        state      = _load_batch_state(state_path)
        pending    = state.get("pending") or state.get("failed") or []
        if not pending:
            _warn("Nothing to resume — no saved batch found.")
            return None
        found_urls = pending
        _info(f"Resuming {len(found_urls)} pending URL(s).")

    # ── 1: scan ──────────────────────────────────────────────────────────────
    else:
        console.print()
        _info("Use [bold]*[/bold] as a numeric placeholder.")
        _info("Example: [cyan]https://example.com/episode*.mkv[/cyan]")
        console.print()

        try:
            pattern = Prompt.ask("  [cyan]URL pattern[/cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            return None

        if pattern.count("*") != 1 or not validate_url(pattern.replace("*", "1")):
            _err("Invalid pattern — must contain exactly one * and be a valid URL.")
            return None
        pattern_used = pattern

        try:
            raw_start = Prompt.ask("  Start number", default="1")
            start_num = int(raw_start)
            raw_pad   = Prompt.ask("  Zero-padding digits  [dim](0 = none)[/dim]", default="2")
            padding   = int(raw_pad)
        except (ValueError, KeyboardInterrupt, EOFError):
            _err("Invalid input.")
            return None

        if start_num < 0 or not 0 <= padding <= 12:
            _err("Start must be ≥ 0 and padding between 0 and 12.")
            return None

        try:
            scan_mode = Prompt.ask(
                "  Scan mode",
                choices=["sequential", "range"],
                default="sequential",
            )
        except (KeyboardInterrupt, EOFError):
            return None

        console.print()
        _rule("Scanning", "yellow")
        console.print()

        if scan_mode == "range":
            try:
                raw_end = Prompt.ask("  End number", default="100")
                end_num = int(raw_end)
            except (ValueError, KeyboardInterrupt, EOFError):
                _err("Invalid end number.")
                return None
            if end_num < start_num:
                _err("End must be ≥ start.")
                return None
            found_urls = scan_regex_urls(
                pattern, config, session, start_num, end_num
            )
        else:
            found_urls = scan_pattern_urls(
                pattern, config, session, start_num, padding
            )

    # ── scan summary ─────────────────────────────────────────────────────────
    _scan_summary(found_urls)

    if not found_urls:
        return None

    # ── action choice ─────────────────────────────────────────────────────────
    console.print(Panel(
        "  [bold yellow]1[/bold yellow]  Download all now\n"
        "  [bold yellow]2[/bold yellow]  Save links for later",
        title=f"[bold]{len(found_urls)} file(s) ready[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))

    try:
        action = Prompt.ask("  Choose", choices=["1", "2"], default="1")
    except (KeyboardInterrupt, EOFError):
        return None

    if action == "2":
        saved = save_links_to_file(found_urls, base_dir, pattern_used)
        if saved:
            _ok(f"Links saved to: [cyan]{saved}[/cyan]")
        return None

    # ── download ──────────────────────────────────────────────────────────────
    console.print()
    dl_dir = get_valid_directory("  Destination folder", config.download_dir)
    console.print()
    _rule("Downloading", "cyan")
    console.print()

    success, total = batch_download_urls(found_urls, config, session, dl_dir)

    console.print()
    _rule(style="grey30")
    if success == total:
        _ok(f"All {total} file(s) downloaded successfully.")
    else:
        _warn(f"{success}/{total} succeeded  —  {total - success} failed.")

    save_config(config)
    return success, total
