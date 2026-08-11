# N13 Download Manager — Phase 1 Technical Audit

Date: 2026-08-10
Scope: full repository inspection (no changes made to application code).

---

## 1. Architecture

The project is a Python 3.10+ download manager with **two front-ends over one
engine**:

```
d.py  (CLI entry point, argparse + signal handlers)
 ├── ui.menu            Terminal interactive menu (Rich TUI)
 ├── ui.bridge          pywebview GUI launcher → ui.frontend (HTML/CSS/JS)
 │      └── ui.api      pywebview JS bridge (exposed to JS)
 │            └── ui.common.TaskManager   (thread-safe queue, shared state)
 │                  └── ui.legacy.LegacyDownloadRunner  (adapter)
 │                        └── core.download.DownloadController  ★ ENGINE
 │                              ├── core.probe    URL probing (HEAD→Range GET)
 │                              ├── core.parts    part layout / remapping
 │                              ├── core.merge    part→final merge (.merging)
 │                              ├── core.state    per-download .dlstate JSON
 │                              ├── core.context  global cancel/pause context
 │                              ├── core.session  requests Session + TCP tuning
 │                              ├── core.security SSRF guards
 │                              ├── core.throttle global bandwidth limiter
 │                              ├── core.speed    SpeedTracker (rolling avg)
 │                              ├── core.cookies  cookies.txt / browser / raw
 │                              └── core.utils    filenames, checksums, headers
 ├── batch.sources    URL list import/export (JSON/CSV/TXT)
 ├── batch.pattern    pattern scan + batch download + batch resume state
 └── browser.protocol dldm:// registration + extension generation
      ├── browser.dldm_handler  OS protocol handler (temp-file handoff)
      ├── browser.live_server   authenticated loopback HTTP relay
      └── browser.icons         generated PNG icons
```

Python modules per layer:

| Layer | Files |
| --- | --- |
| Entry | `d.py`, `__init__.py` |
| Config | `config/settings.py`, `config/loader.py` |
| Core engine | `core/{download,parts,merge,state,context,session,security,probe,throttle,speed,cookies,utils}.py` |
| UI (Python) | `ui/{bridge,api,common,legacy,menu,prompts,progress}.py` |
| UI (frontend) | `ui/frontend/index.html`, `js/{app,api,components,utils}.js`, `css/main.css` |
| Batch | `batch/{sources,pattern}.py` |
| Browser | `browser/{protocol,live_server,dldm_handler,icons}.py` |
| Extension | `extension/*` (template), `chrome_extension/*` (generated copy) |

Dependencies (`requirements.txt`): `requests`, `urllib3`, `rich`, `colorama`,
`pywebview`, `browser-cookie3`. Runtime also uses stdlib `http.server`,
`tkinter`, `sqlite3` (not yet), `winreg` (optional), `pyfiglet` (used by
`ui/menu.py` but **missing from requirements.txt**), `psutil` (optional,
dashboard stats).

---

## 2. Download Engine

`core/download.py` — `DownloadController.download_file()` is the orchestrator.

Flow:
1. `wait_for_schedule()` — wait until `config.schedule_time` if set.
2. `probe_url()` — HEAD first, fall back to `Range: bytes=0-0` GET, then plain
   GET. Returns `(reachable, total_size, supports_range, filename, error)`.
3. Compute `unique_filepath` (never overwrites), build `.dlstate` path.
4. If file already complete → short-circuit success.
5. If server does **not** support ranges → `single_thread_download()` into a
   `.tmp` file, then `safe_rename`.
6. Otherwise multi-part:
   - Load/validate `.dlstate`; resume when URL+size match and part paths stay
     inside the directory; remap parts when thread count changed.
   - `DownloadContext.begin(state, url, size, parts, threads)`.
   - Submit part downloads to a **shared** `ThreadPoolExecutor`.
   - Merge via `core/merge.py` into a `.merging` staging file, verify size,
     verify checksum (optional), delete state, rename parts away.

Key mechanics:
- **Range requests** per part; `200` responses trigger skip-logic and a
  discard/restart of that part (server ignored Range).
- **Retry** per part with exponential backoff + jitter
  (`_retry_delay`), capped; `_RETRYABLE_STATUS` = {408, 425, 429, 500, 502,
  503, 504}.
- **Throttling** — every chunk passes through the global `BandwidthLimiter`
  (token bucket) before write.
- **Progress** — local byte accumulation, flush to shared dict every 256 KB,
  external callback throttled to ~20 Hz.
- **Cancellation** — lock-free `threading.Event.is_set()` in the hot loop.
- **Pause** — `DownloadContext.wait_if_paused()` blocks part threads on a
  `_pause_blocker` event.

Optimisation notes already documented in the file: socket buffers (4 MB
`SO_RCVBUF`), lock-free cancel, progress batching, shared thread pool, content
negotiation via urllib3.

### Engine gaps (audit findings)

1. **Part files are never "safe" for single-thread vs multi-path.** The
   single-thread path writes `.tmp` and renames; the multi-path writes
   `.partN` files and only renames the merged result. No single `movie.mp4.part`
   convention; completion is only the final merge+rename, which is correct
   (partial files never appear completed), but there is no explicit
   "crash-safe" partial file beyond this.
2. **DownloadContext is process-global and serialised.** `LegacyDownloadRunner`
   uses a `_ctx_lock` so only **one** download at a time can own the shared
   cancel/pause context. `max_concurrent > 1` therefore means multiple tasks
   run, but they all share one cancel event — a cancel on task A also cancels
   task B (see §4, concurrency gap).
3. **No automatic resume on startup** for engine-owned tasks: state is only
   loaded when the same URL is re-downloaded manually.
4. **Probe does not capture** `ETag`, `Last-Modified`, `Accept-Ranges`
   separately, auth-required status, or content-type for later category
   routing — `probe_url` returns only 5 fields.
5. **`verify_size` compares `Content-Length`**; for chunked/unknown length the
   total stays 0 and size verification is skipped.
6. **No disk-full / permission detection** — surfaced as generic `OSError` to
   the retry classifier, which may then retry a non-transient error.

---

## 3. Threading Model

- **Shared module-level `ThreadPoolExecutor`** (`_get_shared_pool`) reused
  across downloads; resized (rebuilt) when the worker count grows. Worker
  threads named `n13-dl-*`.
- **Per-task worker thread** in `ui/common.py` (`n13-task-<id>`) launched by
  the `TaskManager`, daemonised.
- **Context-monitor thread** per run (`n13-ctx-monitor-<id>`) in
  `ui/legacy.py` that bridges `TaskControl` → `DownloadContext` every 0.5 s.
- **Live-server threads**: `serve_forever` + queue worker, both daemon.
- **UI event pump**: JS polls `poll_events()` every 200 ms; stats every 2 s.

Concurrency gaps (Phase 21 targets):
1. **One global DownloadContext** means per-task pause/cancel is not actually
   isolated. `pause_task(B)` while A downloads pauses **both** — the monitor
   thread for A sees `DownloadContext.pause()` applied globally.
2. `TaskControl` events are polled on a 0.5 s monitor tick — pause/cancel
   latency can be up to ~0.5 s.
3. The shared pool is replaced under a lock; in-flight futures keep running on
   the old pool, so a resize can briefly exceed `num_threads`.
4. No per-task progress throttling beyond the manager's 120 ms emit interval —
   acceptable, but row updates bypass the "structure change" check and call
   `_updateRow` directly.
5. Deadlock/race risk review needed for: `shutdown()` joining threads outside
   the lock while `_worker`'s `finally` re-acquires it (RLock is used, so
   re-entrant — but the worker decrements `_active` and calls `_start_next`
   after the caller joins; ordering is delicate).

---

## 4. Persistence

Three persistence mechanisms today:

1. **Config** — `~/.config/terminal-download-manager/config.json`
   (`config/loader.py`). Atomic temp+rename, fsync, `0o600` best-effort,
   auto-regenerated live-server token, clamps via `AppConfig.from_dict`.
2. **Per-download state** — `<file>.dlstate` next to the target file
   (`core/state.py`). Atomic temp+rename. Stores url, total_size, num_threads,
   part list (index/start/end/path/done). Loaded only on explicit re-download.
3. **GUI queue/history** — `saved_links/gui_queue.json` and
   `saved_links/gui_history.json` (`ui/common.py`). Atomic writes with fsync.
   `_restore_queue()` reloads non-completed tasks on `TaskManager` start; tasks
   in DOWNLOADING/PAUSED/STOPPING are restored to QUEUED. History capped at
   500 entries.

Gaps:
- **The `.dlstate` and `gui_queue.json` stores are split** and can diverge
  (queue knows completed-bytes, state knows part layout). On restart, the GUI
  restores the task to QUEUED but the *engine* then re-probes and re-loads
  `.dlstate` — this works but there is no unified task DB, no
  created/started/completed timestamps, no per-task retry_count, checksum,
  content-type, server, or supports_range stored in the queue.
- **No migration/versioning** on the JSON stores (forward-compat only by
  ignoring unknown keys in config; queue/history JSON have no schema version).
- Batch resume state lives in `saved_links/batch_resume.json` (separate store).

---

## 5. Queue System

`ui/common.py` `TaskManager` is a solid, thread-safe queue:

- Ordered list `_order` + `dict _tasks` keyed by uuid4 hex.
- `max_concurrent` gate; `_start_next()` starts QUEUED tasks while
  `_active < max_concurrent`.
- Actions: add, start_task, pause/resume/cancel/retry/remove, pause_all/
  resume_all, clear_finished, set_max_concurrent, shutdown.
- Dedupe by (url, directory) for non-terminal states.
- Observer events: added / started / progress / updated / finished / removed.
- Persists non-completed tasks on every transition (atomic).

Gaps:
- **No priority** field, no move-up/move-down (only `start_task` jumps a task
  to the front).
- No "retry failed" / "clear completed only" / "clear failed only" helpers.
- `retry_task` resets `completed = 0` (the engine will re-derive from
  `.dlstate`, but the UI momentarily shows 0).
- No queued-task ordering exposed to the UI beyond creation order.
- State enum (`Queued/Downloading/Paused/Stopping/Stopped/Failed/Complete`) is
  UI-centric and lacks the engine lifecycle (ANALYZING, STARTING, MERGING,
  VERIFYING, CANCELLED, REMOVED).

---

## 6. Browser Integration

Two transport paths, both preserved:

1. **`dldm://` protocol** (`browser/protocol.py` + `browser/dldm_handler.py`):
   registered in `HKCU\Software\Classes\dldm`. The handler decodes the URL
   (up to 5 unquote passes), writes it to a temp file, and spawns
   `python d.py --from-browser --url-file <tmp>` in a new console. Robust to
   `&`, `%`, spaces.
2. **Live Server** (`browser/live_server.py`): `ThreadingHTTPServer` bound to
   127.0.0.1, bearer-token auth via `secrets.compare_digest`, CORS restricted
   to localhost/chrome-extension origins, body capped at 64 KiB, SSRF check on
   every URL, `/download` + `/queue` POST endpoints, `/health` GET. A worker
   drains the queue; `download_callback` may delegate to the GUI.

Chrome extension (`extension/` → `chrome_extension/`): MV3 service worker,
context menus (link/media/page), popup with URL input, `sendToTDM` prefers
Live Server then falls back to the protocol via a throwaway `about:blank` tab.
`token.json` is per-machine and git-ignored.

Gaps:
- Extension host_permissions hardcode port 6868 (`manifest.json`) — changing
  `live_server_port` breaks the extension unless regenerated (acceptable, but
  should be surfaced in UI).
- No "download all links on page" / multi-select batch from the extension yet
  (single URL at a time).
- Live server `_process_queue` downloads **sequentially** with a fresh
  `DownloadController` per item; it does not feed the GUI queue when the GUI is
  running unless `download_callback` is set (GUI sets it in `ui/api.py`).
- Token is truncated to 12 chars in `live_server_status()` — displayed token is
  fine, but the full token is exposed to the frontend for regeneration needs.

---

## 7. GUI Architecture

pywebview (Edge Chromium) + local HTML/CSS/JS:

- `ui/bridge.py` creates a frameless 1440×900 window with `js_api=Api`.
- `ui/api.py` — every public method is callable from JS:
  downloads CRUD, queue ops, settings (get/update), theme prefs, dashboard
  stats, system stats (psutil, optional), browser server control, extension
  generation, protocol registration, window controls, probe/validate, pattern
  scan, file dialogs, history.
- JS bridge (`api.js`) wraps all calls; `app.js` holds state + rendering; event
  loop polls `poll_events()` every 200 ms; components (modal/toast/context
  menu/rows) in `components.js`.
- Frontend is dark-theme-first with accent theming, frameless drag/resize,
  drag-drop URL capture, keyboard shortcuts, sidebar with badges.

Good points: no network calls on the UI thread (all through bridge/worker
threads), coalesced event emission, skeleton loading, capped history rows.

Gaps:
- `Api.delete_file` imports `filename_from_url` from `core.utils`, which does
  **not exist** → the "Delete file" action crashes (ImportError) at runtime.
  (Confirmed: only `get_filename_from_response` exists.)
- History page lacks filters/search-by-status/duration/avg-speed and
  open-file/copy-path/redownload actions.
- No download detail panel (connections, server, avg speed, time elapsed).
- Settings page lacks scheduler, retry-count advanced, categories/custom dirs,
  clipboard monitor toggle, language, startup behaviour.

---

## 8. Error Handling

- Engine classifies exceptions into retryable vs fatal
  (`_is_retryable_exception`); retries with backoff; surfaces part failures
  and lets the user re-run to resume.
- Probe returns structured error strings (SSL/timeout/connection/too many
  redirects/HTTP status/HTML-interstitial).
- `merge_parts` detects missing/short parts and cleans staging.
- `TaskManager._worker` swallows runner exceptions → FAILED state; listeners
  are guarded; persistence errors are swallowed (non-fatal).
- Live server callbacks are guarded so one bad URL can't kill the worker.

Gaps:
- Errors are English-rich-terminal strings (e.g. `[red]...`) mixed with JSON
  `error` fields; no user-friendly, i18n-ready error codes.
- Retry is per **part**; a fatal-for-the-file error (404, auth, disk full) is
  still attempted up to `max_retries` times because the classifier treats some
  cases as transient.
- `single_thread_download` treats any `HTTPError` with `status` outside
  retryable set as fatal — fine — but the message is the raw exception text.
- No "file not found after completion" handling in history (deleted-file case
  crashes nothing today, but there is no detection either).

---

## 9. Security

Already strong:

- SSRF: `core/security.py` blocks private/loopback/link-local/multicast/
  reserved/unspecified IPs, dangerous hostnames (metadata endpoints), non-http
  schemes, embedded URL credentials. DNS resolution is blocking (documented to
  run off the UI thread).
- Live server: loopback-only bind, constant-time token compare, CORS
  whitelist, body size cap, per-request SSRF.
- Cookies/auth: session-level, never logged; token files git-ignored and
  written with restricted permissions.
- Config values for proxy/auth stored plaintext in config.json (acceptable for
  a local tool; flag: password fields are in plaintext config — no OS keychain).

Gaps / review notes:
- No host header / SNI mismatch guard; DNS-rebinding protection is implicit
  (resolve-once) but the connection is to `requests` which re-resolves — a
  DNS-rebinding attacker could bypass the pre-check. (Edge case; consider
  pinning resolved IP.)
- Proxy credentials are embedded into the proxy URL (`get_proxy_dict`) — if
  this dict ever gets logged it would leak credentials; verify nothing logs
  `session.proxies`.
- `--insecure-ssl` gating by env var is good; keep it.

---

## 10. Performance

Already optimised:
- 4 MB `SO_RCVBUF` socket tuning; chunked progress batching (256 KB flush);
  ~20 Hz callback cap; shared thread pool; C-level merge (`shutil.copyfileobj`);
  atomic `os.replace` writes; coalesced UI events (120 ms); polling at 200 ms.

Remaining concerns:
- `_start_stats_polling` runs `Promise.all([getStats, getSystemStats])` every
  2 s — fine.
- `SpeedTracker` acquires a lock per `add()` call per chunk flush — acceptable.
- `get_downloads()` serialises every task snapshot on each poll batch — small.
- `_shared_pool` is module-global and persists for process lifetime (intended).
- The 0.5 s monitor-thread tick adds CPU for each concurrent download; could
  be replaced with a push model.

---

## 11. Existing Problems (consolidated)

P1. **`ui/api.py:187`** — `from core.utils import filename_from_url` is a
    NameError at call time; "Delete file" is broken.
P2. **`ui/menu.py` imports `pyfiglet`** but `requirements.txt` omits it — fresh
    installs crash the TUI (`ModuleNotFoundError`).
P3. **Per-task pause/cancel is not isolated** — a global `DownloadContext`
    serialised by `LegacyDownloadRunner._ctx_lock` makes pause/cancel affect
    other concurrent downloads; `max_concurrent > 1` is semantically wrong.
P4. **No crash-restore across the whole app** — `.dlstate` only restores when
    the same URL is manually re-added; there is no startup scanner that loads
    unfinished tasks, validates partial files, and requeues them.
P5. **No unified DownloadTask model** — the engine and queue each keep their
    own state; fields like priority, retry_count, checksum, content_type,
    server, supports_range, started_at, completed_at are missing or split.
P6. **No categories / per-category directories** — the Add dialog fabricates a
    subfolder from category in JS only; nothing persists the mapping.
P7. **History is minimal** — no duration, avg speed, filters, open-file/copy
    path/redownload; no "file missing" detection.
P8. **No scheduler beyond one `schedule_time`** — no start/stop windows, no
    speed-by-schedule.
P9. **Speed limiter is global-only** — no per-download limit; presets exist
    only as free-form MB/KB parsing in TUI.
P10. **Retry semantics** — retries are per-part and unboundedly many
    (default 15) for some fatal errors; no user-friendly error codes; no
    retry-count stored per task.
P11. **Single `.merging` staging file** — merge into final is crash-safe
    (rename at end) but if the process dies mid-merge the `.merging` file is
    orphaned (cleaned only on next merge attempt).
P12. **No clipboard monitor** (feature request, not present).
P13. **No media detection / play / download&play** (architecturally absent;
    existing WebView is capable).
P14. **Extension hardcodes port 6868** in `manifest.json`.
P15. **`python d.py URL` CLI path** uses `DownloadController` directly and
    **ignores the queue** — fine for CLI, but the GUI queue is the only place
    with persistence of tasks.
P16. **No automated tests** exist in the repository.
P17. **`chrome_extension/` is a generated copy committed to the repo tree**
    (token files git-ignored, but the copy is stale vs `extension/`).

---

## 12. Reusable Components (do not destroy)

| Component | Location | Reuse |
| --- | --- | --- |
| Probe (HEAD→range GET fallback) | `core/probe.py` | Analyze step for DownloadTask |
| Part builder / remapper | `core/parts.py` | Part layout per task |
| Merge (staging + rename) | `core/merge.py` | MERGING phase |
| Per-download state (atomic JSON) | `core/state.py` | Migrate into task DB |
| Token-bucket limiter | `core/throttle.py` | Global + per-download caps |
| SpeedTracker | `core/speed.py` | Speed/ETA per task |
| SSRF guards | `core/security.py` | Every URL entry point |
| Session manager + TCP tuning | `core/session.py` | Engine HTTP layer |
| Filename/headers/checksum utils | `core/utils.py` | Reuse as-is |
| TaskManager queue + observer | `ui/common.py` | Extend with priority/states |
| Rich progress builders | `ui/progress.py` | TUI progress |
| Live server | `browser/live_server.py` | Keep; add multi-URL capture |
| Protocol handler | `browser/protocol.py`, `dldm_handler.py` | Keep as-is |
| Extension | `extension/` | Extend for download-all-links |
| Frontend shell | `ui/frontend/*` | Extend, don't rewrite |
| Atomic JSON write helpers | `config/loader.py`, `ui/common.py` | Reuse pattern |

---

## 13. Recommended Architecture (target)

Introduce a **unified task lifecycle** while keeping the working engine:

```
DownloadTask  (single source of truth, new: core/task.py)
  id, url, filename, destination, total_size, downloaded_size,
  current_speed, average_speed, eta, status(enum), priority,
  created_at, started_at, completed_at, retry_count, error,
  connections, checksum, content_type, server, supports_range

TaskStatus: QUEUED ANALYZING STARTING DOWNLOADING PAUSED MERGING
            VERIFYING COMPLETED FAILED CANCELLED REMOVED

State machine: strict transitions, no scattered booleans.
```

- **Persistence** → `core/store.py` (SQLite) replacing/augmenting
  `.dlstate` + `gui_queue.json`; stores tasks + segments + history; written on
  every transition (debounced); atomically safe.
- **Queue manager** → `core/queue.py` (or extend `ui.common.TaskManager`) with
  priority + move up/down + all the batch ops, driven by max_active.
- **Engine** → `DownloadController` stays the transfer primitive; the task
  layer drives it with per-task cancellation (replace global context with a
  per-task context), per-task limiters, analyzer results, and crash recovery.
- **Recovery** → on startup, scan store for unfinished tasks, validate partial
  files (size per part), detect completed segments, rebuild parts, requeue;
  auto-continue if configured.
- **Modular media** → **EXPLICITLY OUT OF SCOPE.** This project is a download
  manager only.  No video/audio playback, streaming, HLS, DASH, VLC, media
  preview, or "Download & Play" features are implemented or planned.  Media
  type detection is limited to download **categorization** (Videos/Music
  folders) and never to playback.
- **UI** → keep frontend; add Details drawer, History filters/actions,
  Scheduler, Categories, Clipboard monitor, Language/theme settings.

All changes are additive; nothing currently working is removed.

---

## 14. Baseline verification

- All `*.py` compile (`py_compile` OK).
- `import d` and core modules OK in the project venv.
- Confirmed runtime deps present: requests, rich, colorama, pywebview,
  pyfiglet, browser-cookie3, psutil.
- No automated test suite exists (Phase 24 will add one).
