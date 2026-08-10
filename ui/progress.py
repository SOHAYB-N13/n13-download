"""Rich progress-bar helpers — compatible with rich ≥ 13."""

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from core.speed import SpeedTracker

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Custom columns
# ─────────────────────────────────────────────────────────────────────────────

class SpeedColumn(ProgressColumn):
    """Reads live speed from a SpeedTracker (single-file downloads)."""

    def __init__(self, tracker: SpeedTracker) -> None:
        super().__init__()
        self.tracker = tracker

    def render(self, task) -> Text:  # type: ignore[override]
        return Text(self.tracker.formatted_speed, style="bold cyan")


# ─────────────────────────────────────────────────────────────────────────────
# Single-file download progress  (CLI / menu mode)
# ─────────────────────────────────────────────────────────────────────────────

def create_download_progress(
    speed_tracker: SpeedTracker,
    transient: bool = False,
) -> Progress:
    """Full progress bar for a single multi-threaded download."""
    return Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("[bold cyan]{task.description}", justify="left"),
        BarColumn(bar_width=None, style="cyan", complete_style="bold cyan"),
        TaskProgressColumn(),
        DownloadColumn(),
        SpeedColumn(speed_tracker),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=transient,
        expand=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scan progress  (pattern probing)
# ─────────────────────────────────────────────────────────────────────────────

def create_scan_progress() -> Progress:
    """Animated spinner while probing pattern URLs."""
    return Progress(
        SpinnerColumn(spinner_name="dots2", style="yellow"),
        TextColumn("[yellow]{task.description}", justify="left"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Batch download progress  (two-row: overall + current file)
# ─────────────────────────────────────────────────────────────────────────────

def create_batch_progress() -> Progress:
    """Two-task progress display for batch downloads.

    Row 1 — overall : N / M files + ETA
    Row 2 — current : filename + byte bar + speed + ETA

    Both tasks live in one Progress instance so there is never more than one
    live progress widget on the terminal at a time.
    """
    return Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("{task.description}", justify="left"),
        BarColumn(bar_width=None, style="cyan", complete_style="bold green"),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        expand=True,
    )
