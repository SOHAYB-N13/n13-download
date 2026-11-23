# N13 Download Manager — Windows Packaging & Installation

This document describes how N13 is packaged, installed, upgraded and
uninstalled on Windows.

## Requirements (build machine only)

- Windows 10/11 64-bit development machine
- Python 3.12+ with a project virtual environment at `.venv`
- PyInstaller 6.x installed in `.venv`
- Inno Setup 7 (ISCC.exe) at `build\tools\InnoSetup7`

The **installed** application does not require Python, PyInstaller, Inno Setup
or any development tool on the user's computer.

## One-step build

From the repository root in PowerShell:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
build\scripts\build_release.ps1
```

The script produces:

- `dist\N13\N13.exe` — the production one-dir application bundle
- `release\N13-Download-Manager-Setup.exe` — the Windows installer
- `release\N13-Download-Manager-Setup.exe.sha256.txt` — checksum required by
  the auto-updater

The script refuses to produce an installer if the bundle exposes raw project
sources, tests, build scripts, documentation, VCS metadata or unrelated
development folders.

## Packaging approach

**PyInstaller one-dir bundle + Inno Setup LZMA2 installer.**

Reasons:

- Python modules are compiled into `PYZ-00.pyz` inside `_internal`; the
  project's own `.py` sources are not shipped as loose files.
- One-dir avoids extracting the whole runtime to `%TEMP%` on every launch.
  This matters because the `dldm://` protocol handler and the Chrome native
  messaging host start `N13.exe` frequently; a one-file build would pay the
  extraction cost on every browser action.
- Subprocesses (`--native-host`, `--install-extension`, `--update-now`),
  WebView2/pywebview, pythonnet, tkinter dialogs and the download engine all
  work from the same bundle.
- Inno Setup compresses the bundle with LZMA2 + solid compression, so the
  installed size is much smaller than the unpacked directory.

Development-only packages (comtypes/pywinauto test suites, `tests/`, build
tooling) are excluded by `build/n13.spec`. Third-party runtime libraries are
kept; their presence is normal for a bundled desktop application.

Packaging is **not encryption**. The PYZ archive and the installer compression
make casual inspection harder, but they do not cryptographically protect the
Python code. No such claim is made.

## Installation

### Default location

```
{autopf}\N13 Download Manager
```

`{autopf}` is the standard Inno Setup constant:

- per-user install (default): `%LOCALAPPDATA%\Programs\N13 Download Manager`
- all-users install (administrator override): `%ProgramFiles%\N13 Download Manager`

No drive letter, username or developer path is hardcoded. The installer:

- shows the directory page and accepts any local path,
- supports spaces, Unicode/Persian characters and drives other than C:,
- remembers the previous directory on upgrade (`UsePreviousAppDir=yes`),
- refuses to run while N13 is running (`AppMutex`).

### Privilege model

The default is a **per-user installation** (`PrivilegesRequired=lowest`), which
needs no UAC prompt and writes only to the current user's profile. The standard
Inno Setup "Install for all users" override is enabled; choosing it goes through
the normal Windows UAC elevation flow and installs under `%ProgramFiles%`.

No security mechanism is bypassed, weakened or silently elevated.

### Registration

- Start Menu shortcut: `N13 Download Manager`
- Start Menu uninstall shortcut: `Uninstall N13 Download Manager`
- Optional desktop shortcut (unchecked by default)
- Installed Apps / Apps & Features entry with name, publisher, version and icon
- Per-user `dldm://` protocol registration:
  `"{app}\N13.exe" "%1"`
- Per-user Chrome/Edge native-messaging registration is created by the
  application on first run (see the extension audit below)

## User data locations

All writable user data lives outside the installation directory:

```
%LOCALAPPDATA%\N13\
    config\        config.json, ui_prefs.json
    data\          downloads.db (SQLite, WAL)
    saved_links\   batch URL lists and batch resume state
    logs\          application logs
    native_host\   native-messaging manifest + launcher
    chrome_extension\  regenerable extension copy (when needed)
```

The application never writes configuration, databases, logs or downloaded files
into `{app}`. Downloads go wherever the user chose in N13's settings.

## Uninstall

The standard Inno Setup uninstaller (`unins000.exe`, registered in Installed
Apps) removes:

- files installed under `{app}`,
- Start Menu and desktop shortcuts,
- the per-user `dldm://` registration,
- Chrome/Edge native-messaging registry entries for `com.n13.download_manager`,
- the regenerable extension copy under `{app}` (if any).

It **never** deletes `%LOCALAPPDATA%\N13` or any other user-data location. The
completion message tells the user exactly where their data was preserved. There
is no silent data-removal option; users who want to erase their data can delete
that folder manually.

Upgrades and uninstalls tolerate already-missing files and shortcuts. If an
older build left a machine-wide `dldm://` registration, it is removed only when
it is recognisably an N13 registration.

## Updates

See `docs/UPDATE_SYSTEM.md`. Updates run the new installer as an **in-place
upgrade** into the exact directory detected from the running `N13.exe`. They do
not uninstall first and do not delete or recreate `%LOCALAPPDATA%\N13`.

## WebView2 runtime

The GUI requires the Microsoft Edge WebView2 Runtime. Windows 11 ships it by
default; most updated Windows 10 systems also have it. The installer detects
WebView2 (machine-wide and per-user) and, if missing, offers to open the
download page. Silent installs log the finding and continue; the application
reports the missing runtime itself.

## Version source of truth

Edit `core/version.py`, then rebuild. `build/generate_version_files.py`
regenerates `build/version.txt` and `build/version_info.txt`. The same version is
applied to:

- `N13.exe` VERSIONINFO
- Inno Setup metadata
- Installed Apps entry
- installer filename

The build script verifies that the compiled EXE version matches
`build/version.txt`.

## Browser extension (read-only audit)

The extension files under `extension/` and `chrome_extension/` are not modified
by the Windows packaging work. Audit findings:

- The extension itself contains no developer username, drive letter or install
  path. It talks to `http://127.0.0.1:6868` and native messaging.
- Native-messaging registration is derived at runtime from the running
  `N13.exe`, the per-user data directory, and extension IDs discovered from
  Chrome/Edge profiles. It is per-user (HKCU), needs no administrator rights,
  and is recomputed if the installation moves.
- An unpacked Chrome extension's ID is derived from its folder path. Installing
  N13 elsewhere changes that ID, but the application re-discovers loaded
  extension IDs on startup and rewrites the native host manifest, so the
  integration remains functional after a move.
- When the installation directory is writable, the application may create a
  regenerable extension copy under `{app}\_internal\chrome_extension` and mirror
  the live token there. That copy contains no unique user data and is removed on
  uninstall/upgrade. It is regenerated from the bundled template and
  `%LOCALAPPDATA%\N13\config\config.json`.

No extension code, manifest, permissions, profile or registration logic was
changed as part of this work.

## Known limitations

- The installer and executable are not code-signed; Windows SmartScreen may show
  a warning until a signing certificate is configured.
- WebView2 must be present (or installed) for the GUI.
- The bundle is one-dir and installs roughly 60 MB (compressed by Inno Setup).
- A fully interrupted update may require re-running the installer; the
  in-place design leaves user data untouched and the installer can be retried.
