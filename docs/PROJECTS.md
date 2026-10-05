# Download Projects — Architecture

How N13 gained a first-class *project* layer without replacing its download
engine.

**Vocabulary.** The backend persists **projects**; the UI calls them **groups**,
because that is what they are to the user. `Groups.load()` is the single place
the two words meet — see §11.

- Domain + persistence: `projects/`
- Shared database connection & migrations: `core/db.py`
- Task ↔ project association: `core/task.py`, `ui/common.py`
- Bridge: `ui/api.py` (`Api.projects_*`)
- UI: `ui/frontend/js/features/groups/`, `ui/frontend/css/groups.css`
- Tests: `tests/test_projects.py`, `tests/test_project_scheduling.py`,
  `tests/test_project_concurrency.py`, `tests/test_db_migrations.py`,
  `tests/frontend/groups-logic.test.mjs`

---

## 0. What the audit found

N13 is a layered desktop app with a very thin, very wide middle:

```
frontend (vanilla JS) → window.pywebview.api → ui/api.py::Api
                       → ui/common.py::TaskManager → core/*  → SQLite + JSON
```

Three god modules dominated the picture before this work:

| Module | Lines | Responsibilities it had accumulated |
|---|---|---|
| `ui/common.py::TaskManager` | 2062 | domain records, persistence, queue ordering, admission, worker execution, history, crash recovery, listener dispatch |
| `ui/api.py::Api` | 1509 | every bridge method for every feature, tray, updater, browser, settings |
| `core/store.py::TaskStore` | 504 | connection ownership, schema DDL, migrations, corruption recovery, and the task/history repositories |

Two facts made this a real problem rather than an aesthetic one:

1. **`TaskStore` owned the connection *and* the schema *and* the repositories.**
   Adding a `projects` table would have meant either a 700-line store or a second
   SQLite connection to the same file. Both are worse than fixing the seam.
2. **`TaskManager` owned admission.** Per-project concurrency is an admission
   rule, so it would have landed inside the 2000-line god class — exactly the
   outcome the brief forbids.

Everything else was in good shape and was left alone: the `DownloadTask` state
machine, the queue ordering/priority semantics, the auto-shutdown controller,
the download engine, and the frontend's delegation convention
(`App.x() → Module.x(this)`).

## 1. The decision

> **Extract two seams, then build the project domain as its own package.**
> *(Not "add project_id columns to TaskManager" — that is how a god class becomes
> a worse god class. Not "rewrite the queue" — the queue is correct and heavily
> tested; projects only add one admission rule to it.)*

Three changes, in dependency order:

1. **`core/db.py`** — one module owns the SQLite connection, the PRAGMAs, the
   corruption quarantine, and a *versioned* migration runner. `TaskStore` and
   `ProjectRepository` become repositories that share it. This is what makes
   "database repositories" and "database migrations" separate layers instead of
   one 500-line class.
2. **`projects/`** — the project domain, split by responsibility so no module
   has two jobs: models (pure data), schedule (pure time logic), admission (pure
   policy), repository (SQL), service (orchestration + validation).
3. **One new admission rule in `TaskManager`** — it delegates the "may this task
   start?" question to `ProjectAdmissionPolicy` instead of growing a second
   scheduling algorithm.

## 2. Module responsibilities

| Module | Owns | Must never |
|---|---|---|
| `core/db.py::Database` | connection, PRAGMAs, the migration runner, quarantine | know about tasks or projects |
| `core/migrations.py` | schema DDL and the versioned ledger | open a connection |
| `core/artifacts.py` | which files on disk belong to a task | delete anything |
| `projects/models.py` | `Project`, `ProjectStatus`, `CompletionAction`, `DeleteMode`, name rules | touch I/O |
| `projects/views.py` | `ProjectCounts`, `ProjectView`, `derive_state`, `STATE_REASONS` | be imported by `models` |
| `projects/schedule.py` | window arithmetic, overnight wrap, day matching | read the config or the clock itself (time is injected) |
| `projects/admission.py` | "may this task start?" given global + per-project limits | mutate state |
| `projects/repository.py` | the `projects` table | contain business rules |
| `projects/validation.py` | destination-folder checks, OS errors → error codes | create projects |
| `projects/deletion.py` | deleting a project's files from disk | be reachable without an explicit confirmation |
| `projects/service.py` | CRUD, validation, deletion policy, view building, gate snapshot | know about the UI |
| `ui/projects_api.py::ProjectsApiMixin` | the bridge surface (`get_projects`, `create_project`, …) | hold business rules |
| `ui/queue_projects.py::ProjectAdmissionMixin` | slot accounting, the admission check, the gate snapshot | own the queue |
| `ui/common.py::TaskManager` | queue + workers | own project rules |
| `ui/frontend/js/features/groups/*` | model (pure) / view (markup) / dialogs (forms) / controller (data) | one file doing two of those |

`models.py` describes **what a project is**; `views.py` describes **what it shows**.
The dependency is one-way (`views` → `models`), which is what keeps the domain free of
any notion of presentation. `deletion.py` is separate for a blunter reason: it is the
one place in the package where a mistake is unrecoverable.

Dependency direction is strictly one-way:

```
ui/api.py (+ ProjectsApiMixin) → projects/service.py → {repository, models, schedule,
                                                        admission, validation}
                                                     → core/db.py, core/store.py
ui/common.py → projects/admission.py (policy only, no I/O)
ui/frontend/js/features/groups/groups.js → GroupsModel / GroupsView / GroupDialogs
```

`projects/admission.py` imports nothing from `ui/`, so `TaskManager` can use it
without a cycle.

## 3. Database migration

`core/db.py` runs a versioned migration list. `PRAGMA user_version` is the
ledger and is only advanced *after* the migration commits — the previous code
wrote `user_version=1` unconditionally on every open, which would have silently
reset a newer version.

| Version | Change |
|---|---|
| 1 | baseline: `tasks`, `history`, `queue_order` (idempotent `CREATE TABLE IF NOT EXISTS`) |
| 2 | `projects` table; `tasks.project_id` column + index |

Legacy tasks (created before projects existed) are attached to a **Default**
project by migration 2, so nothing is discarded and `project_id` is never NULL
for a real task. The Default project is created once and is protected from
deletion.

## 4. Scheduling

`projects/schedule.py` is pure: it takes a `ProjectSchedule` and a
`datetime`, and returns whether the window is open. It reuses the *semantics*
already proven by `core/scheduler.py` (half-open `[start, end)`, midnight wrap)
but is a separate function so the two can be tested independently — the queue
scheduler gates the whole queue, a project schedule gates one project.

Schedules never touch another project: the scheduler evaluates each project's
window and produces a per-project gate. A project whose window is closed stops
*launching*; running downloads are left alone (the same policy the queue gate
already uses, for the same reason: stopping a transfer is a different decision
from not starting one).

## 5. Concurrency

`ProjectAdmissionPolicy.may_start()` is consulted per candidate task and must
answer two independent questions:

1. Is a **global** slot free? (`TaskManager.max_concurrent` — unchanged)
2. Is a **project** slot free? (the project's own limit, if set)

Both must pass. A project limit of 0/None means "inherit the global limit".
The policy is pure and takes the currently-running counts as arguments, so it is
unit-testable without a queue.

## 6. Completion actions

A project may declare a completion action. `ProjectService` reports project
completion; `ui/api.py` maps it onto the **existing** `AutoShutdownController`
rather than creating a second shutdown path. The controller already refuses to
fire while any work is outstanding and already owns the countdown, the abort
window and the stale-session recovery — duplicating any of that would be the
bug, not the feature.

Project-scoped shutdown is therefore only offered when it is honest: the
controller's own queue-wide policy remains the final authority, and the UI says
which scope is in effect.

## 7. State derivation

`derive_state()` in `projects/views.py` is the **single** place the precedence
is defined. The UI never re-derives a state from counts — two implementations of
the same rule eventually disagree, and the copy in the UI is the one that would
be wrong.

Persisted is `Project.status` (`active` / `paused`) and nothing else. Everything
else is computed on read:

| Order | Condition | State | Reason |
|---|---|---|---|
| 1 | the user paused it | `paused_by_user` | `project_paused` |
| 2 | no tasks at all | `empty` | `no_tasks` |
| 3 | a transfer is running | `running` | `active_tasks` |
| 4 | window closed **and** work queued | `paused_by_schedule` | the window's own reason |
| 5 | window closed **and** work pending | `waiting_for_schedule` | the window's own reason |
| 6 | window open **and** work queued | `scheduled` | `in_window` |
| 7 | anything pending | `queued` | `waiting_for_slot` |
| 8 | a failure with nothing pending | `failed` | `has_failures` |
| 9 | otherwise | `completed` | `all_complete` / `nothing_left` |

Two consequences worth keeping:

* **User intent wins over everything.** A pause is a decision, not a symptom, so
  it is reported before a running transfer can claim the project is fine.
* **A running transfer wins over the schedule.** A closed window stops *launching*
  new work; it never stops a download that is already moving. Reporting
  `paused_by_schedule` for a project that is actively transferring would be a lie.

`STATE_REASONS` lists every reason the UI may receive. A test asserts that a
`projects.reason.*` i18n key exists for exactly that set in both locales, so a new
branch cannot ship without a translation.

## 8. Day indices

Day indices use the **JavaScript convention: Sunday = 0 … Saturday = 6**, because
that is what the UI sends (`Date.getDay()`, and the `projects.day.*` labels).

`datetime.weekday()` uses **Monday = 0**. Every conversion therefore goes through
`projects/schedule.py::_weekday_index()`. Mixing the two conventions shifts every
day-filtered schedule by one day — silently, and only for schedules that restrict
days, which is exactly the kind of bug that survives a casual test. There is a
dedicated test (`test_project_scheduling.py::DayFilterTest`) that pins
`0 == Sunday` and `1 == Monday`.

## 9. Testing

| File | Covers |
|---|---|
| `tests/test_db_migrations.py` | fresh schema, v1→v2 upgrade, legacy tasks attached to Default, idempotency, future versions left alone |
| `tests/test_projects.py` | name rules, persistence across reopen, rename keeping task links, all three delete policies, the confirmation guard, file-path containment |
| `tests/test_project_scheduling.py` | window evaluation, overnight wrap, day filter, fail-open, admission policy ordering |
| `tests/test_project_concurrency.py` | both ceilings, pause/schedule isolation, starvation, duplicate workers, slot release, restart persistence |
| `tests/frontend/groups-logic.test.mjs` | progress-is-null, tab strip order, task scoping, validation mirroring, and cross-layer checks that the frontend state / reason / day / delete-mode vocabularies match the backend enums |

The concurrency tests drive the real `TaskManager` with a runner that *parks*
inside `download()`, so "how many are running" is controlled by the test rather
than guessed from timing.

## 10. What is deliberately not here

* **No per-project headers, cookies, mirrors or tags.** Those are per-*task*
  features the download engine does not have at all; a project cannot provide
  them and pretending otherwise would be fake UI.
* **No `REFERENCES` clause on `tasks.project_id`.** SQLite cannot `ALTER TABLE
  ADD COLUMN` with a non-NULL foreign key default, and rebuilding `tasks` to get
  one would mean copying every row — a real data-loss risk for a constraint the
  service already enforces inside a transaction (see §3).
* **No second scheduler.** A project window produces a *gate* that the existing
  admission path consumes; `core/scheduler.py` still owns the queue-wide gate.

## 11. Where the groups live in the UI

**Groups are tabs inside Downloads. There is no "Projects" page and no Projects
sidebar entry.** The Sidebar is reserved for application areas (Downloads, Queue,
History, Batch, Browser, Logs, Dashboard, Settings); a group must never compete
with those.

```
Downloads
  [ + ] [ All ] [ Default ] [ Breaking Bad ] [ GTA V ] [ Movies ]   ← #grpStrip
  ┌ Breaking Bad ── 5 downloads · 2 active · … ── Running  24% ┐    ← #grpHead
  └ facts: folder · schedule · concurrency · completion       ┘
  [ All 5 ] [ Active 2 ] [ Queued 1 ] [ Paused 1 ] …               ← group-scoped chips
  rows belonging ONLY to the open group
```

### The central rule: switching a tab is a *re-filter*, not a page change

`Groups.setActive(id)` mutates `app.state.activeProject` and clears
`app.state.listSig`, then re-renders the **same** Downloads view. No second task
list, no second renderer, no per-group worker system. The whole feature is one
predicate — `DownloadsView.groupTasks(app)` — applied before the existing
filter/sort/search pipeline.

```
groupTasks(app)  →  filteredTasks(app)  →  sort  →  rows
   (group scope)      (state/cat/search)     (existing)
```

Everything downstream (`updateCounts`, `renderCatStrip`) reads from
`groupTasks`, so the chips and the category strip always agree with the rows on
screen. They are group-scoped on purpose: a chip reading "9" above a list of 5
is the kind of small lie that erodes trust in every other number on the page.

### The Default group is a real tab, and it owns the orphans

A task with no `project_id` belongs to **Default**, exactly as the backend
computes it. If the frontend used a different rule, a legacy task would be
visible under "All" and invisible in every other tab.

### Two empty states, deliberately different

| Situation | Message |
|---|---|
| the open group has no tasks at all | *No downloads in this group yet* + `[ + Add download ]` |
| the group has tasks, but a filter/search hides them | *Nothing matches* |
| nothing anywhere yet | the first-run empty state (add / new group / extension / batch) |

A group being **open** is not the same as a group being **empty**. Conflating the
two makes a search that returns nothing claim the group is empty — which is why
the test is `groupTasks(app).length === 0`, not `Groups.current() !== null`.

### Frontend modules

| File | Owns | Must never |
|---|---|---|
| `groups-model.js` | pure logic: tab order, task scoping, counts, progress, validation, display names | touch the DOM, the API or `app.state` |
| `groups-view.js` | markup only: the strip, the header, the empty states | fetch data or bind events |
| `group-dialogs.js` | the create/edit and delete forms | talk to the API |
| `groups.js` | the only module that calls the API; owns `activeProject` | render markup |

`groups-model.js` is evaluated standalone by the test suite, so it must stay free
of DOM and bridge references.

### Progressive disclosure in the create form

Only **name** and **destination folder** are visible up front — the two things
that make a group meaningful. Concurrency, completion action and the schedule
live behind a single *More options* disclosure. The 17-step flow ("create a
group, add downloads, switch groups, settings survive") must feel like *"these
are my download groups"*, not like a project-management application.

## 12. Tab order is creation order

`projects/repository.py` orders `is_default DESC, created_at ASC, name ASC`.

Renaming a group or pausing it must **not** move its tab. A tab that reorders
itself while the user is reaching for it is a tab they will click by mistake, so
the list is stable and the UI never re-sorts it. `ListingOrderTest` in
`tests/test_projects.py` pins this.

