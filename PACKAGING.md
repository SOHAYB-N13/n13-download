# N13 Download Manager — Windows Packaging

This document describes how to build the Windows executable and installer for N13.

## Requirements

- Windows 10/11 64-bit development machine
- Python 3.12+ with a project virtual environment at `.venv`
- PyInstaller 6.x installed in `.venv`
- Inno Setup 7 (ISCC.exe) installed at `build\tools\InnoSetup7`

## One-step build

From the repository root in PowerShell:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
build\scripts\build_release.ps1
```

The script produces:

- `dist\N13\N13.exe` — the unpackaged one-dir application
- `release\N13-Download-Manager-Setup.exe` — the Windows installer

## What is packaged

- Python runtime + all runtime dependencies from `requirements.txt`
- `ui/frontend` (HTML/CSS/JS)
- `extension` (browser-extension template)
- Windows metadata and icon from `assets/icon.ico`
- pywebview / pythonnet native bridge files

## User data

N13 never stores user data in the installation directory. All writable data lives
under:

```
%LOCALAPPDATA%\N13\
    config\        config.json, ui_prefs.json
    data\          downloads.db
    saved_links\   batch URL lists
    logs\          application logs
```

The installer and uninstaller preserve this directory.

## WebView2 runtime

The GUI requires the Microsoft Edge WebView2 Runtime. Windows 11 ships it by
default; most updated Windows 10 systems also have it. The installer detects
WebView2 and, if missing, offers to open the download page before aborting the
installation.

## Version bump

Edit `core/version.py`, then rebuild. `build/generate_version_files.py` will
regenerate `build/version.txt` and `build/version_info.txt` automatically. The
same version is applied to:

- `N13.exe` VERSIONINFO
- Inno Setup metadata
- Add/Remove Programs entry
- installer filename

## Auto-update system

N13 checks the configured GitHub repository for releases. See
`docs/UPDATE_SYSTEM.md` for the required release layout, checksum format, and
security details.

## Manual steps

Build only the executable:

```powershell
.venv\Scripts\pyinstaller build\n13.spec --clean -y
```

Build only the installer (after the executable exists):

```powershell
build\tools\InnoSetup7\ISCC.exe installer\N13-Setup.iss
```

## dldm:// protocol

The installer registers `dldm://` under the current user's registry. Invoking a
`dldm://...` URL launches `N13.exe "%1"`. If N13 is already running, the URL is
forwarded to the running instance; otherwise the application opens normally.
