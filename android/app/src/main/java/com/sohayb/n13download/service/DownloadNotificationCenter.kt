package com.sohayb.n13download.service

import android.app.Notification
import android.content.Context
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * The single writer of N13's notification shade.
 *
 * Before this existed, three things posted notifications independently: a
 * progress collector driven by the database, a terminal-event collector driven
 * by the queue, and the foreground service promoting one of them.  They had no
 * ordering guarantee relative to each other, so a progress row read a moment
 * before a completion could land *after* it and repaint a finished download as
 * still running — and the service could cancel a notification the event
 * collector had just replaced with "completed".
 *
 * The rule now is simple and absolute: **the persisted download row is the only
 * source of truth**, and exactly one coroutine turns rows into notifications.
 * Events are not consulted at all, so there is no ordering to get wrong:
 *
 *  - Every decision is made from the current row, never from an event payload.
 *  - The whole render pass runs under one mutex, so two passes cannot interleave
 *    and an older pass cannot finish after a newer one.
 *  - A pass only touches a notification when what the user sees actually
 *    changed ([NotificationFingerprint]), so repeated emissions cost nothing.
 *
 * A task's state is remembered ([lastStatus]) only to tell a *new* terminal
 * state from one already announced — never to decide what a notification says.
 *
 * # Notifications do not outlive the process
 *
 * Notification ids are derived from the task id ([DownloadNotifications.notificationId]),
 * so *cancelling* one never requires a record of having posted it.  What does
 * require a record is the opposite question — "is what I see in the shade still
 * true?" — and the system cannot answer it reliably: `getActiveNotifications()`
 * is documented but returns an empty list on some OEM builds (verified on the
 * MIUI device this app is tested against), which would silently disable
 * reconciliation exactly where it is needed.
 *
 * So the shade is treated as **session-scoped**: [resetShade] empties it once,
 * at process start, before anything is posted, and the first render pass then
 * rebuilds it from the rows.  That is exact rather than best-effort — it also
 * clears notifications posted by a build that had no bookkeeping at all — and it
 * needs no persistence.  The user-visible rule is simple and predictable: a
 * notification always describes a download in *this* session; finished-download
 * notifications are announced when they happen and live until dismissed or the
 * app is restarted, never longer.
 */
class DownloadNotificationCenter(
    context: Context,
    private val settings: () -> DownloadSettings,
) {

    private val builder = DownloadNotifications(context)

    /** Serialises render passes: one pass at a time, in the order they arrived. */
    private val mutex = Mutex()

    /** What this process has put in the shade, keyed by notification id. */
    private val posted = HashMap<Int, Posted>()

    /** Last status rendered per task, so a terminal state is announced once. */
    private val lastStatus = HashMap<Long, TaskStatus>()

    /**
     * Tasks whose notification the user swiped away while paused.
     *
     * Concurrent because the dismissal arrives on the main thread from a
     * broadcast while a render pass runs on a background dispatcher.  Cleared the
     * moment the task changes state, so resuming a download brings its
     * notification back — and nothing else does.
     */
    private val dismissed: MutableSet<Long> = ConcurrentHashMap.newKeySet()

    /** False until the first pass after process start has rendered. */
    private var reconciled = false

    /** True once the shade has been emptied after notifications were switched off. */
    private var suppressed = false

    /** Diagnostics: the ids this process believes it has in the shade. */
    fun postedIds(): List<Int> = synchronized(posted) { posted.keys.sorted() }

    /** The notification the foreground service shows while it boots. */
    fun serviceStartingNotification(): Notification = builder.serviceStarting()

    /** Removes the startup placeholder once it is no longer needed. */
    fun clearPlaceholder() {
        // The placeholder never enters [posted]: it is handed straight to
        // `startForeground` and never posted through this class, so there is no
        // bookkeeping to undo and no lock to take.
        builder.cancel(DownloadNotifications.SERVICE_STARTING_ID)
    }

    /**
     * Empties the shade at the start of a new process.
     *
     * Must be called exactly once, from the application container, before any
     * render pass and before the foreground service exists — which is guaranteed,
     * because `Application.onCreate` always precedes `Service.onCreate`.
     *
     * Nothing in the shade can be trusted at this point.  It was put there by a
     * process that is gone, and the rows it described may have changed, or the
     * download may have been deleted, while that process was dead.  Clearing
     * first and then rebuilding from the rows is the only reconciliation that is
     * exact rather than approximate: it needs no knowledge of what was posted,
     * and it removes leftovers from older builds that kept no records at all.
     */
    fun resetShade() {
        builder.cancelAll()
        // Nothing can be in [posted] yet, but be explicit about the invariant.
        synchronized(posted) { posted.clear() }
        lastStatus.clear()
        dismissed.clear()
    }

    /**
     * Brings the shade in line with [tasks] — the authoritative snapshot.
     *
     * Returns the notification the service should promote itself onto: the first
     * download that is *actually running*.  A paused task never anchors the
     * foreground service, because a service kept alive for a transfer that is not
     * moving is exactly the battery drain this app is not allowed to have — and
     * because a foreground notification the user may swipe away would leave the
     * service without one.
     */
    suspend fun render(tasks: List<DownloadTask>): RenderResult = mutex.withLock {
        val settings = settings()
        val reconcile = !reconciled
        reconciled = true

        // Notifications switched off in N13: empty the shade once, then stay
        // quiet.  The status map is kept current so nothing is re-announced when
        // they are switched back on.
        if (!settings.notificationsEnabled) {
            if (!suppressed) {
                suppressed = true
                val shown = synchronized(posted) { posted.keys.toList() }
                shown.forEach {
                    builder.cancel(it)
                    posted.remove(it)
                }
            }
            tasks.forEach { lastStatus[it.id] = it.status }
            return@withLock RenderResult(null, null, emptySet())
        }
        suppressed = false

        val live = HashSet<Int>()
        var anchorTask: DownloadTask? = null
        var anchorNotification: Notification? = null

        for (task in tasks) {
            val previous = lastStatus[task.id]
            val changed = previous != null && previous != task.status
            // A real transition re-arms a dismissal; a refresh does not.
            if (changed) dismissed.remove(task.id)

            val decision = NotificationPolicy.decide(
                task = task,
                previous = previous,
                settings = settings,
                reconcile = reconcile,
                dismissed = task.id in dismissed,
            )
            lastStatus[task.id] = task.status

            val id = DownloadNotifications.notificationId(task.id)
            when (decision.kind) {
                NotificationKind.NONE -> {
                    // Only ever clear something that exists: a progress
                    // notification that is no longer warranted, or a terminal one
                    // whose task has moved on (the user retried it, or it was
                    // cancelled).  A stable terminal notification is left alone so
                    // an unrelated refresh cannot wipe it.
                    val shown = posted[id]
                    if (shown != null && (changed || shown.fingerprint.kind == NotificationKind.PROGRESS)) {
                        posted.remove(id)
                        builder.cancel(id)
                    }
                }

                NotificationKind.PROGRESS -> {
                    val fingerprint = NotificationPolicy.fingerprint(task, decision)
                    val shown = posted[id]
                    val notification = if (shown?.fingerprint == fingerprint) {
                        shown.notification
                    } else {
                        // Built and recorded in one step: the fingerprint is the
                        // record of *what we asked the system to show*, so a pass
                        // that changes nothing visible stays a no-op even when the
                        // POST_NOTIFICATIONS grant is missing and the call is a
                        // silent no-op.  The next real change re-posts it.
                        val fresh = builder.progress(task, decision)
                        builder.post(id, fresh)
                        posted[id] = Posted(fingerprint, fresh)
                        fresh
                    }
                    live += id
                    if (decision.ongoing && anchorTask == null) {
                        anchorTask = task
                        anchorNotification = notification
                    }
                }

                NotificationKind.COMPLETED,
                NotificationKind.FAILED,
                -> {
                    val fingerprint = NotificationPolicy.fingerprint(task, decision)
                    if (posted[id]?.fingerprint != fingerprint) {
                        val notification = if (decision.kind == NotificationKind.COMPLETED) {
                            builder.completed(task)
                        } else {
                            builder.failed(task, task.error)
                        }
                        builder.post(id, notification)
                        posted[id] = Posted(fingerprint, notification)
                    }
                }
            }
        }

        // Notifications whose download no longer exists at all (removed from the
        // list, or its history cleared).  Idempotent: the second pass finds
        // nothing to do.
        val present = tasks.mapTo(HashSet()) { DownloadNotifications.notificationId(it.id) }
        posted.keys.filter { it !in present }.forEach { id ->
            builder.cancel(id)
            posted.remove(id)
        }
        val presentTaskIds = tasks.mapTo(HashSet()) { it.id }
        lastStatus.keys.retainAll(presentTaskIds)
        dismissed.retainAll(presentTaskIds)

        RenderResult(anchorTask?.id, anchorNotification, live)
    }

    /**
     * Called after the service leaves the foreground.
     *
     * Leaving with `STOP_FOREGROUND_DETACH` keeps the notification on screen,
     * which is what we want for a finished download — but it also means a
     * cancellation issued while the notification *was* the foreground one may
     * have been ignored by the system.  Now that the service is out of the way,
     * any id that should not exist is cleared for real.  Idempotent.
     */
    suspend fun releaseForeground(notificationId: Int) = mutex.withLock {
        if (!posted.containsKey(notificationId)) builder.cancel(notificationId)
    }

    /**
     * Records that the user swiped a notification away.
     *
     * Only paused downloads are dismissible, so this only ever affects them.  The
     * download itself is untouched: it stays paused, keeps its bytes, and stays
     * resumable from the app.  The notification comes back only when the task
     * changes state — which is a legitimate lifecycle transition, not a refresh.
     */
    fun onDismissed(taskId: Long) {
        if (taskId <= 0L) return
        // Only the dismissal set is touched here: [posted] belongs to the render
        // pass, and this arrives on the main thread.  The next pass sees the
        // dismissal and stops showing the notification, which is exactly what the
        // swipe already achieved on screen.
        dismissed.add(taskId)
    }

    /**
     * Forgets a task completely, cancelling anything it still has in the shade.
     *
     * Called when a download is removed from the list.  The render pass gets
     * there on its own — a task missing from the snapshot is swept — but only
     * after the flow emits, and only if the notification was posted by this
     * process.  An explicit signal makes removal immediate and independent of
     * that timing.  Idempotent.
     */
    suspend fun forget(taskId: Long) = mutex.withLock {
        val notificationId = DownloadNotifications.notificationId(taskId)
        posted.remove(notificationId)
        builder.cancel(notificationId)
        lastStatus.remove(taskId)
        dismissed.remove(taskId)
    }

    /**
     * Clears every finished-download notification.
     *
     * Used when the history is emptied: the rows are gone, so the notifications
     * describing them are no longer true.  Progress notifications are untouched —
     * clearing history never cancels a running transfer.
     */
    suspend fun forgetFinished() = mutex.withLock {
        val finished = posted.keys.filter { id ->
            lastStatus[DownloadNotifications.taskIdOf(id)]?.isTerminal == true
        }
        finished.forEach {
            posted.remove(it)
            builder.cancel(it)
        }
    }

    private data class Posted(
        val fingerprint: NotificationFingerprint,
        val notification: Notification,
    )
}

/**
 * What one render pass produced.
 *
 * [anchorTaskId]/[anchorNotification] describe the download the foreground
 * service should own, or `null` when nothing is running.
 */
data class RenderResult(
    val anchorTaskId: Long?,
    val anchorNotification: Notification?,
    val liveIds: Set<Int>,
)
