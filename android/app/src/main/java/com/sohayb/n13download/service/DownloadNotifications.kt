package com.sohayb.n13download.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.sohayb.n13download.MainActivity
import com.sohayb.n13download.R
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus

/**
 * Builds N13's notifications and owns their identity.
 *
 * This class knows how a notification *looks*; it does not decide *whether* one
 * should exist — that is [NotificationPolicy], applied by
 * [DownloadNotificationCenter].  Keeping the two apart is what stops the old
 * situation where three different collectors each decided for themselves.
 *
 * Identity rules:
 *
 *  - One id per download ([notificationId]), stable for the life of the task, so
 *    every update replaces the previous notification in place instead of
 *    stacking a second one.  Completion, failure and cancellation reuse the same
 *    id as the progress notification they replace.
 *  - [SERVICE_STARTING_ID] is pinned to 0 so the placeholder the foreground
 *    service needs during startup can never collide with a real task id.
 *  - Every notification carries [EXTRA_KIND] and [EXTRA_TASK_ID] in its extras,
 *    which is what lets the service recognise its own leftovers in the shade
 *    after a process restart and clear the ones that no longer apply.
 */
class DownloadNotifications(private val context: Context) {

    private val manager = NotificationManagerCompat.from(context)

    init {
        createChannels()
    }

    /**
     * Channels are created once per process and never modified afterwards.
     *
     * `createNotificationChannels` is idempotent and, crucially, does **not**
     * override the user's own choices (importance, sound, blocking) for a
     * channel that already exists — so re-running this on every start is safe
     * and a user who silenced progress notifications stays silenced.
     */
    private fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val system = context.getSystemService(NotificationManager::class.java) ?: return

        // Progress updates must be silent; only real events may make a sound.
        val progress = NotificationChannel(
            CHANNEL_PROGRESS,
            context.getString(R.string.notif_channel_progress),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = context.getString(R.string.notif_channel_progress_desc)
            setShowBadge(false)
        }

        val events = NotificationChannel(
            CHANNEL_EVENTS,
            context.getString(R.string.notif_channel_events),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = context.getString(R.string.notif_channel_events_desc)
        }

        system.createNotificationChannels(listOf(progress, events))
    }

    /**
     * Live progress (or the paused resting state) for one download.
     *
     * The text is built from the row the engine last wrote — never from an
     * estimate — so the notification cannot claim progress the download has not
     * made.  An unknown total size produces an indeterminate bar plus a growing
     * byte count instead of a fake percentage.
     */
    fun progress(task: DownloadTask, decision: NotificationDecision): Notification {
        val known = task.hasKnownSize
        val percent = NotificationPolicy.displayPercent(task)
        val sizeText = if (known) {
            "${N13Format.humanSize(task.downloadedSize)} / ${N13Format.humanSize(task.totalSize)}"
        } else {
            N13Format.humanSize(task.downloadedSize)
        }

        val builder = base(CHANNEL_PROGRESS, task, KIND_PROGRESS)
            .setContentTitle(task.filename)
            .setContentText(progressText(task, sizeText, percent, known))
            .setOngoing(decision.ongoing)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, percent, !known)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            // Without this the foreground notification is only shown after the
            // system's ten-second grace period, which looks like a download that
            // started without telling anyone.
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)

        when (task.status) {
            TaskStatus.PAUSED -> {
                builder.addAction(action(task, ACTION_RESUME, R.string.action_resume, 1))
                builder.addAction(action(task, ACTION_CANCEL, R.string.action_cancel, 2))
                // Paused is the one state the user may clear from the shade: the
                // transfer is not running, so nothing depends on it being visible.
                // The dismissal is reported back so an unrelated refresh cannot
                // bring it straight back.
                builder.setAutoCancel(false)
                builder.setDeleteIntent(dismissIntent(task))
            }

            else -> {
                builder.addAction(action(task, ACTION_PAUSE, R.string.action_pause, 1))
                builder.addAction(action(task, ACTION_CANCEL, R.string.action_cancel, 2))
            }
        }

        return builder.build()
    }

    /**
     * The one-line summary under the filename.
     *
     * Phase states say what they are doing instead of printing "0 B/s", which is
     * what a merged, verified or analysed transfer would otherwise show while it
     * is not actually transferring anything.
     */
    private fun progressText(
        task: DownloadTask,
        sizeText: String,
        percent: Int,
        known: Boolean,
    ): String = when (task.status) {
        TaskStatus.PAUSED -> context.getString(R.string.notif_paused_detail, sizeText)
        TaskStatus.STARTING -> context.getString(R.string.notif_state_starting)
        TaskStatus.ANALYZING -> context.getString(R.string.notif_state_analyzing)
        TaskStatus.MERGING -> context.getString(R.string.notif_state_merging)
        TaskStatus.VERIFYING -> context.getString(R.string.notif_state_verifying)
        else -> {
            val speed = N13Format.formatSpeed(task.currentSpeed)
            val head = if (known) "$percent% · $sizeText · $speed" else "$sizeText · $speed"
            val eta = NotificationPolicy.displayEtaSeconds(task)
            if (eta == null) {
                head
            } else {
                "$head · " + context.getString(
                    R.string.notif_eta_left,
                    N13Format.formatEta(eta.toDouble()),
                )
            }
        }
    }

    /**
     * Shown only for the few hundred milliseconds between the service being
     * started and the first real task notification.  Android requires a
     * notification within seconds of `startForegroundService`, and this is the
     * placeholder that satisfies it without ever lingering: the moment a task is
     * selected the service re-promotes itself onto that task's own id.
     */
    fun serviceStarting(): Notification =
        base(CHANNEL_PROGRESS, null, KIND_STARTING)
            .setContentTitle(context.getString(R.string.app_name))
            .setContentText(context.getString(R.string.notif_preparing))
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, 0, true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()

    fun completed(task: DownloadTask): Notification =
        base(CHANNEL_EVENTS, task, KIND_TERMINAL)
            .setContentTitle(context.getString(R.string.notif_complete_title))
            .setContentText("${task.filename} · ${N13Format.humanSize(task.downloadedSize)}")
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText("${task.filename}\n${N13Format.humanSize(task.downloadedSize)}"),
            )
            .setShowWhen(true)
            .setWhen(task.completedAt ?: System.currentTimeMillis())
            .setAutoCancel(true)
            .setOngoing(false)
            .setSilent(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .build()

    fun failed(task: DownloadTask, error: String): Notification {
        // The reason has to survive the *collapsed* layout, because that is what
        // the user actually sees.  MIUI clips an expanded-text preview to its
        // first line, so a failure whose cause was only in the second line of
        // `bigText` read "Download failed / b.bin / Connection" — the reason cut
        // off mid-word.  It is therefore the first line of the big text, and part
        // of the collapsed text as well.
        val reason = error.ifBlank { context.getString(R.string.notif_failed_unknown) }
        val builder = base(CHANNEL_EVENTS, task, KIND_TERMINAL)
            .setContentTitle(context.getString(R.string.notif_failed_title))
            .setContentText("${task.filename} · $reason")
            .setStyle(NotificationCompat.BigTextStyle().bigText("$reason\n${task.filename}"))
            .setShowWhen(true)
            .setWhen(task.completedAt ?: System.currentTimeMillis())
            .setAutoCancel(true)
            .setOngoing(false)
            .setSilent(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_ERROR)
        if (task.isRetryable) {
            builder.addAction(action(task, ACTION_RETRY, R.string.action_retry, 3))
        }
        return builder.build()
    }

    fun post(id: Int, notification: Notification) {
        // Notifications are best-effort: a missing POST_NOTIFICATIONS grant on
        // Android 13+ must never break the download itself, and posting into a
        // blocked app would only throw.
        if (!manager.areNotificationsEnabled()) return
        runCatching { manager.notify(id, notification) }
    }

    fun cancel(id: Int) {
        runCatching { manager.cancel(id) }
    }

    /**
     * Removes every notification this app has posted.
     *
     * Used exactly once per process, by [DownloadNotificationCenter.resetShade],
     * before anything new is posted.  It is the only reliable way to clear
     * leftovers from a previous process: `getActiveNotifications()` is unusable
     * on some OEM builds, and a notification posted by an older version of the
     * app carries no record this one could look up.
     */
    fun cancelAll() {
        runCatching { manager.cancelAll() }
    }

    private fun base(channel: String, task: DownloadTask?, kind: String): NotificationCompat.Builder {
        val builder = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_n13)
            .setColor(context.getColor(R.color.n13_accent))
            .setShowWhen(false)
            .addExtras(
                Bundle().apply {
                    putString(EXTRA_KIND, kind)
                    putLong(EXTRA_TASK_ID, task?.id ?: 0L)
                },
            )

        builder.setContentIntent(contentIntent(task))
        return builder
    }

    /**
     * Tapping the body opens N13 on that download.
     *
     * The request code is the notification id, so two downloads never share a
     * `PendingIntent` — with a single shared code the extras of the last one
     * built would silently win and the wrong row would open.
     */
    private fun contentIntent(task: DownloadTask?): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (task != null) putExtra(MainActivity.EXTRA_OPEN_TASK_ID, task.id)
        }
        return PendingIntent.getActivity(
            context,
            task?.let { notificationId(it.id) } ?: 0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    /**
     * A notification action.
     *
     * `getForegroundService` rather than `getService`: an action can arrive when
     * the service is not running (a paused download whose service already
     * stopped), and a plain `startService` from the background is refused on
     * Android 8+.  The service posts its placeholder in `onCreate`, which
     * satisfies the five-second rule that comes with starting in the foreground.
     */
    private fun action(
        task: DownloadTask,
        actionName: String,
        labelRes: Int,
        requestOffset: Int,
    ): NotificationCompat.Action {
        val intent = Intent(context, DownloadService::class.java).apply {
            action = actionName
            putExtra(EXTRA_TASK_ID, task.id)
        }
        val pending = PendingIntent.getForegroundService(
            context,
            (notificationId(task.id) shl 3) or requestOffset,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Action(0, context.getString(labelRes), pending)
    }

    /** Reports a swipe so the dismissal can be honoured instead of undone. */
    private fun dismissIntent(task: DownloadTask): PendingIntent {
        val intent = Intent(context, NotificationDismissReceiver::class.java).apply {
            putExtra(EXTRA_TASK_ID, task.id)
        }
        return PendingIntent.getBroadcast(
            context,
            notificationId(task.id),
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    companion object {
        const val CHANNEL_PROGRESS = "n13_progress"
        const val CHANNEL_EVENTS = "n13_events"

        const val EXTRA_TASK_ID = "task_id"
        const val EXTRA_KIND = "n13_kind"

        const val KIND_PROGRESS = "progress"
        const val KIND_TERMINAL = "terminal"
        const val KIND_STARTING = "starting"

        const val ACTION_PAUSE = "com.sohayb.n13download.action.PAUSE"
        const val ACTION_RESUME = "com.sohayb.n13download.action.RESUME"
        const val ACTION_CANCEL = "com.sohayb.n13download.action.CANCEL"
        const val ACTION_RETRY = "com.sohayb.n13download.action.RETRY"

        /**
         * Notification id for a task.
         *
         * Stable for the life of the task, so every update — starting, 15%, 82%,
         * complete — replaces the previous one in place.  Room ids start at 1,
         * and [SERVICE_STARTING_ID] is pinned to 0 so the placeholder used during
         * service startup can never collide with a real task id even if the
         * autoincrement ever wrapped to zero.
         */
        fun notificationId(taskId: Long): Int = (TASK_ID_BASE + taskId).toInt()

        /** The inverse of [notificationId], for mapping a posted id back to its task. */
        fun taskIdOf(notificationId: Int): Long = notificationId - TASK_ID_BASE

        /** Placeholder id used only while the service is being brought up. */
        const val SERVICE_STARTING_ID = 0

        private const val TASK_ID_BASE = 2000L
    }
}
