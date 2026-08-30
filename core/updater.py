"""Auto-update system for N13 — GitHub Releases + deterministic Windows updater.

The running N13 process NEVER installs anything itself and NEVER modifies the
installation directory.  The full flow is:

    check latest release
    -> download N13-Download-Manager-Setup.exe into %TEMP%\\N13-Updater\\<id>\\
    -> verify SHA-256 against the official release checksum
    -> write the independent PowerShell updater (update.ps1) into the staging dir
    -> launch powershell.exe (detached) with every required parameter
    -> N13 performs its normal safe shutdown and exits completely
    -> PowerShell waits for N13 to exit
    -> PowerShell runs the real uninstaller (<InstallDir>\\unins000.exe)
    -> PowerShell verifies the old installation is gone (and cleans leftovers)
    -> PowerShell deletes %LOCALAPPDATA%\\N13
    -> PowerShell runs the NEW installer into the EXACT same InstallDir
    -> PowerShell verifies the new N13.exe exists and its version
    -> PowerShell launches the NEW installed N13.exe
    -> PowerShell cleans its temporary files and exits

The PowerShell updater (``update.ps1``, embedded in this module) is the ONLY
component that touches the installer after shutdown.  It is fully independent
of N13: no Python runtime, no WebView, no UI event loop, no N13 threads, and it
executes entirely from %TEMP% so the uninstaller is free to remove the install
directory.

User data under ``%LOCALAPPDATA%\\N13`` is intentionally removed by this update
flow (it is regenerated on the next launch).
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
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from core.version import VERSION, compare_versions, parse_version

log = logging.getLogger("n13")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_REPO = "SOHAYB-N13/n13-download"
GITHUB_API_URL = "https://api.github.com/repos/{repo}/releases/latest"
INSTALLER_NAME = "N13-Download-Manager-Setup.exe"
CHECKSUM_NAME = INSTALLER_NAME + ".sha256.txt"
PS1_NAME = "update.ps1"
UPDATER_START_MARKER = "update.started"
POWERSHELL = "powershell.exe"
TEMP_ROOT_NAME = "N13-Updater"

REQUEST_TIMEOUT = 15          # seconds — API / metadata requests
DOWNLOAD_READ_TIMEOUT = 60    # seconds — per-read timeout while downloading
UPDATER_START_TIMEOUT = 20.0  # seconds — wait for the updater to confirm start

# Environment overrides (used by tests / support diagnostics only):
#   N13_UPDATE_API_URL      — full URL replacing the releases/latest endpoint
#                             (file:// URLs are accepted for local fixtures)
#   N13_UPDATE_INSTALL_DIR  — overrides the detected installation directory


class UpdateState:
    """Canonical updater states (serialised to the UI as strings)."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY_TO_INSTALL = "ready_to_install"
    INSTALLING = "installing"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed transitions — keeps the model small and prevents impossible jumps.
_TRANSITIONS: Dict[str, frozenset] = {
    UpdateState.IDLE: frozenset({UpdateState.CHECKING}),
    UpdateState.CHECKING: frozenset({
        UpdateState.UP_TO_DATE, UpdateState.AVAILABLE, UpdateState.FAILED, UpdateState.IDLE,
    }),
    UpdateState.UP_TO_DATE: frozenset({UpdateState.CHECKING, UpdateState.IDLE}),
    UpdateState.AVAILABLE: frozenset({
        UpdateState.DOWNLOADING, UpdateState.CHECKING, UpdateState.IDLE,
    }),
    UpdateState.DOWNLOADING: frozenset({
        UpdateState.DOWNLOADING,  # progress ticks (same-state updates)
        UpdateState.VERIFYING, UpdateState.FAILED, UpdateState.CANCELLED,
    }),
    UpdateState.VERIFYING: frozenset({UpdateState.READY_TO_INSTALL, UpdateState.FAILED}),
    UpdateState.READY_TO_INSTALL: frozenset({
        UpdateState.INSTALLING, UpdateState.AVAILABLE, UpdateState.CHECKING, UpdateState.IDLE,
    }),
    UpdateState.INSTALLING: frozenset({UpdateState.FAILED, UpdateState.IDLE}),
    UpdateState.FAILED: frozenset({
        UpdateState.CHECKING, UpdateState.DOWNLOADING, UpdateState.AVAILABLE, UpdateState.IDLE,
    }),
    UpdateState.CANCELLED: frozenset({
        UpdateState.DOWNLOADING, UpdateState.CHECKING, UpdateState.AVAILABLE, UpdateState.IDLE,
    }),
}


class UpdateError:
    """Stable error codes surfaced to the UI (never raw tracebacks)."""

    NETWORK = "network"              # cannot reach GitHub / timeout
    RATE_LIMITED = "rate_limited"    # GitHub API rate limit hit
    NO_RELEASE = "no_release"        # no suitable stable release found
    NO_INSTALLER = "no_installer"    # release has no installer asset
    NO_CHECKSUM = "no_checksum"      # release has no checksum asset
    CHECKSUM_MISMATCH = "checksum_mismatch"
    DOWNLOAD_FAILED = "download_failed"
    UPDATER_FAILED = "updater_failed"  # PowerShell updater could not be started
    NOT_READY = "not_ready"
    DEV_MODE = "dev_mode"            # install requested from a source checkout


# --------------------------------------------------------------------------- #
# Release metadata
# --------------------------------------------------------------------------- #

@dataclass
class ReleaseInfo:
    """The relevant parts of a GitHub release."""

    version: str
    tag: str
    name: str = ""
    notes: str = ""
    published_at: str = ""
    html_url: str = ""
    installer_url: str = ""
    installer_size: int = 0
    checksum_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "tag": self.tag,
            "name": self.name,
            "notes": self.notes,
            "published_at": self.published_at,
            "html_url": self.html_url,
            "installer_url": self.installer_url,
            "installer_size": self.installer_size,
            "has_checksum": bool(self.checksum_url),
        }


def get_current_version() -> str:
    """Return the installed application version (authoritative source)."""
    return VERSION


def is_newer(remote: str, current: str = VERSION) -> bool:
    """True when *remote* is a newer semantic version than *current*."""
    return compare_versions(remote, current) > 0


def _request(url: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[bytes, Dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"N13-Updater/{VERSION}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), dict(resp.headers.items())


def _api_url(repo: str) -> str:
    override = os.environ.get("N13_UPDATE_API_URL", "").strip()
    if override:
        return override
    return GITHUB_API_URL.format(repo=repo)


def _pick_installer_asset(assets: Dict[str, str]) -> str:
    """Locate the installer download URL among release assets, dynamically."""
    if INSTALLER_NAME in assets:
        return assets[INSTALLER_NAME]
    exes = [name for name in assets if name.lower().endswith(".exe")]
    setup_like = [n for n in exes if "setup" in n.lower() or "installer" in n.lower()]
    if len(setup_like) == 1:
        return assets[setup_like[0]]
    if len(exes) == 1:
        return assets[exes[0]]
    return ""


def _pick_checksum_asset(assets: Dict[str, str], installer_name: str) -> str:
    """Locate the SHA-256 checksum asset for the installer, if published."""
    if CHECKSUM_NAME in assets:
        return assets[CHECKSUM_NAME]
    base = installer_name.rsplit(".", 1)[0].lower()
    for name, url in assets.items():
        low = name.lower()
        if low.endswith(".sha256.txt") or low.endswith(".sha256"):
            if base in low:
                return url
    return ""


def fetch_latest_release(repo: str) -> Tuple[Optional[ReleaseInfo], Optional[str]]:
    """Query the latest stable GitHub release for *repo*.

    Returns ``(ReleaseInfo, None)`` on success or ``(None, error_code)`` on
    failure.  Drafts and pre-releases are ignored.  Never raises.
    """
    repo = (repo or "").strip() or DEFAULT_REPO
    if "/" not in repo:
        return None, UpdateError.NO_RELEASE
    url = _api_url(repo)
    log.info("UPDATE: check started (repo=%s)", repo)
    try:
        raw, _headers = _request(url)
    except urllib.error.HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        if exc.code in (403, 429) and (remaining == "0" or exc.code == 429):
            log.warning("UPDATE: GitHub rate limit hit (HTTP %s)", exc.code)
            return None, UpdateError.RATE_LIMITED
        log.warning("UPDATE: release query failed (HTTP %s)", exc.code)
        return None, UpdateError.NETWORK
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("UPDATE: release query failed: %s", exc)
        return None, UpdateError.NETWORK

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.warning("UPDATE: could not parse release metadata: %s", exc)
        return None, UpdateError.NO_RELEASE

    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None, UpdateError.NO_RELEASE

    tag = str(data.get("tag_name") or "").strip()
    version = re.sub(r"^v", "", tag, flags=re.IGNORECASE)
    if parse_version(version) is None:
        log.warning("UPDATE: release tag %r is not a semantic version", tag)
        return None, UpdateError.NO_RELEASE

    assets = {
        str(a.get("name", "")): str(a.get("browser_download_url", ""))
        for a in data.get("assets", [])
        if isinstance(a, dict) and a.get("browser_download_url")
    }
    sizes = {
        str(a.get("name", "")): int(a.get("size", 0) or 0)
        for a in data.get("assets", [])
        if isinstance(a, dict)
    }

    installer_url = _pick_installer_asset(assets)
    if not installer_url:
        log.warning("UPDATE: release %s has no installer asset", tag)
        return None, UpdateError.NO_INSTALLER
    installer_name = INSTALLER_NAME if INSTALLER_NAME in assets else next(
        (n for n, u in assets.items() if u == installer_url), INSTALLER_NAME
    )
    checksum_url = _pick_checksum_asset(assets, installer_name)

    info = ReleaseInfo(
        version=version,
        tag=tag,
        name=str(data.get("name") or ""),
        notes=str(data.get("body") or ""),
        published_at=str(data.get("published_at") or ""),
        html_url=str(data.get("html_url") or ""),
        installer_url=installer_url,
        installer_size=sizes.get(installer_name, 0),
        checksum_url=checksum_url,
    )
    log.info(
        "UPDATE: latest release=%s current=%s checksum_asset=%s",
        version, VERSION, "yes" if checksum_url else "no",
    )
    return info, None


# --------------------------------------------------------------------------- #
# Checksum + download primitives
# --------------------------------------------------------------------------- #

def parse_checksum(text: str) -> Optional[str]:
    """Extract the first 64-character hex token from checksum file content."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0].lower()
        if re.fullmatch(r"[0-9a-f]{64}", token):
            return token
    return None


def fetch_checksum(checksum_url: str) -> Optional[str]:
    """Download and parse the official SHA-256 checksum for the installer."""
    if not checksum_url:
        return None
    try:
        raw, _ = _request(checksum_url)
    except Exception as exc:
        log.warning("UPDATE: checksum download failed: %s", exc)
        return None
    try:
        return parse_checksum(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_installer(path: Path, expected_checksum: Optional[str]) -> bool:
    """Verify *path* against the official checksum.

    A missing/empty expected checksum always fails — an unverified installer
    must never be executed.
    """
    if not expected_checksum:
        return False
    actual = sha256_file(path)
    ok = hmac.compare_digest(actual.lower(), expected_checksum.lower())
    log.info("UPDATE: checksum verification %s (sha256=%s)", "OK" if ok else "MISMATCH", actual)
    return ok


class DownloadCancelled(Exception):
    """Raised inside the download loop when the user cancels."""


def download_file(
    url: str,
    dest: Path,
    progress_cb: Optional[Callable[[int, int, float, float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Stream *url* to *dest* with progress + cancellation support.

    ``progress_cb(written, total, speed_bps, eta_seconds)`` is invoked
    periodically.  Raises :class:`DownloadCancelled` when *cancel_event* is
    set; propagates network errors to the caller.
    """
    req = urllib.request.Request(url, headers={"User-Agent": f"N13-Updater/{VERSION}"})
    written = 0
    started = time.monotonic()
    last_emit = 0.0
    with urllib.request.urlopen(req, timeout=DOWNLOAD_READ_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        with open(dest, "wb") as f:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled()
                chunk = resp.read(128 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if progress_cb and (now - last_emit >= 0.25 or written == total):
                    last_emit = now
                    elapsed = max(now - started, 1e-6)
                    speed = written / elapsed
                    eta = (total - written) / speed if (total and speed > 0) else 0.0
                    progress_cb(written, total, speed, eta)
    if total and written != total:
        raise IOError(f"incomplete download ({written}/{total} bytes)")


# --------------------------------------------------------------------------- #
# Paths — staging + install dir
# --------------------------------------------------------------------------- #

def temp_root() -> Path:
    d = Path(tempfile.gettempdir()) / TEMP_ROOT_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_staging_dir(version: str) -> Path:
    """Dedicated per-update staging dir: ``%TEMP%\\N13-Updater\\<ver>-<id>\\``."""
    safe = re.sub(r"[^0-9A-Za-z.\-]", "_", version or "unknown")
    d = temp_root() / f"{safe}-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def installed_exe_path() -> Optional[Path]:
    """The real path of the running installed N13.exe (None in a source tree)."""
    override = os.environ.get("N13_UPDATE_INSTALL_DIR", "").strip()
    if override:
        return Path(override) / "N13.exe"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def install_dir() -> Optional[Path]:
    """The real installation directory (parent of the installed N13.exe).

    Returns ``None`` when running from a source checkout (no installation to
    update), unless ``N13_UPDATE_INSTALL_DIR`` is set (support/testing).
    """
    override = os.environ.get("N13_UPDATE_INSTALL_DIR", "").strip()
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def uninstaller_path(install_dir: Path) -> Path:
    """The Inno Setup uninstaller inside the installation directory."""
    return Path(install_dir) / "unins000.exe"


# --------------------------------------------------------------------------- #
# The independent PowerShell updater (the ONLY post-shutdown updater)
# --------------------------------------------------------------------------- #

POWERSHELL_UPDATER = r"""# N13 deterministic Windows updater.
#
# Runs fully independently of the N13 process: no Python runtime, no WebView,
# no UI event loop, no N13 threads.  Executes entirely from %TEMP% so the
# uninstaller is free to remove the installation directory.
#
# Flow: wait for N13 exit -> run the real uninstaller -> verify old install is
# gone -> delete %LOCALAPPDATA%\N13 -> run the NEW installer into the SAME
# directory -> verify the new N13.exe version -> relaunch -> clean up.
#
# Every stage is logged to update.log next to this script.  Any failure aborts
# immediately with a stage name and exit code; nothing is ever faked.
[CmdletBinding()]
param(
    [int]$N13Pid = 0,
    [string]$ExePath = "",
    [string]$InstallDir = "",
    [string]$Uninstaller = "",
    [string]$Installer = "",
    [string]$ExpectedVersion = "",
    [string]$ExpectedSha256 = ""
)

$ErrorActionPreference = "Stop"
$WorkDir = $PSScriptRoot
$LogFile = Join-Path $WorkDir "update.log"
$AppExe = "N13.exe"
$LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
$UserDataDir = Join-Path $LocalAppData "N13"
$N13ExitTimeout = 300
$UninstallTimeout = 600
$InstallTimeout = 900

function Write-Log([string]$Msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $env:COMPUTERNAME, $Msg
    try { Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Exit-Fail([int]$Code, [string]$Stage, [string]$Msg) {
    Write-Log "[$Stage] FAILED: $Msg (code=$Code)"
    exit $Code
}

function Test-ProcessAlive([int]$Id) {
    if ($Id -le 0) { return $false }
    $p = Get-Process -Id $Id -ErrorAction SilentlyContinue
    return ($null -ne $p)
}

function Wait-ForProcessExit([int]$Id, [int]$TimeoutSec) {
    # Poll until the exact PID is gone.  Never kills the process.
    if ($Id -le 0) { return $true }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-ProcessAlive $Id)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (-not (Test-ProcessAlive $Id))
}

function Wait-ForFileUnlocked([string]$Path, [int]$TimeoutSec) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $fs = [System.IO.File]::Open($Path, 'Open', 'ReadWrite', 'None')
            $fs.Close()
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Get-Sha256([string]$Path) {
    # .NET-based SHA-256 — deliberately avoids Get-FileHash, which is broken
    # on some machines because PSModulePath puts PowerShell 7 modules before
    # the Windows PowerShell 5.1 modules (Get-FileHash then fails to load).
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $bytes = $sha.ComputeHash($stream)
        $sb = New-Object System.Text.StringBuilder 64
        foreach ($b in $bytes) { [void]$sb.Append($b.ToString("x2")) }
        return $sb.ToString()
    } finally {
        if ($stream) { $stream.Dispose() }
        $sha.Dispose()
    }
}

function Get-VersionTuple([string]$V) {
    $parts = @()
    foreach ($p in ($V -replace '^v', '').Split('.')) {
        $n = 0
        if ([int]::TryParse($p, [ref]$n)) { $parts += $n } else { $parts += 0 }
    }
    while ($parts.Count -lt 4) { $parts += 0 }
    return , $parts
}

function Compare-VersionLt([string]$A, [string]$B) {
    $ta = Get-VersionTuple $A
    $tb = Get-VersionTuple $B
    for ($i = 0; $i -lt 4; $i++) {
        if ($ta[$i] -lt $tb[$i]) { return $true }
        if ($ta[$i] -gt $tb[$i]) { return $false }
    }
    return $false
}

function Get-ProductVersion([string]$Path) {
    try {
        $vi = (Get-Item -LiteralPath $Path).VersionInfo
        if ($vi -and $vi.ProductVersion) { return [string]$vi.ProductVersion }
    } catch {}
    return ""
}

# --- argument sanity -----------------------------------------------------
if (-not $InstallDir) { Exit-Fail 10 "UNINSTALL" "InstallDir not provided" }
if (-not (Test-Path -LiteralPath $InstallDir)) { Exit-Fail 11 "UNINSTALL" "InstallDir does not exist: $InstallDir" }
$AppExePath = Join-Path $InstallDir $AppExe
if (-not (Test-Path -LiteralPath $Uninstaller)) { Exit-Fail 12 "UNINSTALL" "Uninstaller not found: $Uninstaller" }
if (-not (Test-Path -LiteralPath $Installer)) { Exit-Fail 13 "VERIFY" "Installer not found: $Installer" }

# --- startup marker so N13 knows the updater is alive and will take over ----
try { Set-Content -LiteralPath (Join-Path $WorkDir "update.started") -Value "ok" -Encoding ASCII } catch {}

Write-Log "UPDATER_START workdir=$WorkDir"
Write-Log "UPDATER_START pid=$N13Pid"
Write-Log "UPDATER_START exe=$ExePath"
Write-Log "UPDATER_START installDir=$InstallDir"
Write-Log "UPDATER_START uninstaller=$Uninstaller"
Write-Log "UPDATER_START installer=$Installer"
Write-Log "UPDATER_START expectedVersion=$ExpectedVersion"

# --- verify installer checksum (mandatory; never run unverified) ----------
if ($ExpectedSha256) {
    $actual = Get-Sha256 $Installer
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        Exit-Fail 14 "VERIFY" "Checksum mismatch: actual=$actual expected=$ExpectedSha256"
    }
    Write-Log "VERIFY checksum OK ($actual)"
} else {
    Exit-Fail 15 "VERIFY" "No checksum metadata available for this release"
}

# --- wait for N13 to exit --------------------------------------------------
Write-Log "N13_EXIT waiting for pid=$N13Pid (timeout ${N13ExitTimeout}s)"
if (-not (Wait-ForProcessExit $N13Pid $N13ExitTimeout)) {
    Exit-Fail 20 "N13_EXIT" "N13 (pid $N13Pid) did not exit within ${N13ExitTimeout}s"
}
Write-Log "N13_EXIT ok"

if (-not (Wait-ForFileUnlocked $AppExePath 120)) {
    Exit-Fail 21 "N13_EXIT" "N13.exe remained locked after process exit"
}

# --- run the real uninstaller ----------------------------------------------
Write-Log "UNINSTALL launching $Uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
try {
    $null = Start-Process -FilePath $Uninstaller -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -PassThru
} catch {
    Exit-Fail 22 "UNINSTALL" "Could not start uninstaller: $($_.Exception.Message)"
}
# Inno Setup uninstallers relaunch from a temp copy, so the started stub's
# exit code is meaningless.  Completion is detected by the unins000 process
# disappearing AND the old application files being gone.
$unDone = $false
$unDeadline = (Get-Date).AddSeconds($UninstallTimeout)
while ((Get-Date) -lt $unDeadline) {
    $still = Get-Process -Name "unins000" -ErrorAction SilentlyContinue
    $appGone = (-not (Test-Path -LiteralPath $AppExePath)) -and (-not (Test-Path -LiteralPath $Uninstaller))
    if ((-not $still) -and $appGone) { $unDone = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $unDone) {
    Exit-Fail 23 "UNINSTALL" "Uninstaller did not complete within ${UninstallTimeout}s"
}
Write-Log "UNINSTALL ok"

# --- verify the old installation is really gone ----------------------------
$residual = @()
if (Test-Path -LiteralPath $InstallDir) {
    $residual = @(Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue)
}
if ($residual.Count -gt 0) {
    Write-Log "UNINSTALL_VERIFY residual items: $($residual.Count) - cleaning the exact captured InstallDir"
    $cleaned = $false
    $cleanDeadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $cleanDeadline) {
        try {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop
            $cleaned = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $cleaned) {
        Exit-Fail 24 "UNINSTALL_VERIFY" "Could not remove residual old application files in $InstallDir"
    }
    Write-Log "UNINSTALL_VERIFY residual old files removed"
}
if (Test-Path -LiteralPath $AppExePath) {
    Exit-Fail 25 "UNINSTALL_VERIFY" "Old N13.exe still exists: $AppExePath"
}
Write-Log "UNINSTALL_VERIFY ok (old installation removed)"

# --- delete user data (intentional for this update flow) --------------------
Write-Log "USER_DATA_DELETE removing $UserDataDir"
if (Test-Path -LiteralPath $UserDataDir) {
    try {
        Remove-Item -LiteralPath $UserDataDir -Recurse -Force -ErrorAction Stop
    } catch {
        Exit-Fail 30 "USER_DATA_DELETE" "Could not remove $UserDataDir : $($_.Exception.Message)"
    }
}
if (Test-Path -LiteralPath $UserDataDir) {
    Exit-Fail 31 "USER_DATA_DELETE" "$UserDataDir still exists after removal attempt"
}
Write-Log "USER_DATA_DELETE ok"

# --- run the NEW installer into the SAME installation directory -------------
Write-Log "INSTALL launching $Installer /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR=`"$InstallDir`""
$iproc = $null
try {
    $iproc = Start-Process -FilePath $Installer -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/DIR=`"$InstallDir`"") -Wait -PassThru
} catch {
    Exit-Fail 40 "INSTALL" "Could not start installer: $($_.Exception.Message)"
}
Write-Log "INSTALL installer exit code $($iproc.ExitCode)"
if ($iproc.ExitCode -ne 0) {
    Exit-Fail 41 "INSTALL" "Installer failed with exit code $($iproc.ExitCode)"
}

# --- verify the new installation --------------------------------------------
if (-not (Test-Path -LiteralPath $AppExePath)) {
    Exit-Fail 42 "INSTALL_VERIFY" "N13.exe not found after install: $AppExePath"
}
$installed = Get-ProductVersion $AppExePath
Write-Log "INSTALL_VERIFY installedVersion=$installed expected=$ExpectedVersion"
if ($ExpectedVersion -and (Compare-VersionLt $installed $ExpectedVersion)) {
    Exit-Fail 43 "INSTALL_VERIFY" "Installed version $installed is older than expected $ExpectedVersion"
}
Write-Log "INSTALL_VERIFY ok (version $installed >= $ExpectedVersion)"

# --- launch the NEW installed N13 --------------------------------------------
try {
    Start-Process -FilePath $AppExePath -WorkingDirectory $InstallDir | Out-Null
    Write-Log "RESTART launched $AppExePath"
} catch {
    Exit-Fail 50 "RESTART" "Could not launch $AppExePath : $($_.Exception.Message)"
}
Start-Sleep -Seconds 3
$started = Get-Process -Name "N13" -ErrorAction SilentlyContinue
if ($started) {
    Write-Log "RESTART ok (N13 running, pid $($started.Id))"
} else {
    Write-Log "RESTART warning: N13 process not detected after launch"
}

# --- clean temporary update files (best effort) ------------------------------
try {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction Stop
    Write-Log "CLEANUP removed $WorkDir"
} catch {
    Write-Log "CLEANUP warning: could not remove $WorkDir : $($_.Exception.Message)"
}

Write-Log "COMPLETE update to $ExpectedVersion succeeded"
exit 0
"""


# NOTE: DETACHED_PROCESS is intentionally NOT used — it breaks powershell.exe
# (a console-subsystem process), which then exits code 0 without executing.
# CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP are sufficient: the updater is a
# separate process with no console and its own process group, so it survives
# N13's exit and is not tied to N13's console/job.
_CREATE_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
)


def launch_powershell_updater(
    staging: Path,
    installer_path: Path,
    expected_checksum: str,
    expected_version: str,
    app_install_dir: Optional[Path] = None,
    pid: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Write and launch the independent PowerShell updater; wait for its ack.

    The updater script and the installer both live in *staging* (outside the
    installation directory).  Returns ``(True, None)`` only after PowerShell
    confirmed it is alive and will take over once N13 exits.  Never raises.
    """
    try:
        target_dir = app_install_dir or install_dir()
        if target_dir is None:
            log.error("UPDATE: installation directory unknown (source checkout?)")
            return False, UpdateError.DEV_MODE
        target_dir = target_dir.resolve()
        target_dir = Path(os.path.normpath(target_dir))

        exe_path = installed_exe_path() or (target_dir / "N13.exe")
        uninstaller = uninstaller_path(target_dir)
        if not uninstaller.is_file():
            log.error("UPDATE: uninstaller not found: %s", uninstaller)
            return False, UpdateError.UPDATER_FAILED

        # The updater executes entirely from TEMP — never from InstallDir.
        script = staging / PS1_NAME
        script.write_text(POWERSHELL_UPDATER, encoding="utf-8")

        ack_file = staging / UPDATER_START_MARKER
        try:
            ack_file.unlink(missing_ok=True)
        except OSError:
            pass

        args = [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-N13Pid", str(pid or os.getpid()),
            "-ExePath", str(exe_path),
            "-InstallDir", str(target_dir),
            "-Uninstaller", str(uninstaller),
            "-Installer", str(installer_path),
            "-ExpectedVersion", expected_version,
            "-ExpectedSha256", expected_checksum or "",
        ]
        log.info(
            "UPDATE: launching PowerShell updater pid_target=%s install_dir=%s setup=%s",
            pid or os.getpid(), target_dir, installer_path,
        )
        proc = subprocess.Popen(
            args,
            close_fds=True,
            creationflags=_CREATE_FLAGS,
            cwd=str(staging),
        )
        log.info("UPDATE: PowerShell updater started pid=%s", proc.pid)

        # The updater must confirm it is alive BEFORE N13 shuts down.
        deadline = time.monotonic() + UPDATER_START_TIMEOUT
        while time.monotonic() < deadline:
            if ack_file.is_file():
                log.info("UPDATE: PowerShell updater acknowledged - safe to shut down")
                return True, None
            if proc.poll() is not None:
                log.error("UPDATE: PowerShell updater exited early (code=%s)", proc.returncode)
                return False, UpdateError.UPDATER_FAILED
            time.sleep(0.1)
        log.error("UPDATE: PowerShell updater did not start within %.0fs", UPDATER_START_TIMEOUT)
        try:
            proc.kill()
        except OSError:
            pass
        return False, UpdateError.UPDATER_FAILED
    except Exception as exc:
        log.error("UPDATE: failed to launch PowerShell updater: %s", exc)
        return False, UpdateError.UPDATER_FAILED


# --------------------------------------------------------------------------- #
# UpdateController — UI-facing state machine (no UI dependencies)
# --------------------------------------------------------------------------- #

class UpdateController:
    """Thread-safe updater state machine.

    All network/disk work runs on background threads; subscribers receive an
    immutable snapshot dict on every change.  No UI imports — the bridge layer
    (``ui/api.py``) forwards snapshots to the frontend.
    """

    def __init__(self, config: Any = None):
        self._config = config
        self._lock = threading.Lock()
        self._state = UpdateState.IDLE
        self._release: Optional[ReleaseInfo] = None
        self._error: Optional[Dict[str, str]] = None
        self._progress: Dict[str, Any] = {
            "percent": 0, "downloaded_bytes": 0, "total_bytes": 0,
            "speed_bps": 0.0, "eta_seconds": 0.0,
        }
        self._staging: Optional[Path] = None
        self._installer_path: Optional[Path] = None
        self._expected_checksum: Optional[str] = None
        self._cancel = threading.Event()
        self._workers: list[threading.Thread] = []
        self._listeners: list[Callable[[Dict[str, Any]], None]] = []

    # -- state plumbing ---------------------------------------------------- #

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def _notify(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(snapshot)
            except Exception:
                pass

    def _transition(self, new_state: str, **updates: Any) -> bool:
        with self._lock:
            allowed = _TRANSITIONS.get(self._state, frozenset())
            if new_state not in allowed:
                log.warning("UPDATE: ignored invalid transition %s -> %s", self._state, new_state)
                return False
            self._state = new_state
            if "release" in updates:
                self._release = updates["release"]
            if "error" in updates:
                self._error = updates["error"]
            if new_state not in (UpdateState.FAILED,):
                if "error" not in updates and new_state in (
                    UpdateState.CHECKING, UpdateState.DOWNLOADING, UpdateState.VERIFYING,
                    UpdateState.READY_TO_INSTALL, UpdateState.INSTALLING,
                    UpdateState.UP_TO_DATE, UpdateState.AVAILABLE, UpdateState.CANCELLED,
                ):
                    self._error = None
            if "progress" in updates:
                self._progress.update(updates["progress"])
            if new_state == UpdateState.CHECKING:
                self._progress = {
                    "percent": 0, "downloaded_bytes": 0, "total_bytes": 0,
                    "speed_bps": 0.0, "eta_seconds": 0.0,
                }
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return True

    def _snapshot_locked(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "current_version": get_current_version(),
            "release": self._release.to_dict() if self._release else None,
            "error": dict(self._error) if self._error else None,
            "progress": dict(self._progress),
            "download_path": str(self._installer_path) if self._installer_path else None,
        }

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _fail(self, code: str, message: str) -> None:
        log.error("UPDATE: failed [%s] %s", code, message)
        self._transition(UpdateState.FAILED, error={"code": code, "message": message})

    def _spawn(self, target: Callable[..., None], *args: Any) -> None:
        t = threading.Thread(target=target, args=args, daemon=True, name="n13-updater")
        with self._lock:
            self._workers.append(t)
        t.start()

    # -- check -------------------------------------------------------------- #

    def check(self) -> None:
        """Start a background check for the latest release (idempotent)."""
        if not self._transition(UpdateState.CHECKING):
            return
        self._spawn(self._do_check)

    def _do_check(self) -> None:
        repo = getattr(self._config, "update_repo", None) or DEFAULT_REPO
        info, err = fetch_latest_release(repo)
        if info is None:
            self._fail(err or UpdateError.NETWORK, _error_message(err or UpdateError.NETWORK))
            return
        if not is_newer(info.version, get_current_version()):
            log.info("UPDATE: up to date (current=%s latest=%s)", get_current_version(), info.version)
            self._transition(UpdateState.UP_TO_DATE, release=info)
            return
        log.info("UPDATE: update available: %s -> %s", get_current_version(), info.version)
        self._transition(UpdateState.AVAILABLE, release=info)

    # -- download + verify -------------------------------------------------- #

    def download(self) -> None:
        """Download + verify the available installer in the background."""
        with self._lock:
            release = self._release
        if release is None:
            return
        if not self._transition(UpdateState.DOWNLOADING):
            return
        self._cancel.clear()
        self._spawn(self._do_download, release)

    def cancel_download(self) -> None:
        """Cancel an in-progress installer download (no-op otherwise)."""
        if self.state == UpdateState.DOWNLOADING:
            log.info("UPDATE: download cancellation requested")
            self._cancel.set()

    def _do_download(self, release: ReleaseInfo) -> None:
        staging = create_staging_dir(release.version)
        dest = staging / INSTALLER_NAME
        with self._lock:
            self._staging = staging
            self._installer_path = None
            self._expected_checksum = None
        log.info("UPDATE: download started: %s -> %s", release.installer_url, dest)

        def on_progress(written: int, total: int, speed: float, eta: float) -> None:
            pct = int(written * 100 / total) if total else 0
            self._transition(UpdateState.DOWNLOADING, progress={
                "percent": pct,
                "downloaded_bytes": written,
                "total_bytes": total,
                "speed_bps": speed,
                "eta_seconds": eta,
            })

        try:
            download_file(release.installer_url, dest, on_progress, self._cancel)
        except DownloadCancelled:
            log.info("UPDATE: download cancelled; removing partial file")
            _safe_unlink(dest)
            self._cleanup_staging()
            self._transition(UpdateState.CANCELLED)
            return
        except Exception as exc:
            log.error("UPDATE: download failed: %s", exc)
            _safe_unlink(dest)
            self._cleanup_staging()
            self._fail(UpdateError.DOWNLOAD_FAILED, _error_message(UpdateError.DOWNLOAD_FAILED))
            return
        log.info("UPDATE: download complete (%d bytes)", dest.stat().st_size)

        # Verification phase — mandatory, never skipped.
        self._transition(UpdateState.VERIFYING)
        expected = fetch_checksum(release.checksum_url) if release.checksum_url else None
        if not expected:
            log.error("UPDATE: release %s publishes no checksum — refusing to continue", release.tag)
            _safe_unlink(dest)
            self._cleanup_staging()
            self._fail(UpdateError.NO_CHECKSUM, _error_message(UpdateError.NO_CHECKSUM))
            return
        if not verify_installer(dest, expected):
            _safe_unlink(dest)
            self._cleanup_staging()
            self._fail(UpdateError.CHECKSUM_MISMATCH, _error_message(UpdateError.CHECKSUM_MISMATCH))
            return

        with self._lock:
            self._installer_path = dest
            self._expected_checksum = expected
        log.info("UPDATE: installer verified; ready to install")
        self._transition(UpdateState.READY_TO_INSTALL, progress={"percent": 100})

    # -- install handoff ----------------------------------------------------- #

    def install(self) -> Tuple[bool, Optional[str]]:
        """Write + launch the independent PowerShell updater and confirm start.

        On success the caller MUST proceed with the normal safe shutdown — the
        PowerShell updater waits for this process to exit before it uninstalls
        and installs.
        """
        with self._lock:
            if self._state != UpdateState.READY_TO_INSTALL or not self._installer_path:
                return False, UpdateError.NOT_READY
            staging = self._staging
            installer = self._installer_path
            checksum = self._expected_checksum or ""
            version = self._release.version if self._release else ""
        if not self._transition(UpdateState.INSTALLING):
            return False, UpdateError.NOT_READY
        ok, err = launch_powershell_updater(
            staging=staging,
            installer_path=installer,
            expected_checksum=checksum,
            expected_version=version,
        )
        if not ok:
            self._fail(err or UpdateError.UPDATER_FAILED, _error_message(err or UpdateError.UPDATER_FAILED))
            return False, err
        return True, None

    def _cleanup_staging(self) -> None:
        with self._lock:
            staging = self._staging
            self._staging = None
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    def reset(self) -> None:
        """Return to IDLE (used when the user dismisses a finished flow)."""
        with self._lock:
            current = self._state
        if current in (UpdateState.UP_TO_DATE, UpdateState.FAILED, UpdateState.CANCELLED):
            self._transition(UpdateState.IDLE)


# --------------------------------------------------------------------------- #
# Headless update (N13.exe --update-now) — same flow as the UI buttons
# --------------------------------------------------------------------------- #

_ERROR_MESSAGES = {
    UpdateError.NETWORK: "Unable to connect to the update server.",
    UpdateError.RATE_LIMITED: "The update server is rate-limiting requests. Try again later.",
    UpdateError.NO_RELEASE: "No stable release was found.",
    UpdateError.NO_INSTALLER: "The latest release has no installer.",
    UpdateError.NO_CHECKSUM: "Update verification data is unavailable for this release.",
    UpdateError.CHECKSUM_MISMATCH: "Update verification failed.",
    UpdateError.DOWNLOAD_FAILED: "The update download failed.",
    UpdateError.UPDATER_FAILED: "The update updater could not be started.",
    UpdateError.NOT_READY: "No verified update is ready to install.",
    UpdateError.DEV_MODE: "Updates can only be installed from an installed build.",
}


def _error_message(code: str) -> str:
    return _ERROR_MESSAGES.get(code, "Update failed.")


def _await_state(controller: UpdateController, targets: set, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = controller.state
        if state in targets:
            return state
        time.sleep(0.2)
    return controller.state


def run_headless_update(config: Any, out: Callable[[str], None] = print) -> int:
    """Full update flow without the WebView: check -> download -> verify ->
    handoff -> exit.  Used by ``N13.exe --update-now`` and by the end-to-end
    update validation.  Returns a process exit code.
    """
    controller = UpdateController(config)
    out(f"N13 {get_current_version()} — checking for updates…")
    log.info("UPDATE: headless update requested")
    controller.check()
    state = _await_state(controller, {
        UpdateState.UP_TO_DATE, UpdateState.AVAILABLE, UpdateState.FAILED,
    }, timeout=90)
    if state == UpdateState.UP_TO_DATE:
        out("N13 is up to date.")
        return 0
    if state != UpdateState.AVAILABLE:
        err = controller.get_state().get("error") or {}
        out(f"Update check failed: {err.get('message', 'unknown error')}")
        return 1

    release = controller.get_state()["release"] or {}
    out(f"Downloading update {release.get('version', '?')}…")
    controller.download()
    state = _await_state(controller, {
        UpdateState.READY_TO_INSTALL, UpdateState.FAILED, UpdateState.CANCELLED,
    }, timeout=3600)
    if state != UpdateState.READY_TO_INSTALL:
        err = controller.get_state().get("error") or {}
        out(f"Update failed: {err.get('message', state)}")
        return 1

    out("Update verified. Handing off to the PowerShell updater…")
    ok, err = controller.install()
    if not ok:
        out(f"Could not start the updater: {_error_message(err or UpdateError.UPDATER_FAILED)}")
        return 1
    out("Updater is ready — N13 will now close and the update will install.")
    # Returning lets the caller exit; the PowerShell updater takes over.
    return 0


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
