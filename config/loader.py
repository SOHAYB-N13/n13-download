"""Load and save persistent configuration with atomic writes.

The settings file lives under the user profile so the token used by the
browser integration survives across runs.  Writes go through a temp file
followed by an atomic replace so a crash mid-write never leaves a truncated
config on disk.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

from config.settings import AppConfig, DEFAULT_CONFIG
from core.paths import config_dir, migrate_legacy_config


def config_path() -> Path:
    return config_dir() / "config.json"


def _ensure_token(cfg: AppConfig) -> None:
    """Guarantee a live-server auth token always exists."""
    if not cfg.live_server_token:
        cfg.live_server_token = secrets.token_urlsafe(32)


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically.

    Uses a temporary sibling file + ``os.replace`` so concurrent readers (the
    live server thread, another CLI invocation) never observe a half-written
    file.  Permissions are restricted to the owner on POSIX.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # chmod is best-effort (fails on some Windows ACL setups).
            pass
    except BaseException:
        # Clean up the temp file on any failure to avoid litter.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration, falling back to defaults on any error.

    A damaged settings file must never leave the application without its
    authentication token on the next browser-integration run, so the token is
    always regenerated when missing.
    """
    migrate_legacy_config()
    cfg_path = path or config_path()
    if not cfg_path.exists():
        cfg = DEFAULT_CONFIG.copy()
        _ensure_token(cfg)
        save_config(cfg, cfg_path)
        return cfg

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
        cfg = AppConfig.from_dict(data)
        _ensure_token(cfg)
        return cfg
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        cfg = DEFAULT_CONFIG.copy()
        _ensure_token(cfg)
        return cfg


def save_config(config: AppConfig, path: Optional[Path] = None) -> None:
    """Persist configuration using an atomic write."""
    cfg_path = path or config_path()
    _ensure_token(config)
    data = json.dumps(config.to_dict(), indent=2)
    _atomic_write(cfg_path, data)
