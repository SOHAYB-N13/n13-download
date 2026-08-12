"""Lightweight GitHub-Releases based updater for N13.

The updater is intentionally small and isolated from the UI:

* checks the configured GitHub repository for the latest stable release
* compares semantic versions
* downloads the official installer to a temporary location
* verifies the SHA-256 checksum before executing anything
* hands off to the existing Inno Setup installer and safe-shutdown path

All network failures are swallowed and returned as user-friendly errors so
that an unreachable GitHub can never prevent N13 from working.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.error import URLError

from core.version import VERSION, compare_versions

log = logging.getLogger("n13")

INSTALLER_NAME = "N13-Download-Manager-Setup.exe"
CHECKSUM_NAME = INSTALLER_NAME + ".sha256.txt"
GITHUB_API_URL = "https://api.github.com/repos/{repo}/releases/latest"
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120


def get_current_version() -> str:
    """Return the installed application version."""
    return VERSION


def is_newer(remote: str, current: str = VERSION) -> bool:
    """Return True when *remote* is a newer semantic version than *current*."""
    return compare_versions(remote, current) > 0


def _github_request(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"N13-Updater/{VERSION}",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read()


def fetch_latest_release(repo: str) -> Optional[Dict]:
    """Query the latest stable GitHub release for *repo*.

    Returns a dict with ``version``, ``tag``, ``notes``, ``installer_url``,
    ``checksum_url`` and ``published_at`` on success, or ``None`` on any
    failure (network, parse, missing asset, etc.).
    """
    if not repo or "/" not in repo:
        return None
    try:
        data = json.loads(_github_request(GITHUB_API_URL.format(repo=repo)).decode("utf-8"))
        if data.get("prerelease") or data.get("draft"):
            return None
        tag = (data.get("tag_name") or "").strip()
        version = re.sub(r"^v", "", tag, flags=re.IGNORECASE)
        assets = {a.get("name", ""): a.get("browser_download_url", "") for a in data.get("assets", [])}
        installer_url = assets.get(INSTALLER_NAME)
        if not installer_url:
            return None
        checksum_url = assets.get(CHECKSUM_NAME)
        return {
            "version": version,
            "tag": tag,
            "notes": data.get("body", "") or "",
            "installer_url": installer_url,
            "checksum_url": checksum_url,
            "published_at": data.get("published_at", "") or "",
        }
    except Exception as exc:
        log.warning("Update check failed for %s: %s", repo, exc)
        return None


def _parse_checksum(text: str) -> Optional[str]:
    """Extract the first 64-character hex string from a checksum file."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0].lower()
        if re.match(r"^[0-9a-f]{64}$", token):
            return token
    return None


def fetch_checksum(checksum_url: str) -> Optional[str]:
    """Download and parse the SHA-256 checksum file."""
    if not checksum_url:
        return None
    try:
        return _parse_checksum(_github_request(checksum_url).decode("utf-8"))
    except Exception as exc:
        log.warning("Checksum fetch failed: %s", exc)
        return None


def _download(url: str, dest: Path, progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"N13-Updater/{VERSION}"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        written = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if progress_cb and total:
                    progress_cb(written, total)


def download_installer(url: str, dest: Path, progress_cb: Optional[Callable[[int, int], None]] = None) -> bool:
    """Download the installer to *dest*.

    Returns ``True`` on success. Any failure is logged and returns ``False``.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _download(url, dest, progress_cb)
        return True
    except Exception as exc:
        log.error("Installer download failed: %s", exc)
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_installer(path: Path, expected_checksum: Optional[str]) -> bool:
    """Verify *path* against *expected_checksum*.

    If no checksum is provided, returns ``False`` (a release without a
    checksum cannot be trusted).
    """
    if not expected_checksum:
        return False
    return hmac.compare_digest(sha256_file(path).lower(), expected_checksum.lower())



def _default_update_dir() -> Path:
    r"""Return a per-user temporary directory for update artifacts.

    This is intentionally outside the installation directory and outside
    ``%LOCALAPPDATA%\N13`` data folders.
    """
    d = Path(tempfile.gettempdir()) / "N13Updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_update_path(version: str) -> Path:
    """Return a unique temporary path for the installer of *version*."""
    return _default_update_dir() / f"N13-Download-Manager-Setup-{version}.exe"


def cleanup_old_updates(keep: Optional[Path] = None) -> None:
    """Delete update installers except the optionally-kept current one."""
    try:
        for f in _default_update_dir().glob("N13-Download-Manager-Setup-*.exe"):
            if keep and f.resolve() == keep.resolve():
                continue
            try:
                f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _helper_log_path() -> Path:
    return _default_update_dir() / "update_helper.log"


def launch_installer(installer_path: Path, app_dir: Optional[Path] = None, restart: bool = True) -> bool:
    """Launch the verified installer from an independent helper process.

    A PowerShell helper is written to disk and started with breakaway flags so
    it survives the N13 process exit. The helper waits for N13 to exit, runs
    the Inno Setup installer, verifies the installed executable, and restarts
    N13.
    """
    if not installer_path.exists():
        log.error("UPDATE: installer not found: %s", installer_path)
        return False
    if not installer_path.is_absolute():
        log.error("UPDATE: installer path is not absolute: %s", installer_path)
        return False
    if installer_path.stat().st_size == 0:
        log.error("UPDATE: installer file is empty: %s", installer_path)
        return False

    current_pid = os.getpid()
    log.info("UPDATE: installer path = %s", installer_path)
    log.info("UPDATE: current N13 PID = %d", current_pid)

    if not restart or not app_dir:
        # Fallback: just launch the installer detached, do not restart.
        try:
            subprocess.Popen(
                [str(installer_path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                close_fds=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                    | getattr(__import__("subprocess"), "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
                ),
            )
            return True
        except Exception as exc:
            log.error("UPDATE: failed to launch installer: %s", exc)
            return False

    n13_exe = app_dir / "N13.exe"
    helper_dir = _default_update_dir()
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper_script = helper_dir / "n13_update_helper.ps1"
    helper_log = _helper_log_path()

    # Use a PowerShell script file so quoting/lifetime is easier to control
    # than an inline -Command string.
    script = f"""# N13 independent update helper
$ErrorActionPreference = "Stop"
$log = "{helper_log}"
function Write-Log($msg) {{
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $log -Value $line -ErrorAction SilentlyContinue
}}

try {{
    Write-Log "UPDATE: helper started, PID=$PID"
    Write-Log "UPDATE: waiting for N13 to exit (PID {current_pid})"
    try {{
        $n13 = Get-Process -Id {current_pid} -ErrorAction Stop
        $n13.WaitForExit()
        Write-Log "UPDATE: N13 exited"
    }} catch {{
        Write-Log "UPDATE: N13 already gone or error: $_"
    }}

    $installer = "{installer_path}"
    if (-not (Test-Path $installer)) {{
        Write-Log "UPDATE: installer not found: $installer"
        exit 1
    }}
    $size = (Get-Item $installer).Length
    Write-Log "UPDATE: installer size = $size"

    Write-Log "UPDATE: launching installer = $installer"
    $proc = Start-Process -FilePath $installer -ArgumentList "/SILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait -PassThru
    $exit = $proc.ExitCode
    Write-Log "UPDATE: installer exit code = $exit"
    if ($exit -ne 0) {{
        Write-Log "UPDATE: installer reported failure"
        exit $exit
    }}

    Start-Sleep -Seconds 2
    $n13path = "{n13_exe}"
    if (-not (Test-Path $n13path)) {{
        Write-Log "UPDATE: installed executable not found: $n13path"
        exit 1
    }}
    $ver = (Get-ItemProperty $n13path).VersionInfo.ProductVersion
    Write-Log "UPDATE: installed executable = $n13path"
    Write-Log "UPDATE: installed version = $ver"

    Write-Log "UPDATE: restarting N13 = $n13path"
    Start-Process -FilePath $n13path
    Write-Log "UPDATE: restart launched"
}} catch {{
    Write-Log "UPDATE: helper fatal error: $_"
    exit 1
}}
"""
    helper_script.write_text(script, encoding="utf-8")
    log.info("UPDATE: helper script = %s", helper_script)

    try:
        proc = subprocess.Popen(
            [
                "powershell.exe",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", str(helper_script),
            ],
            close_fds=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | getattr(__import__("subprocess"), "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
            ),
        )
        log.info("UPDATE: helper PID = %s", proc.pid)
        return True
    except Exception as exc:
        log.error("UPDATE: failed to launch helper: %s", exc)
        return False


class UpdateController:
    """UI-facing updater state machine.

    Keeps the current check/download state and runs network work on a
    background thread so the pywebview API never blocks.
    """

    def __init__(self, config):
        self._config = config
        self._lock = threading.Lock()
        self._state = "idle"  # idle | checking | available | downloading | ready | error
        self._progress = 0
        self._info: Optional[Dict] = None
        self._error: Optional[str] = None
        self._download_path: Optional[Path] = None
        self._expected_checksum: Optional[str] = None
        self._listeners: list[Callable] = []

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def subscribe(self, callback: Callable) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        snapshot = self.get_state()
        for cb in self._listeners:
            try:
                cb(snapshot)
            except Exception:
                pass

    def _set(self, state: str, progress: Optional[int] = None, info: Optional[Dict] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._state = state
            if progress is not None:
                self._progress = progress
            if info is not None:
                self._info = info
            if error is not None:
                self._error = error
        self._notify()

    def get_state(self) -> Dict:
        with self._lock:
            return {
                "state": self._state,
                "progress": self._progress,
                "info": self._info,
                "error": self._error,
                "current_version": get_current_version(),
                "download_path": str(self._download_path) if self._download_path else None,
            }

    def check(self) -> None:
        """Start a background check. Idempotent while already checking."""
        with self._lock:
            if self._state == "checking":
                return
            self._state = "checking"
            self._error = None
            self._progress = 0
        self._notify()
        threading.Thread(target=self._do_check, daemon=True).start()

    def _do_check(self) -> None:
        repo = getattr(self._config, "update_repo", None)
        info = fetch_latest_release(repo)
        if info is None:
            self._set("error", error="Unable to connect to the update server.")
            return
        latest = info.get("version", "")
        if not latest or not is_newer(latest, get_current_version()):
            self._set("idle", info={"latest": latest, **info})
            return
        self._set("available", info=info)

    def download(self) -> None:
        """Download the available installer in the background."""
        with self._lock:
            if self._state != "available" or not self._info:
                return
            info = dict(self._info)
            self._state = "downloading"
            self._progress = 0
            self._error = None
        self._notify()
        threading.Thread(target=self._do_download, args=(info,), daemon=True).start()

    def _do_download(self, info: Dict) -> None:
        version = info.get("version", "unknown")
        url = info.get("installer_url", "")
        checksum_url = info.get("checksum_url")
        if not url:
            self._set("error", error="Installer URL missing.")
            return

        dest = make_update_path(version)
        cleanup_old_updates(keep=None)
        self._download_path = dest
        self._expected_checksum = fetch_checksum(checksum_url) if checksum_url else None

        def progress(written: int, total: int) -> None:
            pct = int(written * 100 / total) if total else 0
            self._set("downloading", progress=pct)

        if not download_installer(url, dest, progress):
            self._download_path = None
            self._expected_checksum = None
            self._set("error", error="Installer download failed.")
            return

        if not self._expected_checksum:
            self._download_path = None
            self._set("error", error="Release checksum missing.")
            return

        if not hmac.compare_digest(sha256_file(dest).lower(), self._expected_checksum.lower()):
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            self._download_path = None
            self._expected_checksum = None
            self._set("error", error="Update verification failed.")
            return

        self._set("ready", info=info)

    def install(self, app_dir: Optional[Path] = None, restart: bool = True) -> bool:
        """Launch the verified installer.

        The caller is responsible for safe shutdown of the application after
        this returns; the installer is launched detached so it survives the
        current process exit.
        """
        with self._lock:
            if self._state != "ready" or not self._download_path:
                return False
            path = self._download_path
        if not launch_installer(path, app_dir=app_dir, restart=restart):
            return False
        cleanup_old_updates(keep=path)
        return True
