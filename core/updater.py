"""GitHub-Releases based updater for N13 — clean reinstall architecture.

The updater never patches the running installation.  The flow is:

    check release -> download Setup -> <install>\\Update\\Setup.exe
    -> copy Setup + a self-contained PowerShell updater to %TEMP%\\N13Updater\\
    -> launch the updater -> N13 exits
    -> updater waits for N13 to disappear
    -> runs unins000.exe (silent) -> waits for uninstall
    -> verifies the old install is gone -> deletes leftover install files
    -> deletes %LOCALAPPDATA%\\N13 (user data)
    -> verifies the downloaded Setup checksum
    -> installs the new Setup into the SAME install directory
    -> verifies N13.exe -> launches the new N13
    -> updater exits

The updater is a standalone PowerShell script with no dependency on Python or
on files inside the install directory, so uninstalling the old install cannot
break it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import uuid
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
UPDATER_DIR = "Update"

_CREATE_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    | getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
)


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

    ``N13_UPDATE_BASE_URL`` (environment variable) overrides the GitHub API
    base URL so the release JSON can be served locally during testing.
    """
    if not repo or "/" not in repo:
        return None
    base = os.environ.get("N13_UPDATE_BASE_URL", "").strip().rstrip("/")
    url = (base + "/releases/latest") if base else GITHUB_API_URL.format(repo=repo)
    try:
        data = json.loads(_github_request(url).decode("utf-8"))
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
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
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


# --------------------------------------------------------------------------- #
# Install-directory + staging paths
# --------------------------------------------------------------------------- #

def install_dir() -> Path:
    """Return the directory N13 is installed into (parent of N13.exe)."""
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "n13.exe":
        return exe.parent
    # Development / test fallback: treat the project root as the install dir.
    return Path(__file__).resolve().parent.parent


def update_dir(app_dir: Optional[Path] = None) -> Path:
    """Return the ``<install>\\Update`` directory used to stage downloads."""
    d = (app_dir or install_dir()) / UPDATER_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_update_path(version: str, app_dir: Optional[Path] = None) -> Path:
    """Return the staged installer path: ``<install>\\Update\\Setup.exe``."""
    return update_dir(app_dir) / INSTALLER_NAME


def _user_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base / "N13"


# --------------------------------------------------------------------------- #
# Independent updater
# --------------------------------------------------------------------------- #

def _ps_q(s: str) -> str:
    """Escape a value for a single-quoted PowerShell string literal."""
    return s.replace("'", "''")


_UPDATER_PS1 = r"""# N13 self-contained updater - uninstall + clean reinstall
$ErrorActionPreference = 'Continue'
$InstallDir    = '__INSTALL_DIR__'
$SetupPath     = '__SETUP_PATH__'
$ExpectedHash  = '__EXPECTED_HASH__'
$UserData      = '__USER_DATA__'
$LogFile       = '__LOG_FILE__'

function Log([string]$msg) {
    try {
        Add-Content -LiteralPath $LogFile -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -ErrorAction Stop
    } catch {}
}

Log 'STEP0 updater started'
if (-not (Test-Path -LiteralPath $InstallDir)) {
    Log ('STEP0 FATAL install dir missing: ' + $InstallDir)
    exit 1
}

# 1) Wait until N13 has really exited (poll the process list).
Log 'STEP1 waiting for N13 to exit'
$deadline = (Get-Date).AddMinutes(3)
while ($true) {
    $p = Get-Process -Name 'N13' -ErrorAction SilentlyContinue
    if (-not $p) { break }
    if ((Get-Date) -gt $deadline) {
        Log 'STEP1 TIMEOUT waiting for N13 to exit'
        exit 1
    }
    Start-Sleep -Milliseconds 800
}
Log 'STEP1 N13 exited'

# 2) Run the official Inno Setup uninstaller (never Remove-Item the app).
$unins = Join-Path $InstallDir 'unins000.exe'
if (-not (Test-Path -LiteralPath $unins)) {
    Log ('STEP2 FATAL uninstaller not found: ' + $unins)
    exit 1
}
Log 'STEP2 running uninstaller'
$p = Start-Process -FilePath $unins -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru
Log ('STEP2 uninstaller exit code=' + $p.ExitCode)

# 3) Wait until the old installation is really gone.
Log 'STEP3 verifying old installation removed'
$deadline = (Get-Date).AddMinutes(2)
while ($true) {
    $exe = Test-Path -LiteralPath (Join-Path $InstallDir 'N13.exe')
    $uni = Test-Path -LiteralPath (Join-Path $InstallDir 'unins000.exe')
    if (-not $exe -and -not $uni) { break }
    if ((Get-Date) -gt $deadline) {
        Log 'STEP3 TIMEOUT waiting for uninstall to finish'
        break
    }
    Start-Sleep -Milliseconds 800
}
Log 'STEP3 old installation removed'

# 4) Clean any leftover files in the install directory (install dir only).
if (Test-Path -LiteralPath $InstallDir) {
    Log 'STEP4 removing leftover install files'
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

# 5) Remove N13 user data (only %LOCALAPPDATA%\N13).
if ($UserData -and (Test-Path -LiteralPath $UserData)) {
    Log 'STEP5 removing user data'
    Remove-Item -LiteralPath $UserData -Recurse -Force -ErrorAction SilentlyContinue
}

# 6) Verify the new installer checksum before running it.
if (-not (Test-Path -LiteralPath $SetupPath)) {
    Log ('STEP6 FATAL setup not found: ' + $SetupPath)
    exit 1
}
try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs = [System.IO.File]::OpenRead($SetupPath)
    $bytes = $sha.ComputeHash($fs)
    $fs.Dispose()
    $sha.Dispose()
} catch {
    Log ('STEP6 FATAL could not hash setup: ' + $_)
    exit 1
}
$actual = ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLower()
if ($actual -ne $ExpectedHash) {
    Log ('STEP6 FATAL checksum mismatch actual=' + $actual + ' expected=' + $ExpectedHash)
    Remove-Item -LiteralPath $SetupPath -Force -ErrorAction SilentlyContinue
    exit 1
}
Log 'STEP6 checksum OK'

# 7) Install the new version into the SAME install directory.
$argLine = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=' + $InstallDir + '"'
Log ('STEP7 running new installer -> ' + $InstallDir)
$p = Start-Process -FilePath $SetupPath -ArgumentList $argLine -Wait -PassThru
Log ('STEP7 installer exit code=' + $p.ExitCode)
if ($p.ExitCode -ne 0) {
    Log 'STEP7 FATAL new install failed'
    exit 1
}

# 8) Verify the new executable exists.
$newExe = Join-Path $InstallDir 'N13.exe'
$deadline = (Get-Date).AddMinutes(2)
while (-not (Test-Path -LiteralPath $newExe)) {
    if ((Get-Date) -gt $deadline) {
        Log 'STEP8 FATAL N13.exe not found after install'
        exit 1
    }
    Start-Sleep -Milliseconds 800
}
$ver = (Get-ItemProperty -LiteralPath $newExe).VersionInfo.ProductVersion
Log ('STEP8 new N13.exe present version=' + $ver)

# 9) Launch the new N13.
Start-Process -FilePath $newExe
Log 'STEP9 launched new N13'
Log 'STEP10 updater done'
exit 0
"""


def render_updater_script(
    install_dir_: str,
    setup_path: str,
    expected_checksum: str,
    user_data: str,
    log_file: str,
) -> str:
    """Render the standalone PowerShell updater script (used for tests too)."""
    return (
        _UPDATER_PS1.replace("__INSTALL_DIR__", _ps_q(install_dir_))
        .replace("__SETUP_PATH__", _ps_q(setup_path))
        .replace("__EXPECTED_HASH__", (expected_checksum or "").lower())
        .replace("__USER_DATA__", _ps_q(user_data))
        .replace("__LOG_FILE__", _ps_q(log_file))
    )


def launch_updater(
    installer_path: Path,
    app_dir: Optional[Path] = None,
    expected_checksum: Optional[str] = None,
) -> bool:
    """Stage and launch the independent updater.

    The installer (already verified in Python at download time) is ensured to be
    inside ``<install>\\Update\\``, then both the installer and the updater script
    are copied to a unique ``%TEMP%\\N13Updater\\<id>\\`` folder so uninstalling
    the old install cannot remove them.
    """
    if not installer_path.exists():
        log.error("UPDATE: installer not found: %s", installer_path)
        return False

    app_dir = (app_dir or install_dir()).resolve()
    update_dir_path = update_dir(app_dir)

    staged = update_dir_path / INSTALLER_NAME
    if installer_path.resolve() != staged.resolve():
        try:
            shutil.copy2(installer_path, staged)
        except OSError as exc:
            log.error("UPDATE: failed to stage installer: %s", exc)
            return False
    installer_path = staged

    if not installer_path.exists():
        log.error("UPDATE: staged installer missing: %s", installer_path)
        return False
    if installer_path.stat().st_size == 0:
        log.error("UPDATE: staged installer is empty: %s", installer_path)
        return False

    work_root = Path(tempfile.gettempdir()) / "N13Updater"
    work_root.mkdir(parents=True, exist_ok=True)
    work = work_root / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)

    setup_temp = work / INSTALLER_NAME
    try:
        shutil.copy2(installer_path, setup_temp)
    except OSError as exc:
        log.error("UPDATE: failed to copy installer to temp: %s", exc)
        return False

    log_file = work / "updater.log"
    script = render_updater_script(
        install_dir_=str(app_dir),
        setup_path=str(setup_temp),
        expected_checksum=expected_checksum or "",
        user_data=str(_user_data_dir()),
        log_file=str(log_file),
    )
    script_path = work / "updater.ps1"
    script_path.write_text(script, encoding="utf-8")
    log.info("UPDATE: updater script = %s", script_path)
    log.info("UPDATE: install dir = %s", app_dir)

    try:
        proc = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", str(script_path),
            ],
            close_fds=True,
            creationflags=_CREATE_FLAGS,
        )
        log.info("UPDATE: updater launched pid=%s", proc.pid)
        return True
    except Exception as exc:
        log.error("UPDATE: failed to launch updater: %s", exc)
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

        if not verify_installer(dest, self._expected_checksum):
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            self._download_path = None
            self._expected_checksum = None
            self._set("error", error="Update verification failed.")
            return

        self._set("ready", info=info)

    def install(self, app_dir: Optional[Path] = None) -> bool:
        """Stage and launch the independent updater (uninstall + reinstall).

        The caller is responsible for safe shutdown of the application after
        this returns; the updater is launched detached so it survives the
        current process exit.
        """
        with self._lock:
            if self._state != "ready" or not self._download_path:
                return False
            path = self._download_path
            checksum = self._expected_checksum
        if not launch_updater(path, app_dir=app_dir, expected_checksum=checksum):
            return False
        return True
