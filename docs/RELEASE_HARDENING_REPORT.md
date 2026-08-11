# N13 Release Hardening Report

Date: 2026-08-11
Scope: single-instance protection, real-world server matrix, pause/shutdown
stress, database failure matrix, event-queue / clipboard / performance audits,
regression, release-build and distribution readiness. No new major features;
no UI redesign; no media/player/streaming functionality.

---

## Automated Tests
**105 / 105 PASS**

Breakdown of the hardening additions (on top of the 85 prior tests):
`tests/test_single_instance.py` (4), `tests/test_network_matrix.py` (8),
`tests/test_stress.py` (6), plus DB failure-matrix cases in
`tests/test_store.py` (read-only + interrupted-initialization, +2).
Verified green across repeated full-suite runs.

## Single Instance
**PASS**

- Windows: session-scoped named mutex (`Local\N13DownloadManager`, ctypes, no
  dependency) held for the process lifetime; second launch is denied and exits
  gracefully with a clear message.
- POSIX fallback: advisory `flock` on a lock file (auto-released on process
  death, so no stale-lock trap).
- URL forwarding: a second `d.py <url>` launch POSTs the URL to the running
  instance's loopback relay with `autostart=true`; the running GUI adds it
  directly to its queue. SSRF validation still runs on the forwarded URL
  (verified: a non-resolving host is rejected, a resolvable public URL is
  queued).
- The GUI now auto-starts the relay (`auto_start_server`, default on) so
  forwarding and browser integration are ready without a manual step.
- Real-process manual test: holder acquires → second process denied → after
  holder exit, a new process acquires. PASS.

## Real-world Server Tests
**PASS** — `tests/test_network_matrix.py`

Range server, true no-Range (single-thread), range-advertising-but-ignoring
(200 full body to ranged requests), redirects (302→206), large file (~9 MB,
4 and 8 threads), slow server (per-chunk delays), connection reset mid-stream
(retry), and a tiny single-chunk file. Every case ends with the correct final
**size and SHA-256 checksum**.

## skip_bytes Validation
**PASS**

The multi-part `skip_bytes` path (server advertises Range but returns 200 full
body) was stress-verified across single-thread, 4-thread, and large-file
profiles with byte-exact checksums — no skipped, duplicated, or gapped bytes,
no missing ranges, correct final size.

## Pause/Resume Stress
**PASS** — `tests/test_stress.py`

5× Pause→Resume cycles per profile (multi-segment Range, range-ignoring
multi-part, true single-thread no-Range), each ending with a valid checksum.
No corruption, no duplicate/missing ranges, no deadlock; the event-based pause
barrier consumes no CPU while paused.

## Shutdown Stress
**PASS** — `tests/test_stress.py`

- Repeated shutdown→restart→resume cycles: the task is always restored as
  `Queued`, never FAILED/CANCELLED.
- Shutdown while the task is PAUSED: worker unblocked, state preserved.
- Shutdown while the engine is stuck retrying a 500: prompt stop, restored.
- Process-exit promptness (worst case, mid-throttle-sleep) verified earlier at
  ~0.8 s with no orphan process/threads.

## Database Failure Matrix
**PASS** — `tests/test_store.py`

Valid DB (works), corrupt DB (quarantine + rebuild, corrupt file preserved),
locked DB (re-raised, not quarantined), read-only DB (re-raised, not
quarantined), disk-full / readonly messages (classified as non-corruption),
interrupted initialization / truncated DB (recovered as corruption).

## Event Queue
**SAFE**

`Api._event_queue` is drained every 200 ms. Production is bounded and
coalesced: task progress is emitted at most every 120 ms per task (~8.3
events/s/task → ≤ ~166/s for 20 tasks → ≤ ~33 events per poll window), and
transition events are emitted once each. No duplicate accumulation. The only
unbounded producer is the log stream, which is throttled by engine retry
backoff. **Left unchanged** (a hard cap, e.g. 50k, would be cheap insurance but
is not required in practice).

## Clipboard
**NEEDS FUTURE REFACTOR (optional feature, off by default)**

The monitor creates/destroys a `tkinter.Tk()` in a daemon worker thread every
poll (3 s). tkinter is not thread-safe on Windows and repeated Tcl interpreter
init off the main thread is fragile (message-pump / resource churn). It is
opt-in (`clipboard_monitor`, default off), so it does not block this release.
**Recommended future fix:** on Windows read the clipboard with
`win32clipboard` (pywin32 is already a transitive dependency) or a small
`ctypes` wrapper (`OpenClipboard`/`GetClipboardData`), keeping tkinter only as a
non-Windows fallback — no Tk in worker threads.

## Performance
Results (measured on this machine, loopback server):

| Scenario | CPU | RSS | Threads |
|---|---|---|---|
| Idle (queue + scheduler) | 0.0% | 53 MB | 1 |
| 1 download | 0.0%* | 55 MB | 4 |
| 5 downloads | 0.0%* | 55 MB | 4 |
| 10 downloads | 0.0%* | 55 MB | 4 |
| 20 downloads | 0.0%* | 55.1 MB | 4 |
| 4 concurrent throttled downloads | avg 1.4% / peak 15% | 54.5 MB | 4 |

\* Completed too fast to catch the transfer; the throttled run shows the real
transfer cost. **No memory growth (55 MB across 20 downloads), no thread or
connection leak** (thread count returns to baseline; WAL size stable ~90 KB).
UI responsiveness is preserved by the existing 200 ms poll / 120 ms coalescing
(no per-progress DB writes).

## Static Analysis
**PASS** — `pyflakes` reports only pre-existing unused-import warnings in
`ui/menu.py`, `batch/pattern.py`, `browser/protocol.py` (none from this work; no
undefined names, no real defects). `python -m py_compile` clean across all
modules.

## Dependency Audit
**PASS** — `requirements.txt` is minimal and every entry is used:
`requests`/`urllib3` (engine), `rich`/`colorama` (TUI), `pywebview` (GUI),
`pyfiglet` (banner), `psutil` (optional dashboard), `browser-cookie3`
(optional cookies). Note: `browser-cookie3` pulls heavy transitive deps
(pywin32/wmi/shadowcopy); moving it to an optional extra would slim installs.

## Packaging Readiness
**PARTIAL** (source distribution is ready; an installer needs one fix)

- **Required before an installer/binary distribution:** the task database and
  queue state live in `saved_links/` which is resolved relative to the source
  tree (`Path(__file__).parent.parent / "saved_links"`). On a read-only install
  directory (e.g. Program Files) this would fail. For a packaged app the
  storage must move to a user-writable path (e.g. `%LOCALAPPDATA%\N13`) with a
  one-time migration from `saved_links/`. The **documented source distribution
  is unaffected** (the repo dir is writable).
- Config lives in the user profile (`~/.config/terminal-download-manager/`,
  writable) and is auto-initialised on first run.

## Installer / Distribution Readiness (clean Windows 11)
Checklist for a clean machine:

- **Python 3.10+** runtime (source distribution) — required.
- **`pip install -r requirements.txt`** — pulls everything needed.
- **WebView2 runtime** for `--gui` (pywebview GUI backend) — preinstalled on
  Windows 11; otherwise installed by Edge/Windows Update.
- **FFmpeg** — **NOT used** (no media/streaming/playback in this project).
- **Microsoft Visual C++ runtime** — not required (pure Python + wheels;
  pywin32/wmi wheels bundle what they need).
- **Node/other runtimes** — not required.
- **Browser extension**: `python d.py --register`, then
  `python d.py --create-extension`, load the generated folder unpacked in
  `chrome://extensions`.
- **Required files**: the source tree + virtualenv.
- **Required directories**: user config dir (auto-created), download dir
  (auto-created), and `saved_links/` (auto-created; must be user-writable for a
  packaged install — see Packaging Readiness).
- **Config initialisation**: automatic on first launch (config + relay token).

## Remaining Risks
1. **Storage location for a packaged install** — `saved_links/` is
   project-relative. Not a problem for the documented source distribution; must
   be relocated for an installer (see above).
2. **Clipboard monitor** (optional, off) uses tkinter in a worker thread —
   future refactor recommended (win32clipboard).
3. **`browser-cookie3` transitive deps** make a lean install heavier — optional
   extras would slim it.
4. **Two instances with different config ports** each still run their own
   storage; the mutex prevents this in the normal (single-config) case.
5. The pre-existing unused-import warnings in `ui/menu.py` etc. are cosmetic.

## Required Before Release
- **None for the documented source distribution.**
- **For an installer/binary distribution:** relocate `saved_links/` (task DB /
  queue / history) to a user-writable path (e.g. `%LOCALAPPDATA%\N13`) with
  migration, and align the `dldm://` handler subprocess path with the packaged
  layout.

---

# Release Status

**RELEASE READY** for the documented source distribution.

Basis: 105/105 automated tests, real-process single-instance + forwarding
verified, full real-world server matrix with byte-exact checksums, pause/resume
and shutdown stress green, database failure matrix green (corruption only
classified as corruption), event queue safe, performance shows no leaks, static
analysis and dependency audits clean.

**NOT RELEASE READY as a packaged Windows installer** until the storage
location is moved to a user-writable path (single clear item listed under
"Required Before Release"). All other release criteria are met.
