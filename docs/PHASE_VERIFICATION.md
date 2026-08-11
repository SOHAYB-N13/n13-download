# N13 Download Manager — Phase Verification Report

Date: 2026-08-10
Scope: technical verification of Phases A–G (per the freeze request). No new
phases were started; Phase K (Media/Player/Streaming) is explicitly out of
scope and has been removed from the codebase and roadmap.

---

## Phase A — Audit & Architecture
**PASS**

- The full repository was inspected before any changes (every `.py`, `.js`,
  `.html`, `.css`, `.json`, `requirements.txt`, `README.md`).
- The audit report (`docs/AUDIT.md`) documents architecture, engine, threading
  model, persistence, queue, browser integration, GUI architecture, error
  handling, security, performance, existing problems, reusable components, and
  recommended architecture.
- No unnecessary rewrites were introduced: the engine (`core/download.py`,
  `core/session.py`, `core/security.py`, etc.) was preserved; changes are
  additive or tightly scoped.
- Confirmed pre-existing problems were logged (P1 `filename_from_url` NameError,
  P2 `pyfiglet` missing from requirements, P3 global pause/cancel bleed, etc.).

---

## Phase B — DownloadTask + State Machine
**PASS** (12 unit tests)

- `core/task.py` implements `DownloadTask` with all specified fields
  (id, url, filename, destination, total/downloaded, current/average speed,
  ETA, status, priority, created/started/completed timestamps, retry_count,
  error, connections, checksum, content_type, server, supports_range).
- All 11 statuses; every transition validated against an explicit table;
  illegal transitions raise `TransitionError` (never silent).
- No scattered booleans — a task has exactly one `status`.
- `__post_init__` normalizes string statuses defensively.
- `normalize_status` maps legacy "Stopped"/"Stopping" → CANCELLED/DOWNLOADING
  for old queue files.
- **Race-condition review (worker thread safety):**
  - All task mutation happens under `TaskManager._lock` (an `RLock`); the only
    lock ordering is manager → store, and the store never calls back into the
    manager → no lock inversion, no deadlock.
  - `update_progress` and the worker `finally` both hold the manager lock.
  - **Found & fixed:** a retry race where a finishing worker could clobber a
    re-queued task (old `force_status(CANCELLED)` over a fresh `QUEUED`).
    Fixed with a control-identity guard in the worker finalize
    (`rec.control is not control` → the old worker backs off).

---

## Phase C — Persistent SQLite Store
**PASS** (10 unit tests)

- `core/store.py`: WAL journaling, `synchronous=NORMAL`, schema version pragma
  (`user_version=1`), tasks / segments / history / queue_order tables.
- Insert/update/delete, history bounded at 1000, legacy `gui_queue.json` +
  `gui_history.json` one-time migration (`*.imported` rename).
- Concurrency: 4-thread parallel write test passes; single guarded connection
  (`check_same_thread=False` + RLock).
- Corruption: a non-SQLite file raises (caller contract) — callers wrap access
  defensively; a corrupt DB does not silently return wrong data.
- **Found & fixed:** lingering worker threads touching the store after
  `close()` raised `ProgrammingError`; store now guards with a `_closed` flag
  and a `_ClosedCursor` no-op.
- **Verified:** progress ticks never hit the database (0 writes during an
  engine download; queue path writes only on state transitions, ≤ ~6 per task).
- Crash-mid-write: WAL + atomic transactions + subprocess `os._exit` test pass.

---

## Phase D — Queue Manager
**PASS** (queue unit tests + integration)

- FIFO order, max-concurrent gating, automatic start of the next task when a
  slot frees.
- Add / remove / pause / resume / cancel / retry / start-task / clear-finished /
  clear-failed / clear-completed / retry-failed / set-priority / move-up /
  move-down; queue order + priority persisted across restarts.
- Edge cases verified by tests: removing an active task (worker emits
  `removed`), removing a queued task, pausing the queue (`pause_all` blocks new
  starts), cancelling an active task, retrying a failed task (one-shot failure
  then success), changing priority while downloads are active, shutdown while
  the queue is active.
- Per-task control: pause/cancel is isolated per task (verified by an engine
  test where cancelling task A does not affect concurrent task B).
- **Found & fixed:** `shutdown(cancel=True)` marked in-flight tasks CANCELLED
  (terminal), so closing the app was not resumable. Added
  `TaskManager.prepare_for_exit()` (pause + persist) and the GUI now uses it on
  window close. Explicit user cancels still record CANCELLED.
- **Found & fixed:** engine status callbacks used `"DOWNLOADING"` but the enum
  is case-sensitive; transitions were silently dropped. Now normalized via
  `normalize_status`.

---

## Phase E — Crash Recovery / Restart Resume
**PASS** (subprocess crash tests + integration)

- Tested with a **true subprocess crash** (`os._exit(0)`) mid-download: the
  child wrote partial bytes, died abruptly; the parent reopened the store,
  found the task restored to `QUEUED`, resumed, and completed with a correct
  final file.
- Multiple unfinished tasks restored; paused tasks restored to `QUEUED`;
  missing destination folders handled with a clear error.
- Already-complete files (crash between merge and rename) are detected at
  startup and recorded as completed.
- `resume_on_startup` config auto-continues restored downloads.
- Corrupted/missing partial state is handled by the engine on next run
  (re-probe + rebuild parts); segment validation is not duplicated in the
  recovery scanner (no second engine).

---

## Phase F — Smart Retry + Network Recovery + Friendly Errors
**PASS** (engine tests)

- `core/errors.py` maps exceptions/status codes to user-facing messages
  (404, 401/403, 5xx, disk full, permission denied, connection lost, timeout,
  SSL). Requests exceptions are checked before the `OSError` branch (a
  `requests.ConnectionError` also subclasses `OSError`).
- Exponential backoff + jitter (pre-existing, retained), `max_retries`
  configurable, `_RETRYABLE_STATUS` classification.
- `DownloadController.last_error` is set at every failure point and propagated
  to `Task.error`; `retry_count` tracked per task.
- Verified: transient 500 → retry → success; 404 → friendly error; a failing
  log callback can no longer fail the transfer (`_safe_print`).

---

## Phase G — Scheduler
**PASS** (9 scheduler tests)

- `core/scheduler.py`: daemon thread applies a start-time / stop-time queue
  gate and a night speed cap (23:00→07:00 example); handles midnight wrap.
- `core/throttle.py`: `set_schedule_override()` takes precedence over
  `max_speed_bps` without mutating the user setting.
- `ui/common.py`: `set_scheduler_gate()` blocks new starts.
- `ui/api.py`: scheduler started on API init, stopped on shutdown, speed
  override applied.
- Config fields added: `scheduler_enabled`, `schedule_start_time`,
  `schedule_stop_time`, `night_speed_limit_bps`, `night_start_time`,
  `night_end_time`.
- UI controls for these settings are part of Phase I (backend verified here).

---

## Regression Test
**PASS** (63 automated tests total)

Verified working (unchanged behavior confirmed by tests / import checks):
- Normal multi-threaded download, Range, non-Range fallback (incl. fresh-process
  first-download regression), resume, pause, cancel.
- Batch download (engine wrapper + URL list import JSON/CSV/TXT), dldm:// URL
  decoding, live-server authenticated relay + 401 rejection.
- Checksum verification (pass + mismatch), cookie-gated server (403 without
  cookie, success with `Cookie` header).
- SSRF blocking (loopback rejected), speed throttling (measured wall time),
  per-task cancel isolation.
- CLI (`d.py --help`), TUI banner/menu, `--gui` backend (Api construct +
  shutdown), full module imports.

---

## Performance Check
**PASS**

- No thread leaks: 12 concurrent downloads returned live thread count to
  baseline.
- No DB writes on progress ticks; writes only on state transitions.
- No connection leaks: sessions are closed on shutdown; responses consumed or
  explicitly closed.
- No UI-thread blocking: all network + analysis runs on worker threads; events
  coalesced (120 ms progress, 200 ms poll, 2 s stats).
- Known bounded consideration: the shared engine thread pool retains up to
  `num_threads` workers for process lifetime (intended reuse, bounded ≤ 64).

---

## Critical Bugs Found (all fixed)
1. **Engine hang (pre-existing, not introduced):** `DownloadContext._pause_blocker`
   defaulted to an *unset* Event, so the first single-thread (non-Range)
   download in a fresh process blocked forever in `wait_if_paused()`.
   → `core/context.py` now sets it by default.
2. **Log callback could fail a download (pre-existing):** an unguarded
   `console_print` (e.g. UnicodeEncodeError printing `✓` on a cp1252 console)
   raised inside the download thread and failed the task.
   → `DownloadController._print` now guards all callbacks.
3. **Retry race (new code):** a finishing worker could overwrite a re-queued
   task's state.
   → control-identity guard in the worker `finally`.
4. **Close-while-active lost resumability (new code):** `shutdown(cancel=True)`
   marked in-flight tasks CANCELLED (terminal).
   → `prepare_for_exit()` (pause + persist) used by the GUI on window close.
5. **Status-callback case mismatch (new code):** engine emitted "DOWNLOADING"
   vs enum "Downloading".
   → `normalize_status` in `_task_status_cb`.
6. **Store write-after-close (new code):** `ProgrammingError` from lingering
   workers.
   → `_closed` guard + `_ClosedCursor`.
7. **`DownloadTask(status="...")` with a string (robustness):** broke `to_dict`.
   → `__post_init__` normalization.

## Potential Bugs / Considerations (no action taken)
- `requirements.txt` still omits `pyfiglet` (used by `ui/menu.py`) — a fresh
  install without it crashes the TUI at import. **Needs a fix** (Phase I).
- `docs/AUDIT.md` P14: the extension `manifest.json` hardcodes port 6868; the
  UI should surface that regenerating the extension is required after a port
  change.
- Shared thread pool retains up to `num_threads` workers for process lifetime
  (bounded, intended).
- History entries for cancelled tasks keep the "Cancelled" status; the UI
  currently only distinguishes Complete/Failed (Phase I).

## Required Fixes
- Add `pyfiglet` (and `psutil` as optional) to `requirements.txt`.
- Phase I: frontend status maps for the new states (Analyzing, Starting,
  Merging, Verifying, Cancelled), history filters/actions (open file / copy
  path / redownload / duration / avg speed), settings UI for scheduler,
  categories, clipboard monitor, `resume_on_startup`, speed presets, and the
  download-details panel.
- Phase H: category directories wiring (config exists; UI + routing pending).

## Files Changed
- `core/task.py` (new) — DownloadTask + state machine
- `core/store.py` (new) — SQLite task/history/order store
- `core/control.py` (new) — per-task TaskControl / TaskCancelled
- `core/analyzer.py` (new) — smart URL analysis (media bits removed)
- `core/errors.py` (new) — friendly error messages
- `core/scheduler.py` (new) — queue gate + night speed scheduler
- `core/context.py` — pause-blocker default fix (critical)
- `core/download.py` — per-task control, pre_analysis, status_callback,
  last_error, safe_print
- `core/probe.py` — refactored to `_probe_impl` + `probe_with_headers`
- `core/throttle.py` — scheduler speed override
- `config/settings.py` — scheduler/categories/clipboard/resume config fields
- `ui/common.py` — queue rewritten on DownloadTask + TaskStore + prepare_for_exit
- `ui/legacy.py` — analyze/download runner
- `ui/api.py` — scheduler wiring, new queue ops, delete_file fix, config to manager
- `docs/AUDIT.md` — media phase explicitly out of scope
- `tests/*` — 63 automated tests (task/store/queue/scheduler/engine/integration)

## Recommended Next Phase
Phase H (Categories) + Phase I (UI integration) together, in that order:

1. **Phase H** — wire per-category destination directories (config field
   `category_dirs` already exists) into the queue/engine destination selection,
   and enrich history entries (duration, avg speed, category — already stored).
2. **Phase I** — frontend updates: status maps for new states; history filters,
   sort, redownload/open/copy-path actions; Settings page sections for
   scheduler, categories, clipboard monitor, `resume_on_startup`, speed
   presets; a download details view; then fix `requirements.txt` (pyfiglet).
3. Then Phase J (browser integration improvements) and Phase L (final
   packaging/build/clean-install verification).

Phase K (Media/Player/Streaming) remains permanently out of scope for this
project.

---

## Addendum — Phase H (Categories) & Phase I (UI integration) complete

Implemented after the verification freeze:

### Phase H — Categories + enriched history
- `detect_category()` is now configurable via `category_extensions`
  (extensions are *added* to the built-in per-category map).
- Per-category destination routing via `category_dirs` +
  `AppConfig.resolve_category_dir()`; applied in `Api.add_download` /
  `add_batch` and auto-assigned on add (`auto_categorize`).
- History entries now include `category`, `duration`, and `avg_speed`
  (final average is computed at completion instead of being zeroed).
- `category_dirs` / `category_extensions` survive config round-trips as dicts.

### Phase I — UI integration
- **Statuses**: new lifecycle states (Analyzing, Starting, Merging, Verifying,
  Cancelled) rendered across status maps, filter chips, counts, badge, row
  actions and context menu; Pause/Resume/Cancel/Retry for the correct states;
  Move up / Move down in the row menu.
- **History page**: status filter chips, category pill, duration + average
  speed meta, and per-row actions — Open file, Open folder, Copy path,
  Redownload, Remove entry (`open_file_from_history`, `remove_history_entry`).
- **Settings page**: new sections for Scheduler (start/stop times, night cap +
  window), Startup & clipboard (resume_on_startup, start_minimized,
  clipboard_monitor, clipboard_autostart), Categories (auto-detect + per-
  category folder editor + custom-extension JSON editor), quick speed presets
  (256 KB/s … 10 MB/s), and Language (en/fa stored).
- **Clipboard monitoring**: `core/clipboard.py` polls the clipboard (opt-in)
  and either auto-downloads (`clipboard_autostart`) or shows a non-intrusive
  Download / Ignore prompt.
- **`start_minimized`** wired into the GUI launch.
- **requirements.txt**: `pyfiglet` (TUI banner) and optional `psutil` added.
- Backend additions: `open_file`, `redownload`, `scheduler_status`,
  `clipboard_status`, `move_task`, `set_priority`, `retry_failed`,
  `clear_failed`, `clear_completed`, `remove_history_entry`.

Test count: **74 automated tests** (added `test_categories.py` — 7,
`test_clipboard.py` — 4).

### Phase J — Browser integration improvements
- Live server now exposes `POST /download_many` (`{"urls": [...]}`) with
  per-URL SSRF validation, deduplication, and accepted/rejected counts.
- Extension gains two context menus: **Download selected links** (extracts
  http(s) URLs from the selection) and **Download all links on this page**
  (scans anchors and filters to file-extension links). Batch sends go to
  `/download_many` with the token; the popup accepts multiple URLs (one per
  line) and sends them as a batch.
- `chrome_extension/` copy resynced from `extension/`.
- Added `tests/test_browser.py` (3 tests) covering the batch endpoint,
  deduplication, single endpoint and auth rejection.

Test count: **77 automated tests** total.

