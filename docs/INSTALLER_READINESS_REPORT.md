# N13 Installer & Filesystem Readiness Report

Date: 2026-08-11
Scope: temporary-file cleanup, user-data architecture, safe migration,
non-administrator execution, installer preparation. No new major features; no
UI redesign; no media/player/streaming functionality.

---

## User Data Architecture
**PASS**

All user-writable data now lives under a per-user directory, never in the
installation/repository directory:

```
%LOCALAPPDATA%\N13\          (Windows)   ~/.local/share/n13   (POSIX)
    config\        config.json, ui_prefs.json, relay token
    data\          downloads.db (task DB) + legacy queue/history JSON
    saved_links\   batch URL lists (links_*.json), batch resume state
    logs\          log files
```

No hardcoded usernames/absolute paths — `LOCALAPPDATA` / `XDG_DATA_HOME` are
read via the environment. Implemented in `core/paths.py` and wired through
`config/loader.py`, `ui/api.py`, `d.py`, and `batch/pattern.py`.

## Database Location
**PASS**

The SQLite task database is created at `%LOCALAPPDATA%\N13\data\downloads.db`
(user-writable), not next to the code. Verified: a clean `%LOCALAPPDATA%`
profile produces the DB at the expected path.

## Migration
**PASS**

- Legacy `~/.config/terminal-download-manager/config.json` (+ `ui_prefs.json`)
  is copied once to the new config dir.
- Legacy project-relative `saved_links/` is migrated: `downloads.db` and legacy
  queue/history JSON → `data/`; URL lists + batch resume → `saved_links/`.
- **Idempotent** (re-run is a no-op), **failure-aware** (a failed copy is
  logged, originals are never deleted), and covered by `tests/test_paths.py`.
- Verified end-to-end: an old `saved_links/downloads.db` migrates, the app
  opens the new DB, and the original is preserved. (The corruption-recovery
  path even correctly quarantined a garbage legacy DB and rebuilt a fresh one.)

## Non-Administrator Execution
**PASS**

The app only writes under `%LOCALAPPDATA%` (user profile) and the user's
download directory. The clean-profile probe (fresh `LOCALAPPDATA`, no legacy)
ran install → first launch → download → pause → cancel → remove → temp cleanup
without elevation.

## Temporary File Cleanup
**PASS**

Removing an **incomplete / cancelled / failed** task deletes the temporary
artifacts that belong exclusively to it: `.partN` / `.repart-*` segment files,
`.tmp` (single-thread), `.merging`, and `.dlstate`. Cleanup is scoped to the
task's exact resolved destination path (recorded when the engine resolves it),
so it cannot delete another task's files or the completed file.

## Cancel → Remove Cleanup
**PASS**

```
DOWNLOAD → CANCEL → CANCELLED/INCOMPLETE → REMOVE → task removed + temp files removed
```
Cancelling alone keeps the partial files (for Retry/Resume); removal deletes
them. Covered by `test_active_cancel_remove_multipart`,
`test_cancel_then_retry_preserves_partial`.

## Pause → Remove Cleanup
**PASS**

Pause keeps `.part` (Resume depends on it); Remove deletes it. Covered by
`test_paused_remove_cleanup`.

## Failed → Remove Cleanup
**PASS**

Failed download → Remove → temp files gone, unrelated files in the same folder
untouched. Covered by `test_failed_remove_cleanup`.

## Multi-Segment Cleanup
**PASS**

All segment temp files (`.partN`, `.repart-*`, `.dlstate`) are removed;
scoping is per-task, never a broad `*.part` wildcard. Covered by the
multi-segment cleanup tests and `test_similar_filenames_scoped_cleanup`.

## Completed File Preservation
**PASS**

Removing a completed task / history entry does **not** delete the final file —
only the database/history record is removed (a separate explicit "Delete file"
action exists). Covered by `test_completed_remove_preserves_final_file`.

## Cleanup Race Safety
**PASS**

Removing an **active** task requests cancellation first; the worker's finalize
performs the cleanup only after the transfer loop has stopped, so a worker can
never re-create a deleted file. `retry_task`/`remove_task` also join a
still-unwinding worker (releasing its file handles) before reopening/cleaning.
The race was reproduced during development (WinError 32 on part files) and is
now covered by `test_remove_active_worker_race` (which also sleeps and re-checks
that no file reappears).

## Restart Cleanup
**PASS**

After a crash/abrupt close the task is restored (with its resolved path
persisted); removing it then cleans the partial files. Covered by
`test_cleanup_after_restart` (real subprocess crash → restart → remove).

## Update Safety
**PASS**

All user data lives outside the installation directory, so an application
update that replaces the code/install folder cannot touch `downloads.db`,
`saved_links`, history, or configuration. (No in-place updater exists; the
recommended distribution replaces the app folder only.)

## Uninstall Safety
**PASS**

There is no uninstaller that deletes data. Uninstalling the application
(binary/source removal) leaves `%LOCALAPPDATA%\N13\` and the user's downloads
intact. A future uninstaller must not delete these unless explicitly selected.

## Automated Tests
**120 / 120 PASS**

Added for this pass: `tests/test_cleanup.py` (11 — active cancel/remove,
single-segment, multi-segment, failed, paused, cancel-then-retry, completed
preservation, locked temp file, after-restart, worker race, missing temp,
similar filenames) and `tests/test_paths.py` (4 — user-data dir, config
migration, saved-links migration, load_config at new location). All previous
regression tests still pass.

## Clean Windows Test
**NOT AVAILABLE** (no clean Windows 11 VM in this environment)

Simulated via a fresh `%LOCALAPPDATA%` profile (no legacy data): install-path
init, first launch, download, pause, resume, cancel, remove, temp cleanup,
restart persistence, and database creation all verified against a real engine
+ GUI backend. A physical clean-machine run remains a pre-release checklist
item.

## Packaging Recommendation

**PyInstaller (one-dir)** is recommended over Nuitka for the current
architecture:

- **Runtime**: Python 3.10+; `pip install -r requirements.txt`.
- **GUI**: pywebview 5.x (WebView2 backend) — Windows 11 ships the WebView2
  runtime; for Windows 10 the installer must check/install it. PyInstaller must
  bundle `pythonnet`/`clr_loader` (pywebview's Windows bridge); use
  `--collect-all pythonnet` (or the pywebview PyInstaller hook).
- **Assets**: bundle `ui/frontend/**` and `extension/**` as data files (the
  GUI loads `index.html` from disk; the extension generator copies
  `extension/`).
- **dldm:// handler**: the registered command must point at the packaged
  `N13.exe` with a URL argument (e.g. `"N13.exe" "%1"`) instead of
  `python d.py`; keep the temp-file URL hand-off.
- **Optional deps**: `browser-cookie3` (and its heavy transitive pywin32/wmi)
  can be excluded from the bundle to keep it lean — the engine works without
  it. `tkinter` is bundled automatically (clipboard monitor, optional).
- **Data**: all writes go to `%LOCALAPPDATA%\N13\` — no Program Files writes.
- **One-dir vs one-file**: one-dir is preferred (faster startup, simpler asset
  bundling, fewer antivirus false positives).
- Nuitka is a valid alternative for faster startup/lower footprint but has
  longer build times and more complex plugin setup; not required.

## Remaining Risks
1. **Clean physical Windows 11 machine test** not performed (no VM available);
   the fresh-profile simulation is the closest available evidence.
2. **Packaging itself is not built** — PyInstaller config/spec and the
   `dldm://` handler invocation for the packaged exe must be created and tested
   when the installer is prepared.
3. **WebView2 runtime** must be present on Windows 10 targets (bundled on
   Windows 11).
4. **Clipboard monitor** (optional, off) still uses tkinter in a worker thread
   — future refactor to win32clipboard recommended (unchanged here).
5. `browser-cookie3`'s transitive dependencies increase bundle size; excluding
   it is recommended for a lean installer.

## Remaining Blockers
**None for source distribution.**

For an **installer/binary distribution**, the one concrete remaining item is to
produce and validate the PyInstaller build (bundle frontend/extension assets,
pin the `dldm://` handler to the packaged exe, collect pywebview's native
bridge) and to run the resulting build on a clean Windows 11 machine. The
filesystem, user-data, migration, cleanup, and non-admin requirements this pass
was asked to establish are all implemented and tested.

---

# Release Status
**INSTALLER READY** for the filesystem/user-data/cleanup aspects: migration
works, no administrator privileges required, temporary-file cleanup works for
Cancel→Remove, Pause→Remove and Failed→Remove, multi-segment cleanup is scoped
and race-safe, completed files are preserved, restart cleanup works, all 120
automated tests pass, and the clean-profile flow passes.

The actual installer binary (PyInstaller build + clean-machine validation)
remains a delivery task, not a code blocker.
