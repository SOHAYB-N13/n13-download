package com.sohayb.n13download.service

import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus

/**
 * What the notification shade should show for one download.
 *
 * Deliberately a value type rather than "post this notification": the decision is
 * the part worth testing, and it is the part that used to be scattered across a
 * progress collector, a terminal-event collector and a service that all posted
 * notifications independently.
 */
enum class NotificationKind {
    /** Nothing belongs in the shade for this task right now. */
    NONE,

    /** Live progress — a running or paused transfer. */
    PROGRESS,

    /** A download that finished successfully. */
    COMPLETED,

    /** A download that failed. */
    FAILED,
}

/**
 * The notification a task should own, and whether the user may swipe it away.
 *
 * [ongoing] is not cosmetic: an ongoing notification cannot be dismissed, which is
 * exactly right while bytes are moving (the transfer must not depend on the
 * shade) and wrong for a paused download the user wants out of the way.
 */
data class NotificationDecision(
    val kind: NotificationKind,
    val ongoing: Boolean = false,
) {
    companion object {
        val Hidden = NotificationDecision(NotificationKind.NONE)
    }
}

/**
 * The rules that turn a download's real state into a notification decision.
 *
 * Pure — no `Context`, no `Notification`, no coroutines — because this is where
 * the bugs that matter live.  A wrong answer here is either a notification that
 * lies about a download (says "downloading" when it is queued, says "failed"
 * when the user already retried it) or one that never goes away.  Everything
 * Android-specific lives in [DownloadNotificationCenter]; this only answers the
 * policy question, so every state can be tested on the JVM.
 *
 * The state is *always* the persisted row.  Nothing here trusts an event, a
 * timer, or the previous notification: [previous] is used only to tell "this
 * terminal state is new" from "this terminal state was already announced", which
 * is what stops a finished download from re-announcing itself every time the
 * table changes.
 */
object NotificationPolicy {

    /**
     * @param previous the status this process last rendered for the task, or
     *   `null` when it has not been seen since the process (or the service)
     *   started.
     * @param reconcile true on the first pass after start.  Terminal states found
     *   then are *recorded*, not announced: they belong to a previous session and
     *   re-posting them would resurrect notifications the user already dealt with.
     * @param dismissed true when the user swiped this task's notification away
     *   while it was paused.  A dismissal is honoured until the task changes
     *   state, so an unrelated refresh cannot bring it back.
     */
    fun decide(
        task: DownloadTask,
        previous: TaskStatus?,
        settings: DownloadSettings,
        reconcile: Boolean = false,
        dismissed: Boolean = false,
    ): NotificationDecision {
        // Nothing at all when the user switched notifications off in N13.
        if (!settings.notificationsEnabled) return NotificationDecision.Hidden

        return when (task.status) {
            // Waiting, discarded, gone: a queued download must never look like an
            // active one, and a cancelled or removed one must leave no trace.
            TaskStatus.QUEUED,
            TaskStatus.CANCELLED,
            TaskStatus.REMOVED,
            -> NotificationDecision.Hidden

            // Real work in progress.  Every one of these is ongoing: the user
            // cannot swipe away a transfer that is writing bytes.
            TaskStatus.ANALYZING,
            TaskStatus.STARTING,
            TaskStatus.DOWNLOADING,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
            -> NotificationDecision(NotificationKind.PROGRESS, ongoing = true)

            // A paused task only deserves a notification once it has actually
            // started — parking a waiting task is not something to report.
            TaskStatus.PAUSED ->
                if (task.startedAt == null || dismissed) {
                    NotificationDecision.Hidden
                } else {
                    NotificationDecision(NotificationKind.PROGRESS, ongoing = false)
                }

            TaskStatus.COMPLETED ->
                if (reconcile || previous == TaskStatus.COMPLETED) {
                    NotificationDecision.Hidden
                } else if (settings.notifyCompleted) {
                    NotificationDecision(NotificationKind.COMPLETED)
                } else {
                    NotificationDecision.Hidden
                }

            TaskStatus.FAILED ->
                if (reconcile || previous == TaskStatus.FAILED) {
                    NotificationDecision.Hidden
                } else if (settings.notifyFailed) {
                    NotificationDecision(NotificationKind.FAILED)
                } else {
                    NotificationDecision.Hidden
                }
        }
    }

    /** Progress percentage the notification prints, 0 when the size is unknown. */
    fun displayPercent(task: DownloadTask): Int =
        if (task.hasKnownSize) task.percent.toInt().coerceIn(0, 100) else 0

    /**
     * Remaining time worth printing, in whole seconds, or `null` for "don't".
     *
     * Rounded down to [ETA_BUCKET_SECONDS] so the number is stable: an ETA that
     * ticks every second would rebuild the notification every second for text
     * that says the same thing.  Anything past [MAX_ETA_SECONDS] is not a
     * prediction, it is a guess, so it is dropped rather than shown.
     */
    fun displayEtaSeconds(task: DownloadTask): Long? {
        if (task.status != TaskStatus.DOWNLOADING) return null
        if (!task.hasKnownSize) return null
        val eta = task.etaSeconds ?: return null
        if (eta <= 0.0 || eta.isNaN() || eta.isInfinite()) return null
        if (eta > MAX_ETA_SECONDS) return null
        val bucket = (eta / ETA_BUCKET_SECONDS).toLong() * ETA_BUCKET_SECONDS
        // Bucketing exists so the text does not repaint every second.  Rounding
        // down, though, turned any estimate under ten seconds into zero and the
        // line vanished — precisely during the last seconds, when the user is
        // watching it.  Clamp to one bucket instead.
        return bucket.coerceAtLeast(ETA_BUCKET_SECONDS)
    }

    /**
     * Everything a notification renders, for change detection.
     *
     * Two notifications with equal fingerprints are indistinguishable to the
     * user, so the second one is never posted: no binder round trip, no SystemUI
     * rebuild, no wake-up.  Byte counts are compared in [BYTE_BUCKET] steps and
     * speeds in [SPEED_BUCKET] steps — both finer than the printed value, so the
     * text can never be left stale, only spared needless redraws.
     */
    fun fingerprint(task: DownloadTask, decision: NotificationDecision): NotificationFingerprint =
        NotificationFingerprint(
            kind = decision.kind,
            filename = task.filename,
            percent = displayPercent(task),
            indeterminate = !task.hasKnownSize,
            downloadedBucket = task.downloadedSize / BYTE_BUCKET,
            totalSize = task.totalSize,
            speedBucket = (task.currentSpeed / SPEED_BUCKET).toLong(),
            etaBucket = displayEtaSeconds(task) ?: 0L,
            status = task.status,
            error = if (task.status == TaskStatus.FAILED) task.error else "",
            ongoing = decision.ongoing,
        )

    /** ~512 KiB: an unknown-length download still refreshes on real progress. */
    private const val BYTE_BUCKET = 512L * 1024L

    /** 100 KB/s — finer than the printed speed, coarser than the raw sample. */
    private const val SPEED_BUCKET = 100.0 * 1024.0

    /** ETA is printed to this resolution. */
    private const val ETA_BUCKET_SECONDS = 10L

    /** Past six hours a "remaining time" is noise, not information. */
    private const val MAX_ETA_SECONDS = 6.0 * 3600.0
}

/**
 * The rendered identity of a notification.
 *
 * A `data class` on purpose: equality *is* the "has anything the user can see
 * changed?" test.
 */
data class NotificationFingerprint(
    val kind: NotificationKind,
    val filename: String,
    val percent: Int,
    val indeterminate: Boolean,
    val downloadedBucket: Long,
    val totalSize: Long,
    val speedBucket: Long,
    val etaBucket: Long,
    val status: TaskStatus,
    val error: String,
    val ongoing: Boolean,
)
