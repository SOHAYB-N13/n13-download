"""Import/export URL lists (JSON, CSV, TXT)."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.prompt import Prompt

from core.utils import normalize_url, validate_url

console = Console()


def get_saved_links_dir(base_dir: Path) -> Path:
    saved_dir = base_dir / "saved_links"
    saved_dir.mkdir(parents=True, exist_ok=True)
    return saved_dir


def save_links_to_file(urls: List[str], base_dir: Path, pattern: str = "") -> Optional[Path]:
    """Save a URL list to a timestamped JSON file using an atomic write.

    A temporary sibling file is written first and then renamed so a crash
    mid-write never leaves a truncated file that silently poisons future
    import operations.
    """
    import os as _os
    saved_dir = get_saved_links_dir(base_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = saved_dir / f"links_{timestamp}.json"
    data = {
        "saved_at": datetime.now().isoformat(),
        "pattern": pattern,
        "total": len(urls),
        "urls": urls,
    }
    tmp = filepath.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, filepath)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return filepath


def load_url_list(path: Path) -> Optional[List[str]]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                urls = data.get("urls", [])
            elif isinstance(data, list):
                urls = data
            else:
                console.print("[red]JSON must contain a URL list or a 'urls' field.[/red]")
                return None
        elif suffix == ".csv":
            urls = []
            with open(path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    for cell in row:
                        cell = cell.strip()
                        if cell.startswith("http"):
                            urls.append(cell)
        elif suffix in (".txt", ".list"):
            with open(path, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip().startswith("http")]
        else:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            urls = re.findall(r"https?://[^\s\"'<>]+", content)

        if not isinstance(urls, list):
            console.print("[red]The selected file does not contain a valid URL list.[/red]")
            return None
        urls = [normalize_url(str(u)) for u in urls if str(u).strip()]
        urls = [u for u in urls if validate_url(u)]
        return list(dict.fromkeys(urls)) or None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        console.print(f"[red]Failed to read file: {exc}[/red]")
        return None


def import_urls_from_file(base_dir: Path) -> Optional[List[str]]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        filepath = filedialog.askopenfilename(
            title="Select URL list file",
            initialdir=str(get_saved_links_dir(base_dir)),
            filetypes=[
                ("Supported", "*.json;*.csv;*.txt"),
                ("JSON", "*.json"),
                ("CSV", "*.csv"),
                ("Text", "*.txt"),
                ("All", "*.*"),
            ],
        )
        root.destroy()
        if not filepath:
            console.print("[yellow]No file selected.[/yellow]")
            return None
        urls = load_url_list(Path(filepath))
        if urls:
            console.print(f"[green]✓ Imported {len(urls)} link(s)[/green]")
        return urls
    except Exception:
        console.print("[yellow]File dialog unavailable. Enter path manually.[/yellow]")
        path_input = Prompt.ask("[cyan]Path to URL list file").strip()
        if not path_input or not Path(path_input).exists():
            console.print("[red]File not found.[/red]")
            return None
        urls = load_url_list(Path(path_input))
        if urls:
            console.print(f"[green]✓ Imported {len(urls)} link(s)[/green]")
        return urls
