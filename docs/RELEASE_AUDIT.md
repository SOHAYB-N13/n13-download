# N13 Download Manager — Release Audit

Date: 2026-08-10
Scope: release-blocking issues only. No features added, no code changed during
this audit. Every finding below was verified empirically (probes reproduced the
behaviour), not just read from the source.

Legend: **RB** = release-blocking (must fix before release) · **LR** = lower
risk / conditional.

**STATUS (2026-08-11):** all three RB blockers have been fixed and verified —
see `docs/RELEASE_BLOCKER_FIX_REPORT.md`.

---

## 1. [RB] Pause does not actually stop an in-flight download

**Verified:** a download at 1 MB/s with 2 segments was paused; 1 second later the
"paused" task had downloaded another 2.6 MB and completed. The UI shows
`Paused`, the engine keeps writing bytes.

**Root cause:** in `core/download.py`, `download_part()` checks **cancellation**
in the hot chunk loop
(`if cancel_event.is_set() or self._ctl_cancelled(control): return False`) but
**never checks pause**. `pause` is only honoured at the top of the per-attempt
loop (`_ctl_wait(control)`). Once a segment is inside the `iter_content` chunk
loop, `control.pause()` has no effect until that segment finishes. The
single-thread path (`single_thread_download`) has the same gap.

**Impact:** for a single-segment file (e.g. non-Range server, or
`num_threads=1`), Pause does nothing at all. For multi-segment files it only
takes effect between segments. This is a core advertised feature
(pause/resume) that is broken mid-transfer.

**Fix direction:** add a pause barrier inside the hot chunk loop (e.g. call
`control.wait_if_paused()` before writing each chunk when a control is present,
mirroring the global `DownloadContext.wait_if_paused()` path).

---

## 2. [RB] Closing the window does not stop downloads — the process lingers (effectively "won't close")

**Verified:** with a 256 KB/s transfer in progress, closing the app took ~10 s
to exit and the engine pool threads (`n13-dl_0/1`, non-daemon) kept downloading
until the transfer finished. With a large/slow file this becomes minutes/hours;
with a stalled server it can block until the 120 s socket timeout.

**Root cause:** the graceful exit path `Api.shutdown()` →
`TaskManager.prepare_for_exit()` is **never called**. The window X button only
runs `Api.window_close()` → `self._window.destroy()` (confirmed: no
`events.closed`/`closing` handler in `ui/bridge.py`, no `beforeunload`/
`shutdown()` call in the frontend). After the window is destroyed,
`webview.start()` returns and Python's interpreter shutdown joins all
**non-daemon** `ThreadPoolExecutor` threads, so the transfer is allowed to run
to completion before the process exits.

**Impact:** the user closes the app believing it is stopped; it keeps
downloading in the background. Relaunching starts a **second instance** against
the same store/part files (risk of `WinError 32` part-file conflicts and
duplicate downloads). This violates the "shutdown during download must be safe
and resumable" requirement.

**Fix direction:** wire `Api.shutdown()` to the window close event (pywebview
`window.events.closing`), make `prepare_for_exit()` also **cancel** any still
blocked controls after a short grace period so paused workers cannot hang exit,
and/or create the engine pool with daemon threads as a safety net.

---

## 3. [RB] A corrupt SQLite database prevents the application from starting

**Verified:** writing garbage to `saved_links/downloads.db` and constructing
`TaskManager` raises
`sqlite3.DatabaseError: file is not a database`; nothing catches it, so
`ui/api.py` → `ui/bridge.py` → `d.py --gui` crash with a traceback.

**Root cause:** `TaskStore.__init__` performs `PRAGMA journal_mode=WAL` +
`executescript(_SCHEMA)` directly; a corrupt file raises `DatabaseError` and
there is no fallback (rename-and-recreate). No other component wraps `Api`/`
TaskManager` construction.

**Impact:** one bad byte in the DB (crash during a WAL checkpoint, disk error,
partial copy, or an interrupted migration) bricks the whole app with no user
recovery — a guaranteed-support-horror on release.

**Fix direction:** catch `sqlite3.DatabaseError` (and `OperationalError` on the
`journal_mode` pragma) in `TaskStore.__init__`; quarantine the bad file
(`downloads.db.corrupt-<ts>`) and rebuild from scratch, then surface a warning.

---

## 4. [LR] Shutdown path is racy (today it is dead code)

`Api.shutdown()` calls `DownloadContext.request_cancel()` (sets the **global**
cancel event) then `prepare_for_exit()` (sets **per-task** `control.pause()`).
Because parts check both the global cancel and the per-task control, the outcome
for an active download is timing-dependent: it can end `FAILED` (global cancel
wins) or stay `Downloading`-blocked (pause wins). Both are wrong for a graceful
exit. Currently unreachable (see #2) but it must be fixed together with #2 or it
will reintroduce the bug the moment the graceful path is wired up.

---

## 5. [LR] Clipboard monitor creates Tk roots in a worker thread

`core/clipboard.py` creates and destroys a `tkinter.Tk()` every poll (3 s) in a
daemon thread. tkinter is not thread-safe, and repeatedly instantiating Tcl
interpreters off the main thread on Windows is a known source of instability.
The feature is **opt-in** (default off). `pywin32` is already installed
(transitive via browser-cookie3), so `win32clipboard` is the safer Windows
implementation.

---

## 6. [LR] Unbounded API event queue

`Api._event_queue` is a `queue.Queue` with no max size; `_log` pushes on every
engine print. The frontend drains it every 200 ms, so in normal use it stays
empty. Only pathological log flooding (e.g. a long retry storm) could grow it.
Non-blocking.

---

## 7. [LR] Second app instance can silently lose task writes

Two instances can both open `downloads.db` (WAL + `busy_timeout=30000`); on lock
contention `TaskStore._execute/save_task` swallow `OperationalError` and drop the
write. No corruption, but instance B's state can be lost. The store also has no
"already running" lock to prevent a second instance. Normal usage is
single-instance.

---

## 8. [LR] Session rebuilt before every download

`LegacyDownloadRunner._prepare()` calls `session.configure()` (which closes and
rebuilds the whole connection pool) before each run. No leak — just needless
pool churn for batch/queue workloads.

---

## Not found (clean)

- **Memory leaks:** all long-lived structures are bounded (history ≤ 1000,
  logs ≤ 800, Sparkline fixed window, SpeedTracker deque bounded, listener set
  replaced per run).
- **Thread leaks:** a 12-download stress test returned the live thread count to
  baseline; worker threads are daemon and exit after finalize.
- **Deadlocks:** single lock ordering (manager → store); the store never calls
  back into the manager; worker/observer emission happens outside locks; no
  join-while-holding-lock.
- **Connection leaks:** responses use `with` or explicit `close()`; HEAD
  responses are returned to the pool via refcount-GC on CPython; live-server
  handlers are HTTP/1.0 and daemon.
- **File corruption:** `.part` → `.merging` → atomic rename is sound; resumed
  offsets are always re-derived from `stat().st_size`, so interrupted writes
  (even unflushed 8 MB buffers) resume correctly; `.dlstate`/config writes are
  atomic temp+rename.

---

## Summary — release-blocking (must fix)

| # | Issue | Evidence |
|---|---|---|
| 1 | Pause does not stop in-flight segments | Bytes kept downloading while paused |
| 2 | Closing the window leaves the process running until the transfer ends; graceful shutdown is unwired | ~10 s linger; `Api.shutdown` never called |
| 3 | Corrupt `downloads.db` crashes the app at startup with no recovery | `DatabaseError` propagates out of `TaskManager` |

Recommended order: fix #1 (pause barrier in the hot loop) → fix #2 (wire window
close → graceful shutdown that cancels blocked workers) → fix #3 (quarantine +
rebuild corrupt DB). Then re-run the full suite (77 tests) plus the shutdown/
pause probes.
