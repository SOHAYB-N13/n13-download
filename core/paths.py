"""User-data locations (installer-ready).

All user-writable application data lives under a per-user directory, never in
the installation/repository directory:

    %LOCALAPPDATA%\\N13\\        (Windows)   ~/.local/share/n13   (POSIX)
        config\\        config.json, ui_prefs.json, relay token
        data\\          downloads.db (task DB), legacy queue/history JSON
        saved_links\\   batch URL lists (links_*.json), batch resume state
        logs\\          log files

Legacy locations (the old ``~/.config/terminal-download-manager`` and the
project-relative ``saved_links/``) are migrated once, idempotently, without
deleting the originals.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("n13")

_LEGACY_CONFIG_DIR = Path.home() / ".config" / "terminal-download-manager"


def user_data_dir() -> Path:
    """Root per-user data directory (created lazily by the accessors)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "N13"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "n13"


def config_dir() -> Path:
    d = user_data_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = user_data_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def saved_links_dir() -> Path:
    d = user_data_dir() / "saved_links"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "downloads.db"


def _copy_if_missing(src: Path, dst: Path) -> bool:
    try:
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True
    except OSError as exc:
        log.warning("Migration copy failed %s -> %s: %s", src, dst, exc)
    return False


def migrate_legacy_config() -> None:
    """Copy legacy ~/.config/terminal-download-manager files to the new dir."""
    if not _LEGACY_CONFIG_DIR.is_dir():
        return
    _copy_if_missing(_LEGACY_CONFIG_DIR / "config.json", config_dir() / "config.json")
    _copy_if_missing(_LEGACY_CONFIG_DIR / "ui_prefs.json", config_dir() / "ui_prefs.json")


def migrate_legacy_saved_links(project_root: Path) -> None:
    """Migrate the old project-relative ``saved_links/`` directory.

    * ``downloads.db`` + legacy queue/history JSON → ``data/`` (task store)
    * URL lists + batch resume → ``saved_links/``

    Idempotent and failure-aware: originals are never deleted, and a failed
    copy is logged instead of silently dropping data.
    """
    old = Path(project_root) / "saved_links"
    if not old.is_dir():
        return
    if not (data_dir() / "downloads.db").exists():
        _copy_if_missing(old / "downloads.db", data_dir() / "downloads.db")
    for name in ("gui_queue.json", "gui_history.json"):
        _copy_if_missing(old / name, data_dir() / name)
    for f in old.iterdir():
        if f.is_file() and (f.name.startswith("links_") or f.name == "batch_resume.json"):
            _copy_if_missing(f, saved_links_dir() / f.name)
