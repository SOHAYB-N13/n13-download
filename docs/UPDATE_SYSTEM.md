# N13 Download Manager — Auto-Update System

## Overview

N13 uses GitHub Releases as the single official update source. The updater is
a small isolated component (`core/updater.py`) that:

1. Checks the configured repository for the latest stable release.
2. Compares semantic versions.
3. Downloads the official installer asset.
4. Verifies the SHA-256 checksum.
5. Writes and launches one independent PowerShell updater (`update.ps1`) that
   performs the entire post-shutdown update and safe-shutdown path.

The update system is designed to be deterministic, lightweight, and safe: a
failed update check can never prevent N13 from launching or corrupt the current
installation.

## Version source of truth

The authoritative version is `VERSION` in `core/version.py`.

During the build, `build/generate_version_files.py` writes:

- `build/version.txt`
- `build/version_info.txt`

These are consumed by PyInstaller and Inno Setup so the EXE, installer, and
Add/Remove Programs all report the same version.

To bump the version, edit `core/version.py`, then rebuild.

## Required GitHub Release layout

Create a release with tag `vMAJOR.MINOR.PATCH` (for example `v1.1.0`).

Required assets:

- `N13-Download-Manager-Setup.exe` — the installer
- `N13-Download-Manager-Setup.exe.sha256.txt` — SHA-256 checksum file

The checksum file must contain the 64-character hex digest of the installer as
the first token (the standard `sha256sum` output format works).

Release notes go in the GitHub release body; they are shown to the user in the
update dialog.

## How to calculate the checksum

On Windows PowerShell:

```powershell
Get-FileHash N13-Download-Manager-Setup.exe -Algorithm SHA256 | Select-Object -ExpandProperty Hash | Out-File N13-Download-Manager-Setup.exe.sha256.txt
```

Or with any standard SHA-256 tool:

```bash
sha256sum N13-Download-Manager-Setup.exe > N13-Download-Manager-Setup.exe.sha256.txt
```

## Configuration

The repository checked by the updater is configured in `config/settings.py`:

```python
update_repo: str = "SOHAYB-N13/n13-download"
auto_update_check: bool = True
```

Users can disable automatic startup checks from **Settings → Updates**.

## Update flow (the ONLY allowed architecture)

```
N13
  -> Check GitHub (api.github.com/repos/{repo}/releases/latest)
  -> Detect newer release
  -> User chooses Update
  -> Download N13-Download-Manager-Setup.exe to %TEMP%\N13-Updater\<id>\
  -> Verify SHA-256 against the official release checksum
  -> Write update.ps1 next to the installer (also in %TEMP%\N13-Updater\<id>\
  -> Launch powershell.exe (detached, -ExecutionPolicy Bypass) with all params
  -> N13 performs the EXISTING safe shutdown and exits
  -> PowerShell waits for N13 to exit (polls the exact PID, never kills it)
  -> PowerShell runs the real uninstaller: <InstallDir>\unins000.exe
        /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
  -> PowerShell waits for the uninstaller to finish
  -> PowerShell verifies the old installation is really gone
  -> PowerShell removes any remaining old files in the exact captured InstallDir
  -> PowerShell deletes %LOCALAPPDATA%\N13  (intentional for this flow)
  -> PowerShell runs the NEW installer from TEMP:
        /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR="<ORIGINAL_INSTALL_DIR>"
  -> PowerShell waits for the installer and records its exit code
  -> PowerShell verifies <InstallDir>\N13.exe exists
  -> PowerShell reads the new N13.exe file version and compares it with the
     expected release version
  -> PowerShell launches the NEW installed N13.exe
  -> PowerShell cleans %TEMP%\N13-Updater\<id>\ (best effort) and exits
```

Both the manual button flow and the automatic flow use this exact same path,
and the headless `N13.exe --update-now` flow runs the same implementation.

### Install directory

The install directory is never hard-coded. N13 resolves the real path at
runtime:

- `install_dir()` — parent of the running `sys.executable` when frozen.
- `uninstaller_path(install_dir)` — `<InstallDir>\unins000.exe`, verified to
  exist before any update starts. If it is missing the update stops.

### PowerShell updater

- Script: `%TEMP%\N13-Updater\<id>\update.ps1`
- It is embedded in `core/updater.py` (module constant `POWERSHELL_UPDATER`).
- It is the ONLY component that touches the installer or uninstaller after
  N13 shuts down. It runs entirely from TEMP and does not depend on Python,
  WebView, N13 threads, child processes, or the N13 working directory.
- Every stage is logged to `update.log` in the same TEMP directory.

Required stages logged:

```
UPDATER_START
N13_EXIT
UNINSTALL
UNINSTALL_VERIFY
USER_DATA_DELETE
INSTALL
INSTALL_VERIFY
RESTART
CLEANUP
```

Any failure aborts immediately with the exact stage and exit code; the updater
never reports success unless the new N13.exe version matches the release.

### Checksum policy

Verification is mandatory. If the release publishes a SHA-256 asset it is
downloaded and compared; a mismatch stops the update and deletes the invalid
installer. If the release has no checksum metadata the updater does NOT invent
one — it reports the verification data as unavailable and refuses to install
(the project's release policy).

### User data

For this update flow, `%LOCALAPPDATA%\N13` is intentionally removed during the
update. Nothing else (Desktop, Documents, Downloads, selected download
directories, or unrelated AppData directories) is touched. The data directory
is recreated on the next launch.

## Safety guarantees

- No file inside the installation directory is modified directly by N13.
- The running `N13.exe` is never replaced in-place; the old build is removed by
  its own uninstaller before the new installer runs.
- The updater script and installer live outside the installation directory.
- Checksum verification is mandatory; a mismatched installer is discarded.
- Active downloads are paused and persisted before shutdown so they remain
  resumable after the update.
- Failed checks/downloads time out and leave the current installation intact.

## Files involved

- `core/version.py` — version constant and semantic-version helpers
- `core/updater.py` — release discovery, download, verification, the embedded
  `update.ps1`, and the updater launch
- `ui/api.py` — bridge methods exposed to the JavaScript UI
- `ui/frontend/js/api.js` — JS bridge wrappers
- `ui/frontend/js/app.js` — update panel, dialog, and startup check
- `ui/frontend/js/i18n.js` — English / Persian update strings
- `ui/frontend/css/main.css` — update panel styles
- `build/generate_version_files.py` — syncs version files at build time
- `docs/UPDATE_SYSTEM.md` — this document
