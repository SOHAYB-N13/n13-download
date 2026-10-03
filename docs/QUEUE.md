# The Queue

How N13 decides *what runs next*, *what "paused" means*, and *what the UI is
allowed to claim*.

- Domain model: `core/task.py` (`TaskStatus`, `_TRANSITIONS`)
- Queue manager: `ui/common.py` (`TaskManager`)
- Bridge: `ui/api.py` (`Api`)
- UI: `ui/frontend/js/queue.js` (Queue page),
  `ui/frontend/js/features/downloads/*.js` (Downloads page)
- Tests: `tests/test_queue.py`, `tests/test_queue_ui.py`,
  `tests/test_ui_queue_and_repair.py`, `tests/frontend/queue-logic.test.mjs`

---

## 0. Why this document exists

An audit of the queue in October 2026 found that the *scheduling core* was
sound but that four **seams** between the core and the rest of the app were
not. This document records the decision that followed, and the rules the code
now implements.

### What was already correct (and was therefore deliberately left alone)

| Component | Why it was kept |
|---|---|
| `_next_queued_locked` — selection key `(priority, manual position)` | Correct, and the reason priority and hand-ordering can coexist. `_order` is never re-sorted by priority; that is the feature, not an oversight. |
| `_start_next` — fills every free slot in one pass, `attempted` guard | Correct. The guard prevents re-selecting the same task forever while holding the lock. |
| `queue_plan` + `_simulate_slot_starts` | Correct, and honest: an active download of unknown length contributes `None`, never `0.0`. A `0.0` would make the task behind it claim `starts_immediately`. |
| `reorder_tasks` — block move with an absolute target index | Correct, and single-sourced: drag & drop, Ctrl+Up/Down and Move to top/bottom all funnel through it. |
| Listeners emitted **outside** the lock on every path | Load-bearing. `AutoShutdownController` takes its own lock and then calls `manager.snapshots()`; a manager-lock → controller-lock order would deadlock. |
| `_active` accounting, the retry-race guard (`rec.control is not control`), deferred removal | Correct. A stale worker must never clobber a newer worker's state. |

Rewriting any of the above would have destroyed verified behaviour. The queue
is protected by `tests/test_queue.py`, `tests/test_queue_ui.py`,
`tests/test_ui_queue_and_repair.py`, `tests/test_ui_task_controls.py` and
`tests/frontend/queue-logic.test.mjs` — several hundred assertions between
them, including the ordering and plan invariants.

### What was wrong

**1. "Pause" meant three different things.** `_global_pause` is a *queue gate*
("start nothing new"), while `pause_task` is a *per-task stop*. They shared the
word "Pause" and the gate was invisible. Worse, `resume_task()` cleared the
gate as a side effect:

```python
# before
def resume_task(self, task_id):
    rec = self._tasks.get(task_id)
    if rec:
        self._global_pause = False      # <-- resuming ONE row un-paused the QUEUE
        rec.control.resume()
```

So *Pause everything* → *Resume* on one row started every other waiting
download. That is exactly the ambiguity the brief forbids.

**2. `queue_index` was labelled "queue order" but was the *manual* order.**
The scheduler selects by `(priority, manual position)`, so with priorities in
play the two orders differ. The Downloads page's `queue` sort and its
"Queue position" field showed the manual order, so a task could be displayed at
position 1 while actually starting 5th. The Queue page already handled this
honestly (two views, `runs at #n` badges); the backend did not.

**3. An explicit pause was discarded on restart.** `_restore_queue` did:

```python
if task.status in ACTIVE_STATES:        # ACTIVE_STATES includes PAUSED
    task.force_status(TaskStatus.QUEUED)
```

A task the user deliberately paused came back as `Queued` and, with
`resume_on_startup`, started downloading again. Pausing is a user decision, not
an interrupted state.

**4. …and a restored pause had no way to resume.** `resume_task` on a `Paused`
task ran `transition(DOWNLOADING)` and then `_start_next()`. But `_start_next`
only ever selects `QUEUED` tasks, and a restored task has no worker thread and
does not hold a slot. The task would sit in `Downloading` forever with no
worker, no progress and no error. This was latent *because* of defect 3 — the
restore path never produced a worker-less `Paused` task.

### The decision

> **Refactor the seams; do not rewrite the core.**
> *(Not "improve incrementally" — three of the four defects are semantic, not
> cosmetic. Not "rewrite" — the scheduler is correct and heavily tested, and a
> rewrite would trade verified behaviour for unverified behaviour.)*

The four seams were reworked into explicit, named, observable concepts:

1. The queue gate became a first-class concept with its own name, its own
   accessors and its own UI presence.
2. Effective start position became a first-class field, computed from one
   shared helper so `queue_plan` and the snapshots cannot disagree.
3. Restore learned the difference between *interrupted* and *paused*.
4. Resume learned the difference between a *live* paused worker and a
   *restored* pause.

---

## 1. The two pause concepts

These are separate and neither implies the other.

| | Scope | Blocks new starts? | Stops transfers? | Persisted? |
|---|---|---|---|---|
| **Queue gate** (`TaskManager.queue_paused`) | whole queue | **yes** | no | no |
| **Task pause** (`TaskStatus.PAUSED`) | one task | no | **yes** | yes |

The user-facing buttons compose them:

| Button | Effect |
|---|---|
| **Pause everything** | closes the queue gate **and** pauses every active task |
| **Resume everything** | opens the queue gate **and** resumes every paused task |
| **Pause queue** | closes the gate only — active downloads keep running |
| **Resume queue** | opens the gate only |
| *row* **Pause** | pauses that task only |
| *row* **Resume** | resumes that task only |

### Rules

- **A per-task resume never opens the queue gate.** `resume_task` on a task in
  a paused queue resumes *that task* and leaves the gate closed.
- **`start_task` is an explicit override.** Clicking *Start* on a waiting row
  means "run this one now", so it bypasses the **queue gate** — but never the
  **scheduler gate** (a scheduled window is a hard constraint the user
  configured, not a transient pause). It still respects `max_concurrent`: if
  every slot is busy the task moves to the front of the queue and waits.
- **The gate is not persisted.** A restart always begins with the gate open.
  Pausing the queue is a "stop for a minute" action, not a setting.
- **The gate is always visible.** Whenever `queue_paused` is true the UI shows
  a banner with a *Resume queue* button. A stalled queue must never look like a
  hung app.
- **`_start_next` has exactly one blocking predicate.** `_start_blocked()` is
  `_closed or _global_pause or _scheduler_gate`. Every early return uses it, so
  a future gate cannot be forgotten in one branch.

## 2. Position: manual vs effective

Two numbers, never conflated:

| Field | Meaning | Order |
|---|---|---|
| `queue_index` | position in `_order` — the list the user drags | manual |
| `queue_position` | the order the task will **actually start** in | effective |

`queue_position` is 1-based among `Queued` tasks and `-1` for anything that is
not waiting. It is produced by `_queued_in_start_order_locked()`, the **same**
helper `queue_plan()` uses, so the two can never drift apart.

When they disagree, priority is what makes them disagree — and the UI says so
rather than showing one number and meaning the other. A row whose effective
position differs from its visual slot is badged `runs at #n`.

## 3. Selection rule (unchanged, and deliberately so)

`_start_next` picks the waiting task with the lowest `(priority, manual
position)`, strictly-less so ties go to the earlier manual position.

`_order` is **never** re-sorted by priority. Raising one task's priority moves
only that task; the arrangement the user built by hand stays intact. This is
the whole reason priority and manual ordering can coexist.

Concurrency slots are **not** per-file connections. `max_concurrent` is how
many downloads run at once; each download's own connection count is the
optimizer's business (`core/optimizer.py`). The Queue page *suggests* a slot
change and never applies one.

## 4. Lifecycle

```
            ┌───────────── add ─────────────┐
            v                               │
        ┌────────┐   slot free    ┌─────────┴──┐
        │ Queued │ ─────────────► │ Analyzing  │
        └───┬────┘                └─────┬──────┘
            │                           v
            │                      ┌─────────┐      ┌────────┐
            │                      │ Starting│ ───► │Paused  │
            │                      └────┬────┘      └───┬────┘
            │                           v               │ resume
            │                     ┌────────────┐        │
            │                     │ Downloading│◄───────┘
            │                     └──────┬─────┘
            │                            v
            │                    ┌─────────┐   ┌──────────┐   ┌───────────┐
            │                    │ Merging │──►│Verifying │──►│ Complete  │
            │                    └─────────┘   └──────────┘   └───────────┘
            │
            └──► Cancelled / Failed  ──retry──► Queued
```

- `Removed` is not a lifecycle state; a removed task leaves the list.
- `Paused → Queued` is legal: it is how a *restored* pause goes back to waiting
  when it has no worker to resume (see below).
- `Paused` is in `ACTIVE_STATES` so that "is this still unfinished work?"
  accounting (`has_active`, `pause_all`, the rename guard) treats a paused
  download as alive. It is **not** an interrupted state, which is why restore
  treats it separately.

## 5. Restart

`_restore_queue` distinguishes two cases:

| Persisted status at exit | Restored as | Why |
|---|---|---|
| `Downloading`, `Analyzing`, `Starting`, `Merging`, `Verifying` | `Queued` (`error = "Restored after restart"`) | The worker is gone. Restoring the *status* would claim a worker that does not exist. |
| `Paused` | `Paused` | The user paused it. That decision outlives the process. |
| `Failed`, `Cancelled` | themselves | Still unfinished business — the user can retry them. |
| `Complete` | not restored to the list | It lives in history. |

`recover_unfinished()` then validates the `Queued` tasks only, so a restored
pause is never silently started — not even with `resume_on_startup`, which
calls `_start_next()` and therefore only ever picks `Queued` tasks.

**Resume after restart.** A restored `Paused` task has no worker thread, so
`resume_task` must not pretend one exists. It distinguishes the two cases:

- **Live pause** — the record still owns a running thread parked at the pause
  barrier: `control.resume()` and transition to `Downloading`. The existing
  worker continues from where it stopped.
- **Restored pause** — no live thread: transition `Paused → Queued` and let the
  scheduler start it normally. This is the path that previously left the task
  stuck in `Downloading` with no worker.

## 6. What the UI may claim

- **Never invent a number.** Where the backend reports "cannot measure"
  (`estimated_start_seconds = None`, `total = 0`) the UI says *unknown*.
- **Never infer runtime state from a preference.** The queue gate is rendered
  from `queue_paused`, not from a button the user last pressed.
- **Never reorder something the user cannot see.** Move up/down and drag &
  drop flip the view to manual order first, and say so.
- **Never change a setting on its own.** Suggestions carry an explicit button.

## 7. Threading

- All queue state is guarded by one `RLock` (`TaskManager._lock`).
- Worker threads are spawned in exactly one place (`_start_rec_locked`).
- `_active` is incremented when a worker is spawned and decremented in the
  worker's finalize, under the lock.
- Listeners are notified **outside** the lock. See §0.
- Nothing sleeps to "wait for" a state change; the only waits are bounded
  `join()`s in `retry_task`, `remove_task` and `prepare_for_exit`.
