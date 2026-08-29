"""Locate the real N13 Chrome extension directory.

Works in BOTH development/source mode and installed/frozen (PyInstaller) mode:

* source:   <project root>/chrome_extension          (generated copy)
* frozen:   <_MEIPASS>/chrome_extension              (next to the bundled data)
* fallback: %LOCALAPPDATA%/N13/chrome_extension      (per-user writable copy)

Nothing here hardcodes a drive letter or an absolute installation path.  Every
candidate is derived from the *running* application (sys.executable /
sys._MEIPASS / module location) and validated before being accepted:

1. directory exists
2. manifest.json exists
3. manifest.json is valid JSON
4. manifest belongs to the N13 Download Manager extension
5. every file referenced by the manifest exists on disk
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("n13")

TAG = "[N13 Extension]"

EXTENSION_NAME_MARKERS = ("N13", "Download Manager")
# Names of folders that may hold a loadable N13 extension inside the
# application structure: the installable copy and the bundled template.
_LOCAL_FOLDER_NAMES = ("chrome_extension", "extension")


class ExtensionLocatorError(Exception):
    """Raised when the N13 extension directory cannot be found or created."""


def _log(emit: Optional[Callable[[str], None]], message: str) -> None:
    line = f"{TAG} {message}"
    if emit is not None:
        try:
            emit(line)
        except Exception:
            pass
    log.info("%s %s", TAG, message)


def app_root() -> Path:
    """Root directory of the running N13 application (source or frozen)."""
    if getattr(sys, "frozen", False):
        if getattr(sys, "_MEIPASS", None):
            # PyInstaller one-dir: the bundled data (extension template etc.)
            # lives in _MEIPASS, e.g. <install>\\_internal.
            return Path(sys._MEIPASS)
        # Fallback: the directory holding the executable itself.
        return Path(sys.executable).resolve().parent
    # Source: this file is <root>/browser/extension_locator.py.
    return Path(__file__).resolve().parent.parent


def user_extension_dir() -> Path:
    """Per-user extension copy location (writable in every install scenario)."""
    try:
        from core.paths import user_data_dir

        return user_data_dir() / "chrome_extension"
    except Exception:
        base = Path.home() / "AppData" / "Local" / "N13"
        return base / "chrome_extension"


def _writable(directory: Path) -> bool:
    """Best-effort writability probe (no file is actually created)."""
    try:
        probe = directory / ".n13_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _manifest_required_files(manifest: dict) -> list[str]:
    """Every file the manifest references, relative to the extension root."""
    refs: list[str] = []

    def add(value) -> None:
        if isinstance(value, str) and value:
            refs.append(value)

    bg = manifest.get("background") or {}
    if isinstance(bg, dict):
        add(bg.get("service_worker"))
        if isinstance(bg.get("scripts"), list):
            for s in bg["scripts"]:
                add(s)
    action = manifest.get("action") or {}
    if isinstance(action, dict):
        add(action.get("default_popup"))
        for v in (action.get("default_icon") or {}).values():
            add(v)
    for v in (manifest.get("icons") or {}).values():
        add(v)
    for cs in manifest.get("content_scripts") or []:
        if isinstance(cs, dict) and isinstance(cs.get("js"), list):
            for js in cs["js"]:
                add(js)
    options = manifest.get("options_page")
    if isinstance(options, str):
        add(options)
    return refs


def validate_extension_dir(directory: Path) -> tuple[bool, str]:
    """Validate *directory* as a loadable N13 extension.

    Returns (ok, reason).  Never raises for missing/invalid input.
    """
    try:
        if not directory.is_dir():
            return False, "not a directory"
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return False, "manifest.json missing"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "manifest.json is not valid JSON"
        if not isinstance(manifest, dict):
            return False, "manifest.json is not an object"
        name = str(manifest.get("name", ""))
        if not name or not all(marker in name for marker in EXTENSION_NAME_MARKERS):
            return False, f"manifest name is not N13 ({name!r})"
        for ref in _manifest_required_files(manifest):
            if not (directory / ref).is_file():
                return False, f"manifest references missing file: {ref}"
        return True, "valid N13 extension"
    except OSError as exc:
        return False, str(exc)


def candidate_dirs() -> list[Path]:
    """Candidate extension directories for the running application.

    Ordered by preference: the app-local copy first (belongs to the current
    installation), then the per-user copy.  The bundled *template* is never a
    direct candidate — its token.json is a build-time snapshot and may not
    match the live config; it is only used to materialize a fresh copy.
    """
    root = app_root()
    candidates: list[Path] = [root / "chrome_extension"]
    user_dir = user_extension_dir()
    if user_dir not in candidates:
        candidates.append(user_dir)
    return candidates


def materialize_extension_dir(emit: Optional[Callable[[str], None]] = None) -> Path:
    """Create a loadable N13 extension copy from the bundled template.

    Tries the application directory first (source runs are writable), then
    falls back to the per-user data directory (frozen installs under Program
    Files are read-only).  The token.json credential is mirrored from the
    live config so the freshly created copy authenticates immediately.
    """
    template = app_root() / "extension"
    ok, reason = validate_extension_dir(template)
    if not ok:
        raise ExtensionLocatorError(
            f"{TAG} bundled extension template is unusable: {reason}"
        )
    destinations = [app_root() / "chrome_extension", user_extension_dir()]
    for dst in destinations:
        try:
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(template, dst)
            if not _writable(dst):
                shutil.rmtree(dst)
                continue
            _log(emit, f"Materialized extension copy: {dst}")
            # Mirror the live server credential into the fresh copy so the
            # installed extension authenticates with THIS installation.
            try:
                from browser.protocol import sync_extension_token
                from config.loader import load_config

                sync_extension_token(load_config(), ext_dir=dst)
            except Exception as exc:
                log.info("%s token sync skipped for %s: %s", TAG, dst, exc)
            ok, reason = validate_extension_dir(dst)
            if not ok:
                raise ExtensionLocatorError(
                    f"{TAG} materialized copy failed validation: {reason}"
                )
            return dst
        except OSError as exc:
            log.info("%s cannot write %s: %s", TAG, dst, exc)
    raise ExtensionLocatorError(
        f"{TAG} could not create a writable extension copy "
        f"(tried {[str(d) for d in destinations]})"
    )


def discover_extension_dir(emit: Optional[Callable[[str], None]] = None) -> Path:
    """Stage 1: find and validate the real N13 extension directory.

    Raises ExtensionLocatorError with a clear message when it cannot be found.
    """
    _log(emit, "Stage 1: locating extension...")
    _log(emit, f"Application root: {app_root()}")

    validated: list[tuple[Path, str]] = []
    for candidate in candidate_dirs():
        _log(emit, "Searching extension directory...")
        _log(emit, f"Found candidate: {candidate}")
        ok, reason = validate_extension_dir(candidate)
        if ok:
            validated.append((candidate, reason))
            _log(emit, "Validating manifest...")
            _log(emit, f"Valid N13 extension: {candidate}")

    if validated:
        # Prefer the app-local chrome_extension copy; never an unrelated one.
        chosen = validated[0][0]
        # Mirror the live credential into the chosen copy (idempotent) so a
        # pre-baked copy always authenticates with THIS installation's token.
        try:
            from browser.protocol import sync_extension_token
            from config.loader import load_config

            sync_extension_token(load_config(), ext_dir=chosen)
        except Exception as exc:
            log.info("%s token sync skipped for %s: %s", TAG, chosen, exc)
        _log(emit, f"Extension directory: {chosen}")
        return chosen

    _log(emit, "No valid extension copy found; materializing from template...")
    chosen = materialize_extension_dir(emit)
    _log(emit, f"Extension directory: {chosen}")
    return chosen
