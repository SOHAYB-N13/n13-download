# N13 Download Manager — Project Map (Phase 0 Discovery)

**Generated**: 2026-11-18  
**Version**: 1.4.7
**Purpose**: Complete architecture inventory before transformation

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         N13 DOWNLOAD MANAGER                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Windows    │  │   Browser    │  │   Android    │  │   Installer  │   │
│  │   Desktop    │  │  Extension   │  │   (Kotlin/   │  │  (Inno Setup │   │
│  │  (PyInstaller│  │  (Native     │  │  Jetpack     │  │   7 + WiX)  │   │
│  │   + WebView2)│  │   Messaging) │  │  Compose)    │  │              │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │            │
│         └─────────────────┼─────────────────┼─────────────────┘            │
│                           ▼                 ▼                              │
│              ┌─────────────────────────────────────────┐                   │
│              │           CORE ENGINE (Python)          │                   │
│              │  ┌─────────────────────────────────┐    │                   │
│              │  │ Download Engine                 │    │                   │
│              │  │  - Segmented multi-threaded     │    │                   │
│              │  │  - SmartOptimizer (adaptive)    │    │                   │
│              │  │  - ConnectionGovernor           │    │                   │
│              │  │  - State machine (9 states)     │    │                   │
│              │  │  - Range requests + resume      │    │                   │
│              │  └─────────────────────────────────┘    │                   │
│              │  ┌─────────────────────────────────┐    │                   │
│              │  │ Task Queue & Scheduling         │    │                   │
│              │  │  - Queue with priority + order  │    │                   │
│              │  │  - Queue-wide time gate         │    │                   │
│              │  │  - One-shot scheduled downloads │    │                   │
│              │  │  - Auto-shutdown controller     │    │                   │
│              │  └─────────────────────────────────┘    │                   │
│              │  ┌─────────────────────────────────┐    │                   │
│              │  │ Storage Layer                   │    │                   │
│              │  │  - SQLite + WAL (tasks, hist)   │    │                   │
│              │  │  - Projects (groups/tabs)       │    │                   │
│              │  │  - Scheduled downloads table    │    │                   │
│              │  │  - JSON state files (per-task)  │    │                   │
│              │  └─────────────────────────────────┘    │                   │
│              │  ┌─────────────────────────────────┐    │                   │
│              │  │ Network & Security              │    │                   │
│              │  │  - SessionManager (2 transports)│    │                   │
│              │  │  - Probe (HEAD→Range→GET)       │    │                   │
│              │  │  - SSRF protection + DNS cache  │    │                   │
│              │  │  - Cookie / Auth / Proxy        │    │                   │
│              │  └─────────────────────────────────┘    │                   │
│              └─────────────────────────────────────────┘                   │
│                           │                                                │
│                           ▼                                                │
│              ┌─────────────────────────────────────────┐                   │
│              │           GUI (WebView2)                │                   │
│              │  ┌─────────────────────────────────┐    │                   │
│              │  │ Frontend (Vanilla JS/HTML/CSS)  │    │                   │
│              │  │  - Component architecture       │    │                   │
│              │  │  - i18n (EN/FA, RTL support)    │    │                   │
│              │  │  - Reactive state + API bridge  │    │                   │
│              │  │  - Accessibility (ARIA, focus)  │    │                   │
│              │  └─────────────────────────────────┘    │                   │
│              └─────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Engine Modules (`core/`)

| Module | Lines | Purpose | Key Classes |
|--------|-------|---------|-------------|
| **download.py** | ~1,400 | Main download controller | `DownloadController`, `DownloadWorker` |
| **state.py** | ~80 | Per-task persistent state (JSON) | `DownloadState` |
| **control.py** | ~80 | Pause/cancel signalling | `TaskControl`, `TaskCancelled` |
| **optimizer.py** | ~240 | Adaptive connection scaling | `SmartOptimizer`, `ConnectionGovernor` |
| **store.py** | ~460 | SQLite repository (tasks/history) | `TaskStore` |
| **db.py** | ~300 | Database connection + migrations | `Database` |
| **migrations.py** | ~180 | Schema versioning (v1→v4) | `run_migrations` |
| **probe.py** | ~430 | URL metadata discovery | `probe_url`, `probe_with_headers` |
| **session.py** | ~350 | HTTP sessions (2 transports) | `SessionManager`, `_GuardedSession` |
| **auto_shutdown.py** | ~1,650 | State-driven shutdown controller | `AutoShutdownController`, 8 states |
| **scheduler.py** | ~170 | Queue-wide time gate + night cap | `Scheduler` |
| **scheduled.py** | ~320 | One-shot scheduled downloads | `ScheduledDownloadService`, `ScheduledRunner` |
| **task.py** | ~150 | Domain models + status enum | `TaskStatus`, `TaskSnapshot` |
| **urls.py** | ~100 | URL parsing / filename extraction | — |
| **rules.py** | ~200 | Download rules engine | `RuleEngine` |
| **parts.py** | ~100 | Segment representation | `DownloadPart` |
| **throttle.py** | ~150 | Global bandwidth limiter | `SpeedLimiter` |
| **speed.py** | ~50 | Speed calculation utilities | — |
| **merge.py** | ~80 | Part file merging | `merge_parts` |
| **clipboard.py** | ~80 | Clipboard monitoring | `ClipboardMonitor` |
| **startup.py** | ~100 | Application initialization | `startup_checks` |
| **tray.py** | ~200 | System tray integration | `TrayController` |
| **updater.py** | ~150 | Auto-update checker | `Updater` |
| **analyzer.py** | ~100 | Content analysis | `ContentAnalyzer` |
| **security.py** | ~100 | SSRF protection, URL validation | — |
| **errors.py** | ~50 | Custom exceptions | `BlockedURLError`, etc. |
| **cookies.py** | ~100 | Cookie jar resolution | — |
| **context.py** | ~80 | Legacy context (deprecated) | — |
| **single_instance.py** | ~80 | Single-instance enforcement | — |
| **paths.py** | ~50 | Data directory resolution | — |
| **artifacts.py** | ~80 | Extension/installer helpers | — |
| **utils.py** | ~100 | Shared utilities | `human_size`, etc. |

**Total core engine**: ~7,500 lines

---

## 3. Projects Package (`projects/`)

| Module | Lines | Purpose | Key Classes |
|--------|-------|---------|-------------|
| **models.py** | ~250 | Domain models | `Project`, `ProjectStatus`, `ProjectState`, `ProjectID` |
| **validation.py** | ~150 | Directory validation | `check_directory`, `ProjectValidationError` |
| **admission.py** | ~200 | Queue admission policy | `ProjectGate`, `AdmissionContext`, `AdmissionDecision` |
| **schedule.py** | ~180 | Per-project time windows | `ProjectSchedule`, `WindowDecision` |
| **repository.py** | ~200 | Project persistence | `ProjectRepository` |
| **views.py** | ~180 | Aggregation + state derivation | `ProjectCounts`, `ProjectView`, `derive_state` |
| **service.py** | ~400 | Orchestration layer | `ProjectService` |
| **deletion.py** | ~200 | Safe project deletion | `delete_project_files` |
| **__init__.py** | ~50 | Module map documentation | — |

**Total projects**: ~1,600 lines

---

## 4. Frontend Architecture (`ui/frontend/`)

### Component Hierarchy
```
Components (facade)
├── ModalUI              — Shared modal shell
├── DialogsUI            — confirm, link, conflict, rename, speed, scheduler, priority, properties
├── ToastUI              — Transient notifications (max 4)
├── ContextMenuUI        — Row + selection menus
├── EmptyStateUI         — Empty states + skeleton rows
├── VisualFX             — countUp, sparkline, ripple
├── RuleEditorUI         — Settings → Rules editor
├── ScanResultsUI        — Pattern scan results + confirmation
├── ScheduledPanelUI     — Scheduled downloads panel
└── DownloadRowUI        — Downloads list row renderer
```

### Core Modules
| Module | Purpose |
|--------|---------|
| **api.js** | Python bridge (60+ methods) |
| **utils.js** | Formatting, icons, DOM helpers, file type detection |
| **i18n.js** | Reactive i18n (EN/FA, 2444 lines) |
| **navigation.js** | Page switching, sidebar, per-page render dispatch |
| **shortcuts.js** | Global keyboard shortcuts |
| **prefs.js** | Theme/accent/sidebar persistence + WCAG contrast |

### Features
| Module | Purpose |
|--------|---------|
| **batch/scan-results.js** | Pattern scan UI (discovery + explicit submit) |
| **scheduling/scheduled-panel.js** | Scheduled downloads with countdown timers |

---

## 5. Data Flow Architecture

### Download Lifecycle
```
USER ACTION (UI) ──▶ API.js ──▶ Python Bridge ──▶ TaskManager
                                                          │
                              ┌───────────────────────────┼───────────────────────────┐
                              ▼                           ▼                           ▼
                        ┌─────────────┐             ┌─────────────┐             ┌─────────────┐
                        │  QUEUE      │             │  PROJECTS   │             │  SCHEDULED  │
                        │  (priority  │             │  (admission │             │  (one-shot  │
                        │   + order)  │             │   gates)    │             │   at time)  │
                        └──────┬──────┘             └──────┬──────┘             └──────┬──────┘
                               │                         │                         │
                               └─────────────────────────┼─────────────────────────┘
                                                         ▼
                                              ┌─────────────────────┐
                                              │  DOWNLOAD ENGINE    │
                                              │  (SmartOptimizer +  │
                                              │   ConnectionGovernor)│
                                              └──────────┬──────────┘
                                                         │
                              ┌──────────────────────────┼──────────────────────────┐
                              ▼                          ▼                          ▼
                        ┌─────────────┐            ┌─────────────┐            ┌─────────────┐
                        │  SQLite     │            │  JSON State │            │  History    │
                        │  (tasks,    │            │  (.part files│            │  (completed │
                        │   history,  │            │   per task) │            │   entries)  │
                        │   queue)    │            │             │            │             │
                        └─────────────┘            └─────────────┘            └─────────────┘
```

### State Machine (TaskStatus)
```
QUEUED ──▶ ANALYZING ──▶ STARTING ──▶ DOWNLOADING ──▶ MERGING ──▶ VERIFYING ──▶ COMPLETED
                │                                            │
                │                                            ├─▶ PAUSED (user)
                │                                            │
                └─▶ FAILED (error) ◀─────────────────────────┘
                └─▶ CANCELLED (user)
                └─▶ REMOVED (user delete)
                └─▶ STOPPED (queue pause)
```

### Auto-Shutdown State Machine (8 states)
```
DISABLED ──▶ ARMED ──▶ WAITING ──▶ COUNTDOWN ──▶ EXECUTED
     ▲           │         │            │
     │           │         │            ▼
     │           │         │        CANCELLED ◀─── USER
     │           │         │
     │           │         ▼
     │           │        BLOCKED
     │           │
     └───────────┴────────── ERROR (Windows refused)
```

---

## 6. Key Design Patterns

### 1. Repository Pattern (Storage)
- `TaskStore` owns `tasks`, `history`, `queue_order` tables
- `ProjectRepository` owns `projects` table
- `ScheduledDownloadService` owns `scheduled_downloads` table
- All share single `Database` connection (WAL mode)

### 2. Policy/Decision Separation
- `AutoShutdownPolicy` — pure function, no side effects
- `ProjectGate` / `AdmissionContext` — frozen snapshots for queue admission
- `ProjectSchedule.evaluate()` — returns `WindowDecision` (data, not action)

### 3. State Machine Explicit Everywhere
- `TaskStatus` enum (9 values)
- `ShutdownState` enum (8 values)
- `ProjectStatus` / `ProjectState` (derived)
- `ScheduledDownload` status (scheduled/due/starting/completed/cancelled/failed/paused/missed)

### 4. Two-Transport Session Architecture
- `session` — download transport (retries, connection pooling)
- `probe_session` — metadata transport (minimal retries, fast fail)

### 5. Component Facade (Frontend)
```javascript
const Components = {
  ModalUI, DialogsUI, ToastUI, ContextMenuUI,
  EmptyStateUI, VisualFX, RuleEditorUI,
  ScanResultsUI, ScheduledPanelUI, DownloadRowUI
};
```

---

## 7. Configuration & Persistence

### AppConfig (config/settings.py)
- **Network**: pool sizes, timeouts, proxy, auth, SSL verify, UA
- **Download**: max connections (default 8), speed limit, retry policy
- **Queue**: scheduler, night cap, auto-shutdown preferences
- **Projects**: default project, auto-create
- **UI**: theme, accent, sidebar, language
- **Security**: block_private_urls, cookie sources

### Data Directories
```
%LOCALAPPDATA%\N13\
├── n13.db              # SQLite (WAL mode)
├── n13.db-shm / -wal   # WAL files
├── auto_shutdown.json  # Crash-recovery marker
├── gui_queue.json      # Legacy (imported once)
├── state/              # Per-task .json state files
├── logs/               # Rotating logs
├── chrome_extension/   # Browser extension copy
└── projects/           # Project-specific folders
```

---

## 8. Browser Extension Integration

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Native Messaging** | JSON over stdin/stdout | `dldm://` protocol handler |
| **Loopback Relay** | HTTP 127.0.0.1:6868 | Fallback when native messaging unavailable |
| **Extension Locator** | Python | Syncs extension to `{app}\_internal\chrome_extension` |
| **Protocol Registration** | Inno Setup (HKCU) | `dldm://<url>` → `N13.exe "%1"` |
| **Upgrade Repair** | Inno Setup Code | Fixes stale registrations from old versions |

---

## 9. Build & Distribution

### Windows Installer (Inno Setup 7)
- **Output**: `release/N13-Download-Manager-Setup.exe`
- **Architecture**: x64 only (Windows 10 17763+)
- **Compression**: LZMA2 solid
- **Privileges**: lowest (per-user install)
- **Protocol Repair**: Runs at `ssPostInstall` — fixes stale `dldm://` registrations
- **Uninstall**: Preserves `%LOCALAPPDATA%\N13\` data

### PyInstaller Build
- **Entry**: `build/n13_entry.py` → `dist/N13/N13.exe`
- **WebView2**: Required runtime (checked at install)
- **Assets**: icon.ico embedded

---

## 10. Test Suite Status (as of discovery)

| Category | Passed | Failed | Errors | Notes |
|----------|--------|--------|--------|-------|
| Unit/Integration | 856 | 2 | 34 | 34 errors = UI tests need headless Chrome/Playwright |
| Release Blockers | 77 | 0 | 0 | All 3 blockers fixed (pause, shutdown, corrupt DB) |
| UI Tests | — | 2 | 34 | `test_scan_ui.py` (real browser), UI tests (no browser) |

---

## 11. Known Technical Debt / Weaknesses (Phase 0 Findings)

### Architecture
1. **God modules**: `core/download.py` (1,400 lines), `core/auto_shutdown.py` (1,650 lines)
2. **Frontend monolith**: `i18n.js` (2,444 lines) — translation dictionary embedded
3. **Legacy coupling**: `gui_queue.json` import path still exists
4. **Two pause concepts**: Queue gate (`TaskManager.pause_queue`) vs `TaskStatus.PAUSED` — subtle bugs possible
5. **Two position concepts**: `queue_index` (DB) vs `queue_position` (UI) — reconciliation needed

### Reliability
1. **Single-instance test flaky** under load (passes in isolation)
2. **Safe-delete guards block clean builds** (`genie-trash` exits 1; 50-file threshold vs ~1,400 files)
3. **Git push hangs** due to credential helper (workaround: `GIT_TERMINAL_PROMPT=0`)
4. **UI tests unrunnable** without Playwright/Chrome headless in CI

### Performance
1. **No connection pooling metrics** exposed to UI
2. **SmartOptimizer logging** verbose at DEBUG level
3. **DNS cache** (60s TTL) — no observability
4. **Scheduler tick** (15s) — may delay schedule changes

### Maintainability
1. **No TypeScript** — vanilla JS with JSDoc only
2. **No build step** for frontend — manual minification needed for production
3. **Inline CSS/HTML** in JS components — no style isolation
4. **i18n keys** scattered as strings — no compile-time checking

---

## 12. Transformation Opportunities (12-Phase Plan Alignment)

| Phase | Target Area | Current State | Opportunity |
|-------|-------------|---------------|-------------|
| 1 | New Download Dialog | Redesigned (Advanced panel) | Complete — connections, speed limit, priority, checksum |
| 2 | Queue UX | Polished (37 issues fixed) | Drag-drop, manual/priority views, keyboard nav |
| 3 | Batch/Scheduling | Overhauled | Pattern scan, explicit submit, scheduled panel |
| 4 | Projects System | Complete | Tabs, admission gates, schedules, deletion modes |
| 5 | Auto-Shutdown | State machine (8 states) | Reliable, testable, crash-recovery |
| 6 | Smart Download | Adaptive optimizer | ConnectionGovernor, server backoff, speed-limit aware |
| 7 | Core Engine | Segmented + resume | Range probe, multi-threaded, state persistence |
| 8 | Storage | SQLite + WAL + migrations | v4 schema, projects, scheduled_downloads |
| 9 | Network | Dual transport + SSRF | Probe budget, DNS cache, cookie/auth/proxy |
| 10 | Installer | Inno Setup 7 + repair | Per-user, protocol fix on upgrade |
| 11 | Frontend | Component architecture | Modal, toast, context menu, i18n, accessibility |
| 12 | Android | Separate (Kotlin/Compose) | Not in scope for Windows transformation |

---

## 13. File Inventory Summary

| Category | Files | Lines |
|----------|-------|-------|
| Core Engine (`core/`) | 27 | ~7,500 |
| Projects Package (`projects/`) | 9 | ~1,600 |
| Frontend JS (`ui/frontend/js/`) | 17 | ~6,500 |
| Frontend HTML/CSS | 8 | ~2,500 |
| Installer (`installer/`) | 4 | ~500 |
| Config (`config/`) | 3 | ~300 |
| Tests (`tests/`) | 15 | ~4,000 |
| Build (`build/`) | 3 | ~200 |
| Android (`android/`) | 20+ | ~3,000 |
| **Total** | **100+** | **~26,000** |

---

*End of Project Map — Phase 0 Discovery Complete*