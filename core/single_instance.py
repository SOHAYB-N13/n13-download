"""Single-instance guard and URL forwarding.

Enforcement
-----------
* **Windows** — a session-scoped named mutex (``Local\\N13DownloadManager``)
  acquired for the process lifetime.  A second launch fails to acquire it and
  exits gracefully instead of becoming a competing instance.
* **POSIX** — an advisory ``flock`` on a lock file (auto-released when the
  process dies, so a stale lock can never block a relaunch).

URL forwarding
--------------
When a second launch carries a download URL, it is forwarded to the running
instance's local relay (the authenticated LiveServer) via an HTTP POST, so the
running instance adds it to its queue rather than a second process downloading
the same file and racing on the same ``.part`` files.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.request import Request

log = logging.getLogger("n13")

_MUTEX_NAME = "Local\\N13DownloadManager"

_lock_handle = None   # Windows mutex handle (kept for the process lifetime)
_lock_file = None     # POSIX lock file handle


def acquire_single_instance(lock_dir: Optional[Path] = None) -> bool:
    """Become the single running instance if possible.

    Returns ``True`` when THIS process acquired the lock (the first instance);
    ``False`` when another instance already holds it.
    """
    global _lock_handle, _lock_file
    if sys.platform == "win32":
        _lock_handle = _acquire_windows_mutex()
        return _lock_handle is not None
    _lock_file = _acquire_posix_lock(lock_dir)
    return _lock_file is not None


def release_single_instance() -> None:
    """Release the lock (also released automatically at process exit)."""
    global _lock_handle, _lock_file
    if _lock_handle is not None and sys.platform == "win32":
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(_lock_handle)
        except Exception:
            pass
        _lock_handle = None
    if _lock_file is not None:
        try:
            _lock_file.close()
        except OSError:
            pass
        _lock_file = None


def _acquire_windows_mutex() -> Optional[int]:
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return None
    # ERROR_ALREADY_EXISTS (183) => another instance already created it.
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _acquire_posix_lock(lock_dir: Optional[Path] = None) -> Optional[object]:
    import fcntl
    lock_dir = lock_dir or Path(os.environ.get("TMPDIR", "/tmp"))
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / "n13-instance.lock"
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def forward_url(port: int, token: str, url: str) -> bool:
    """Forward *url* to the running instance's local relay.

    Returns ``True`` when the running instance accepted it into its queue.
    ``autostart`` tells the running instance to add it to the queue directly.
    """
    if not url or not token:
        return False
    endpoint = f"http://127.0.0.1:{int(port)}/download"
    body = json.dumps({"url": url, "autostart": True}).encode("utf-8")
    req = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-TDM-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False
