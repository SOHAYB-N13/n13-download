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

`dldm://` is an **application** protocol. The installer registers it per user
under `HKCU\Software\Classes\dldm` with a command built from the installer's own
`{app}` directory:

```
"{app}\N13.exe" "%1"
```

`{app}` is resolved to whatever directory the user actually chose, so the
registration is correct for any drive letter, install path or Windows account.
Nothing about the path is hardcoded anywhere in the project.

Flow:

```
dldm://<url> -> Windows protocol handler -> "<install dir>\N13.exe" "%1"
                                              |
                          build/n13_entry.py parses dldm:// from argv
                                              |
                              running instance (forwarded) or cold start
```

Rules that must not regress (enforced by `tests/test_protocol_registration.py`):

- `browser/dldm_handler.py` is an **internal** module (reused by
  `browser/native_host.py`). It must never be registered as an OS protocol
  launcher.
- The registered command must never contain `python.exe`, `pythonw.exe`,
  `py.exe`, `cmd.exe`, `powershell.exe`, `d.py`, `n13_entry.py` or any
  `_internal\...` path.
- `DefaultIcon` must point at the application, never at an interpreter.
- `N13.exe` is built in GUI (no-console) mode, so invoking `dldm://...` never
  shows a console window.

### Registration self-heal

`browser/protocol.py` re-asserts the registration on every application start
(`ensure_protocol_registration`, called from `build/n13_entry.py`). This repairs
installations broken by older builds — for example one that registered
`N13.exe <install>\_internal\browser\dldm_handler.py "%1"` — without requiring a
reinstall. It is a no-op (zero registry writes) when the registration is already
correct, and only ever touches `HKCU\Software\Classes\dldm`.

### Upgrade behaviour

The installer rewrites the registration unconditionally at `ssPostInstall`
(`RepairProtocolRegistration`), so upgrading an existing/broken installation
repairs it. Stale machine-wide (`HKLM`) entries are removed only when they are
recognisably N13 registrations; the uninstaller removes the per-user key via
`uninsdeletekey` plus an explicit cleanup pass.

