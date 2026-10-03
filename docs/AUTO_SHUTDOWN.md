# Auto Shutdown

N13 can power the machine off once a download workload has finished. The feature
is a small state machine with an explicit eligibility policy, not a flag that
fires on a "download finished" event.

- Module: `core/auto_shutdown.py`
- Tests: `tests/test_auto_shutdown.py`, `tests/test_auto_shutdown_ui.py`
- API: `Api.get_auto_shutdown_status` / `set_auto_shutdown` / `cancel_auto_shutdown`
- UI: queue-strip toggle + countdown banner (`#qsShutdownBtn`, `#qsShutdownBanner`)

> **Design rule #1 — the safe failure mode is always "the machine stays on".**
> If the controller is uncertain about the current download state, it does not
> shut down. Every rule below is written to prefer *not* shutting down.

---

## 1. Preference vs. runtime state

These are two different things and are never conflated:

| | Where | Meaning |
|---|---|---|
| **Preference** | `AppConfig.shutdown_when_done` (persisted JSON) | The user wants a shutdown when the workload finishes. |
| **Runtime state** | `AutoShutdownController` (memory only) | What is actually happening right now. |

The runtime state is **never** written back to `shutdown_when_done`, and the UI
never infers the runtime state from the flag. `enabled = true` with
`state = "Waiting"` is a normal, expected combination.

The preference is **one-shot**: firing consumes it (it is set back to `false`
when the shutdown is handed to Windows).

## 2. Runtime state machine

```
        preference off / power unsupported
                     │
                     ▼
                 ┌──────────┐   arm    ┌──────────┐
                 │ DISABLED │─────────▶│  ARMED   │  (nothing to wait for yet)
                 └──────────┘          └────┬─────┘
                      ▲                     │ work appears
                      │                     ▼
                      │                ┌──────────┐
                      │                │ WAITING  │◀── active / queued / paused /
                      │                └────┬─────┘    scheduled work
                      │                     │
                      │        policy forbids (failed / cancelled)
                      │                     ▼
                      │                ┌──────────┐
                      │                │ BLOCKED  │
                      │                └──────────┘
                      │
                      │  policy allows   ┌───────────┐
                      └──────────────────│ COUNTDOWN │──▶ EXECUTED
                                         └─────┬─────┘
                          invalidated by an   │
                          event / the user /  ▼
                          a failed OS call ┌───────────┐
                                           │ CANCELLED │
                                           └───────────┘
                                               │
                                    power operation failed
                                               ▼
                                           ┌────────┐
                                           │ ERROR  │
                                           └────────┘
```

| State | Meaning |
|---|---|
| `Disabled` | Preference off, or the platform has no power control. |
| `Armed` | Preference on, nothing to wait for yet. |
| `Waiting` | Preference on, work is still in progress. |
| `Blocked` | Work finished, but the policy forbids a shutdown (e.g. failures). |
| `Countdown` | Windows shutdown scheduled; counting down. |
| `Cancelled` | A live countdown was aborted. `cancel_reason` says why. |
| `Executed` | The final safety check passed; Windows owns the shutdown. |
| `Error` | A power operation failed. `last_error` + a reason explain it. |

`Countdown` is the guard for the "exactly one countdown" invariant: an
evaluation that arrives while a countdown is live cannot schedule a second one.

## 3. What "done" means — the eligibility policy

`AutoShutdownPolicy.evaluate(queue_state)` is the **only** place the rules live.
First match wins:

| # | Condition | Verdict | Reason |
|---|---|---|---|
| 1 | an active transfer — `Analyzing`, `Starting`, `Downloading`, `Merging`, `Verifying` | blocked | `active_task` |
| 2 | a queued task | blocked | `queued_task` — or `scheduled_task_pending` when the scheduler window is gating |
| 3 | a paused task | blocked | `paused_task` |
| 4 | a failed task, and `shutdown_allow_failures` is off | blocked | `task_failed` |
| 5 | a cancelled task, and `shutdown_allow_cancelled` is off | blocked | `task_cancelled` |
| 6 | a future one-off `schedule_time` | blocked | `scheduled_task_pending` |
| 7 | nothing has finished yet | blocked | `no_workload` |
| 8 | otherwise | **eligible** | — |

Notes:

- **`Removed` is not finished work.** A task that was removed is gone, not done.
  "Add a download, then remove it" therefore shuts nothing down.
- **Rule 7 is the anti-surprise rule.** Arming auto-shutdown on an empty queue
  must never power the machine off.
- **`Merging` / `Verifying` count as active.** A transfer that is still merging
  segments is not finished.
- **Failures and cancellations are never silently treated as success.** They
  block by default and must be explicitly opted into via
  `shutdown_allow_failures` / `shutdown_allow_cancelled` (both default `false`).

## 4. Countdown behaviour

- Length: `shutdown_countdown_seconds`, default **60**. `AppConfig` clamps it to
  5–3600; the controller clamps again to 1–3600 so a hand-edited config file
  cannot produce an instant shutdown.
- The OS call is `shutdown /s /t <N> /c "<fixed comment>"`, always with an
  argument array and `shell=False`. The comment is a module constant — **no task
  metadata ever reaches the command line.**
- **Final safety check.** A timer fires 5 seconds before the deadline (the grace
  is capped at half the countdown, so a 5-second countdown is still verified).
  It re-reads the queue, re-runs the policy, and if the result is not "still
  eligible" it issues `shutdown /a` and reports the block. Only if it passes is
  the state set to `Executed` and the preference consumed.
- If the timer cannot be armed at all, the shutdown is **aborted** — an
  unverified shutdown is treated as unsafe.
- A failed `shutdown /s` never produces `Countdown`. The state becomes `Error`
  with reason `schedule_failed` (or `power_unsupported`), and retries are
  rate-limited by a 30-second cooldown so Windows is not hammered.

## 5. Cancellation

Any event that makes the policy ineligible while a countdown is live cancels the
pending OS shutdown. On Windows this is `shutdown /a`, and the exit code is
checked:

- `0` → cancelled.
- `1116` ("no shutdown in progress") → treated as **success**; cancelling is
  idempotent.
- anything else → `PowerError`. The controller keeps `pending = true`, moves to
  `Error` with reason `cancel_failed`, and **retries the abort on the next
  evaluation**. It never claims a cancellation that did not happen.

Cancellation reasons are a structured enum (`ShutdownReason`), not free text:
`new_download`, `download_resumed`, `task_retrying`, `task_queued`,
`task_started`, `queue_changed`, `user_cancelled`, `user_disabled`,
`policy_not_eligible`, `final_check_failed`, `active_task`, `queued_task`,
`paused_task`, `task_failed`, `task_cancelled`, `scheduled_task_pending`,
`no_workload`, `system_error`, `power_unsupported`, `schedule_failed`,
`cancel_failed`, `app_exiting`, `stale_session`.

`Cancelled` is observable: after an abort the state stays `Cancelled` (it is not
overwritten microseconds later by a re-evaluation), and the *next* meaningful
event settles the machine into its real `Waiting` / `Armed` / `Blocked` state.

## 6. The countdown race condition

The scenario the previous implementation could not handle:

```
last download finishes  →  shutdown /s /t 60  →  user adds a download
                        →  download starts    →  Windows shuts down anyway
```

This is now impossible, because the countdown is *state*, not a fire-and-forget
OS call:

1. While `state == Countdown`, **every** queue event is checked against the
   policy. A new task, a resume, a retry becoming pending, a task starting — any
   of them aborts the pending shutdown with `shutdown /a` before returning.
2. Even if no event reaches the controller (a lost notification, a
   scheduler-driven start), the **final safety check** re-reads the live queue
   five seconds before the deadline and aborts if the answer changed.
3. The state machine cannot be tricked into a second countdown: `Countdown` plus
   a live `pending` flag short-circuit `_start_countdown_locked`, and the
   controller's lock is held across the OS call, so N simultaneous `finished`
   events produce exactly one `shutdown /s`.

Verified by `tests/test_auto_shutdown.py::CountdownRaceTest`,
`::ConcurrencyTest` (including an 8-thread parallel-`notify` test),
`::FinalSafetyCheckTest` and `::AdversarialTest`.

## 7. Pause / resume

A **paused** task blocks the shutdown (`Waiting` / `paused_task`). It does not
disarm the preference. So:

```
PAUSED (blocked) → RESUMED (blocked, active) → COMPLETED (eligible → countdown)
```

Resuming a paused task during a live countdown cancels the countdown
(`download_resumed`), and it can only become eligible again once it genuinely
finishes. A paused task can never accidentally allow a shutdown.

## 8. Failed and cancelled downloads

Blocked by default, with distinct reasons (`task_failed`, `task_cancelled`) so
the UI can explain *why* the machine is staying on. Opt in with:

- `shutdown_allow_failures` — shut down even though some downloads failed.
- `shutdown_allow_cancelled` — shut down even though the user cancelled some.

These are explicit policy knobs; the behaviour is never a side effect of generic
"terminal state" logic.

## 9. Retries

A retry is a task going `Failed → Queued → Downloading`. While a retry is
pending the task is `Queued`, which rule 2 blocks. If a retry becomes pending
during a live countdown the countdown is cancelled with reason `task_retrying`.
There is no window in which a retry-pending task permits a shutdown.

## 10. Scheduled downloads

N13's scheduler is **queue-wide**: it gates *existing* queued tasks inside a time
window and never creates new downloads. A queued task already blocks shutdown on
its own; the gate is passed to the policy only so the UI can report
`scheduled_task_pending` ("waiting for the scheduler window") instead of a
generic reason. Closing the gate during a live countdown cancels it.

The legacy one-off `AppConfig.schedule_time` is also honoured: a **future** value
blocks shutdown (`scheduled_task_pending`); a past one does not.

## 11. Newly added tasks — scope model

Auto shutdown uses the **dynamic queue** model: it means *"shut down when the
entire current N13 workload is complete"*. Tasks added during the session become
part of that workload, and a task added during a live countdown cancels it.

This is the safer of the two candidate models for a download manager: a snapshot
model would shut the machine down while a freshly added download was still
running.

## 12. Removing or clearing tasks

Policy-driven, not blind:

- Removing one of several completed tasks → the policy is still eligible, so the
  countdown **continues**.
- Removing (or clearing) the **last** counted task → nothing finished remains, so
  rule 7 (`no_workload`) applies and the countdown is **cancelled**, returning to
  `Armed`. "Clear completed" is therefore a way to call off an armed shutdown.

## 13. Recovery and application close

A marker file `%LOCALAPPDATA%\N13\auto_shutdown.json` is written **before** the
OS call and removed on a clean cancel, on execute, and on exit. Its presence at
startup means the previous process died mid-countdown.

| Situation | Behaviour |
|---|---|
| Startup finds a stale marker | `shutdown /a` — the safe direction. Failure keeps the marker so the next launch retries. |
| Startup finds no marker | No-op. |
| Marker is corrupt / not `Countdown` | Discarded. |
| Platform has no power control | Marker cleared; nothing is retried. |
| N13 closed normally (`Api.shutdown`) | Any pending N13-scheduled shutdown is **cancelled** — once N13 has exited it can no longer re-validate eligibility. |

### After a crash, is the shutdown re-armed?

**Yes, if the queue is still eligible.** The preference is one-shot and is consumed by *firing*,
not by a crash. So a crash mid-countdown is handled as: abort the stale OS shutdown, then run the
normal evaluation — which re-arms a fresh countdown if the workload is still complete, and reports
`Waiting` / `Armed` if it is not.

This is deliberate: the user asked for the machine to power off once the work was done, the work
still is done, and the new countdown is visible and cancellable in the UI. Killing N13 is not a
supported way to call off a shutdown — use **Cancel shutdown**, or turn the setting off.

**N13 closing is not Windows shutting down.** To let a countdown complete, keep
N13 running (minimise to tray); do not close it.

## 14. User-initiated cancellation

The countdown banner's **Cancel shutdown** button (and
`Api.cancel_auto_shutdown`) will:

1. issue `shutdown /a` and verify it;
2. set the runtime state to `Disabled`;
3. disarm the preference (`shutdown_when_done = false`, persisted) so the very
   next evaluation cannot immediately schedule a fresh countdown — the
   cancel → schedule loop is structurally impossible;
4. record the reason (`user_cancelled`) and update the UI.

## 15. UI

The queue strip renders the **runtime state**, never the preference flag:

| Runtime state | Strip label |
|---|---|
| `Disabled` | Off |
| `Armed` | Armed |
| `Waiting` | Waiting |
| `Blocked` | Blocked |
| `Countdown` / `Executed` | Shutting down |
| `Cancelled` | Cancelled |
| `Error` | Error |

The button's tooltip shows the structured reason ("Auto shutdown · A new
download was added"). While a countdown is live the strip shows a banner:

```
Shutdown scheduled
The computer shuts down in 42 seconds          [42s]  [Cancel shutdown]
```

Toasts fire on **transitions** only (so a repeated evaluation cannot spam the
user) and carry the structured reason, e.g. "Auto shutdown cancelled — A new
download was added" or "Auto shutdown blocked — Some downloads failed".

`Api.get_auto_shutdown_status()` returns
`enabled, state, supported, pending, countdown_seconds, seconds_remaining,
deadline, reason, cancel_reason, blocked_reason, last_error, policy, session`.
The frontend is pushed the same payload as an `auto_shutdown_state` event.

## 16. Configuration

| Key | Default | Meaning |
|---|---|---|
| `shutdown_when_done` | `false` | The user preference (persisted, one-shot). |
| `shutdown_countdown_seconds` | `60` | Abort window, clamped 5–3600. |
| `shutdown_allow_failures` | `false` | Shut down despite failed downloads. |
| `shutdown_allow_cancelled` | `false` | Shut down despite cancelled downloads. |

## 17. Testability and safety rails

- `PowerController` is the **only** interface that may touch the OS power state.
  Business logic never calls `shutdown.exe`. This includes the interactive
  console menu (`ui/menu.py::shutdown_computer`), which delegates its Windows
  branch to `WindowsPowerController` rather than shelling out on its own — so
  the validated delay, `shell=False`, the kill switch and sanitised errors apply
  to the CLI too. (Its POSIX branch stays native: the controller is Windows-only
  by design.)
- `FakePowerController` records `schedule_shutdown` / `cancel_shutdown` /
  `shutdown_now` and supports failure injection. Every test uses it, so no test
  can power off a machine.
- `ManualTimerFactory` makes the countdown and the final safety check
  deterministic — no sleeping.
- `N13_NO_REAL_SHUTDOWN=1` makes the real controller report itself as
  unsupported, so nothing in the process can power the machine off (CI, dry runs).

### Safety invariants (tested)

1. If any shutdown-blocking task exists, Windows shutdown must not be scheduled.
2. There is never more than one active countdown.
3. A new blocking task during `Countdown` invalidates the pending shutdown.
4. The runtime state never depends solely on the preference flag.
5. A failed OS scheduling command never produces `Countdown`.
6. An unreadable queue, scheduler or snapshot resolves to "do not shut down".

## 18. Known limitations

- **One controller per process.** Two instances would each own a countdown; the
  application's single-instance guard prevents this.
- **External power changes are not detected.** If something else issues
  `shutdown /a` (or `/s`) behind N13's back, N13 cannot tell; it may report
  `Executed` for a shutdown that was aborted externally. The failure mode is
  "machine stays on", which is the safe direction.
- **The OS call is made while holding the controller lock.** A `shutdown.exe`
  invocation is bounded by a 10-second subprocess timeout, so a pathological
  Windows could stall an event handler for up to that long. The alternative
  (releasing the lock) would allow two concurrent `shutdown /s` commands.
- **`shutdown /a` "nothing pending" detection** is exit code `1116` plus a
  case-insensitive match on the message ("no shutdown … in progress"), which
  covers the English Windows message. A localised message with a different exit
  code would be reported as a genuine cancel failure (a false negative, never a
  false success).
- **The final safety check leaves a 5-second blind spot.** Work that appears in
  the last five seconds before the deadline is caught by Windows' own countdown,
  not by N13. This is why the grace is capped at half the countdown.
