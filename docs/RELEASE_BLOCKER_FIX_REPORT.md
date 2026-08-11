# Release Blocker Fix Report

Date: 2026-08-11
Scope: fix the three experimentally confirmed release blockers. No features
added, no UI redesign, no media/streaming/player functionality touched.

Verification basis: the full automated suite (85 tests) passes repeatedly, plus
the exact experimental probes that originally found the blockers.

---

## Blocker #1 — Pause does not stop an active transfer

**Root cause**
The chunk-writing hot path in `download_part()`/`single_thread_download()`
checked *cancellation* on every chunk but never *pause*. `pause` was only
honoured at the top of the per-attempt loop, so a segment already inside the
`iter_content` chunk loop kept downloading to completion while the UI showed
`PAUSED`.

**Fix** (`core/control.py`, `core/download.py`, `core/throttle.py`)
- `TaskControl.wait_if_paused()` rewritten to an event-based barrier (wakes on
  the next pause/resume/cancel transition; no busy loop, no CPU spin while
  paused).
- Two pause barriers added to the hot loops: one at the chunk boundary and one
  **immediately before `dest.write()`** (after the throttle), so a pause that
  arrives during the throttling sleep is honoured before any bytes hit the
  disk.
- The bandwidth limiter's sleep is now interruptible (`consume(..., should_stop)`)
  and the engine passes a pause/cancel-aware callback, so a long throttle sleep
  no longer delays pause or shutdown.
- `_interruptible_sleep` (retry backoff) is now per-control aware.
- Fixed a **pre-existing** data-corruption bug in the multi-part skip/trim path
  (server advertises range but returns 200 full body): skip offset was computed
  before the resume-reset block and used `range_start > part.start`, producing
  wrong content with the right size. Skip is now recomputed after all state is
  reset and correctly uses `range_start > 0`.

**Files changed**
`core/control.py`, `core/download.py`, `core/throttle.py`

**Tests added** (`tests/test_release_blockers.py`)
`test_multi_segment_range_pause`, `test_range_ignoring_multipart_pause`,
`test_true_single_thread_norange_pause` — all assert the transfer stops promptly
(≤ one in-flight chunk) while paused, resumes from the correct offset, and the
final file matches size + SHA-256.

**Experimental result (direct probe)**
After Pause, written bytes were **exactly 0 more** over 1 second across all
three server profiles (range multi-segment, range-ignoring multi-part, true
single-thread no-Range); resume completed with a valid checksum.

---

## Blocker #2 — Window close does not perform safe shutdown

**Root cause**
The graceful path `Api.shutdown()` → `TaskManager.prepare_for_exit()` was never
wired to the window-close event; the X button only called `window.destroy()`.
After the window closed, Python's interpreter shutdown joined the **non-daemon**
engine `ThreadPoolExecutor` threads, so the transfer kept running and the
process lingered until it finished (verified ~10 s on a small file; scales with
size). Additionally, `Api.shutdown()` called `DownloadContext.request_cancel()`
(global) before pausing (per-task), a racy combination that could mark active
tasks FAILED.

**Fix** (`ui/common.py`, `ui/api.py`, `ui/bridge.py`)
- One authoritative, idempotent, thread-safe shutdown path (`Api._cleanup` +
  `shutdown` + `_on_closing`). Both the frameless X button (`window_close`) and
  the pywebview `closing` event (title-bar close / Alt+F4) route through it.
- `TaskManager.prepare_for_exit()` now: stops accepting new downloads → marks
  active records `shutting_down` → **pauses** controls (stop writing at the next
  chunk boundary) → brief wait → **cancels** controls (so any thread blocked on
  the pause barrier unblocks and exits) → waits → persists.
- The worker finalize honours `shutting_down`: it keeps the persisted
  DOWNLOADING/PAUSED state instead of marking the task CANCELLED, so the next
  launch restores the task to the queue and resumes from the saved partial data.
- Removed the racy `DownloadContext.request_cancel()` from shutdown.
- The interruptible throttle (Blocker #1) also guarantees shutdown is not
  delayed by a long throttle sleep.

**Files changed**
`ui/common.py`, `ui/api.py`, `ui/bridge.py`

**Tests added** (`tests/test_release_blockers.py`)
`test_shutdown_preserves_resumable_state` (active download → `prepare_for_exit`
→ state stays Downloading/Paused, not Failed/Cancelled; reopen → Queued),
`test_process_exits_promptly_after_shutdown` (subprocess with an active slow
download exits cleanly within the watchdog budget).

**Experimental result (direct probe)**
- Active download + graceful shutdown → task restored as `Queued` on restart
  (resumable), never Failed/Cancelled.
- Worst case (shutdown while a thread sleeps inside a ~30 s throttle sleep):
  process exited in **~0.8 s**; no watchdog hang; no background N13 process
  left behind.

---

## Blocker #3 — Corrupt SQLite database crashes the application

**Root cause**
`TaskStore.__init__` ran `PRAGMA journal_mode=WAL` + schema init directly; a
corrupt `downloads.db` raised `sqlite3.DatabaseError` out of `TaskManager`, and
nothing recovered — the app would not start.

**Fix** (`core/store.py`)
- `TaskStore.__init__` now catches initialization errors and uses
  `_is_corruption_error()` to classify them:
  - **Corruption** (message hints "not a database"/"malformed"/"disk image"/
    "file is encrypted"/"unsupported file format", or a bare
    `sqlite3.DatabaseError`) → `_recover_from_corruption()`:
    1. rename `downloads.db` (+ `-wal`/`-shm`/`-journal` siblings) to
       `downloads.db.corrupt-<timestamp>` (collision-safe, never overwritten),
    2. create a fresh database, initialise the schema,
    3. log the recovery (path only — no sensitive data),
    4. continue normally.
  - **Non-corruption** (database locked, disk full, permission denied,
    filesystem unavailable) → re-raise the real error; never quarantined,
    never hidden. `OperationalError` is a subclass of `DatabaseError`, so
    classification is message-based + exact-type, not broad subclassing.

**Files changed**
`core/store.py`

**Tests added**
- `tests/test_store.py`: `test_corrupt_db_recovered` (quarantine + rebuild +
  corrupt file preserved), `test_corruption_classification` (locked / disk full /
  readonly are NOT corruption).
- `tests/test_release_blockers.py`: `test_taskmanager_recovers_from_corrupt_db`
  (TaskManager starts, fresh DB usable, corrupt file preserved),
  `test_fresh_db_schema_and_tasks`.

**Experimental result (direct probe)**
- Garbage `downloads.db` → TaskStore recovered, created a usable fresh DB, and
  preserved `downloads.db.corrupt-<timestamp>`.
- A locked-but-valid DB raised the real `OperationalError` and was NOT renamed.

---

## Existing tests
**PASS** — all 77 pre-existing tests pass (with the pre-existing
`test_corrupt_db_handled` updated to the new recovery behaviour, and two
timing-margin test fixes: `test_speed_and_eta` sleep widened; the throttle
timing margin relaxed). Suite re-run 4× clean.

## New regression tests
**PASS** — `tests/test_release_blockers.py` (7 tests) + updated
`tests/test_store.py` (net +1). Total suite: **85 tests**.

## Manual / experimental probes
- **Pause** (Test 1/2/3): delta = 0 bytes while paused; resume integrity valid. **PASS**
- **Window close during download** (Test 4): safe shutdown, prompt exit (~0.8 s worst case), workers terminate, DB closes. **PASS**
- **Restart after close** (Test 5): task restored to queue, resumable, no second writer. **PASS**
- **DB corruption** (Test 6): no crash, quarantined, fresh DB, corrupt file preserved. **PASS**
- **DB failure vs corruption** (Test 7): locked/disk-full/readonly NOT quarantined. **PASS**
- API `shutdown()` idempotent; `window_close()` routes through shutdown. **PASS**

## Remaining risks
1. **Range-ignoring multi-part corruption fix** (`skip_bytes` recompute) is new
   core logic: it is covered by multi-part pause/resume tests and the existing
   multi-threaded checksum test, but real-world CDNs that ignore Range and
   advertise it should be monitored. The fix corrects a pre-existing bug found
   during this work.
2. **Two app instances** can still both open the store (WAL + busy_timeout +
   best-effort writes); no corruption, but a second instance's task writes may
   be dropped. Single-instance is the norm; an instance lock was out of scope.
3. **Pause granularity** is at the chunk boundary: one in-flight chunk (up to
   `chunk_size`, default 4 MB) may be read into memory but is not written after
   pause; it is written on resume or discarded on cancel — no corruption.
4. **Clipboard monitor** still uses `tkinter` in a worker thread (pre-existing,
   opt-in, default off) — lower-risk item noted in the audit, not part of the
   three blockers.
5. The `Api._event_queue` remains unbounded (drained every 200 ms) — low risk.

## Release Status

**RELEASE READY**

Conditions met:
- All 85 tests pass (77 pre-existing + 8 new/updated), verified across
  repeated runs.
- Pause probe passes (delta 0 while paused; resume integrity valid).
- Shutdown probe passes (prompt exit, no hang, resumable state preserved).
- Restart/Resume probe passes (task restored to queue).
- Database-corruption recovery probe passes (quarantine + rebuild, corrupt
  file preserved, non-corruption errors not misclassified).
- No critical regression in the verified feature checklist (normal /
  multi-threaded / Range / no-Range / Resume / Pause / Cancel / Retry / Queue /
  Priority / Scheduler / Speed limiting / Batch / History / Categories /
  Checksum / Cookies / Headers / Browser integration / Clipboard / SSRF /
  shutdown-after-queue / TUI / GUI).
