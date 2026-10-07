package com.sohayb.n13download.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.sohayb.n13download.MainActivity
import com.sohayb.n13download.R
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus

/**
 * N13 notifications.
 *
 * One notification per active download, carrying the filename, percentage,
 * progress, downloaded/total and live speed, with Pause/Resume/Cancel actions —
 * exactly the N13 "active download" notification.  Completion, failure and
 * cancellation reuse the same id, so a finished download replaces its own
 * progress notification instead of stacking a second one.
 *
 * Because a download is never active without a notification, the foreground
 * service uses the first active task's notification as its own and posts nothing
 * extra.
 */
class DownloadNotifications(private val context: Context) {

    private val manager = NotificationManagerCompat.from(context)

    init {
        createChannels()
    }

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val system = context.getSystemService(NotificationManager::class.java) ?: return

        // Progress updates must be silent; only real events may make a sound.
        val progress = NotificationChannel(
            CHANNEL_PROGRESS,
            "Download progress",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Live progress of running downloads"
            setShowBadge(false)
        }

        val events = NotificationChannel(
            CHANNEL_EVENTS,
            "Download events",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Completed and failed downloads"
        }

        system.createNotificationChannels(listOf(progress, events))
    }

    /** Live progress for a running download. */
    fun progress(task: DownloadTask, totalSpeed: Double): Notification {
        val percent = task.percent.toInt().coerceIn(0, 100)
        val known = task.hasKnownSize

        val text = if (known) {
            "${N13Format.humanSize(task.downloadedSize)} / ${N13Format.humanSize(task.totalSize)}" +
                " · ${N13Format.formatSpeed(task.currentSpeed)}"
        } else {
            "${N13Format.humanSize(task.downloadedSize)} · ${N13Format.formatSpeed(task.currentSpeed)}"
        }

        val builder = base(CHANNEL_PROGRESS, task)
            .setContentTitle(task.filename)
            .setContentText(text)
            .setSubText(if (known) "$percent%" else null)
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, percent, !known)
            .setPriority(NotificationCompat.PRIORITY_LOW)

        when (task.status) {
            TaskStatus.PAUSED -> {
                builder.addAction(action(task, ACTION_RESUME, "Resume", 1))
                builder.addAction(action(task, ACTION_CANCEL, "Cancel", 2))
            }

            TaskStatus.QUEUED, TaskStatus.ANALYZING, TaskStatus.STARTING -> {
                builder.addAction(action(task, ACTION_PAUSE, "Pause", 1))
                builder.addAction(action(task, ACTION_CANCEL, "Cancel", 2))
            }

            else -> {
                builder.addAction(action(task, ACTION_PAUSE, "Pause", 1))
                builder.addAction(action(task, ACTION_CANCEL, "Cancel", 2))
            }
        }

        if (totalSpeed > 0.0 && task.status.isRunning) {
            builder.setContentInfo(N13Format.formatSpeed(totalSpeed))
        }
        return builder.build()
    }

    /** Shown when a download is starting but has no progress to report yet. */
    fun preparing(task: DownloadTask): Notification =
        base(CHANNEL_PROGRESS, task)
            .setContentTitle(task.filename)
            .setContentText("Starting…")
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, 0, true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .addAction(action(task, ACTION_CANCEL, "Cancel", 2))
            .build()

    /** Shown by the foreground service while it is being brought up. */
    fun serviceIdle(): Notification =
        base(CHANNEL_PROGRESS, null)
            .setContentTitle("N13 Download Manager")
            .setContentText("Preparing downloads…")
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, 0, true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    fun completed(task: DownloadTask): Notification =
        base(CHANNEL_EVENTS, task)
            .setContentTitle("Download complete")
            .setContentText("${task.filename} · ${N13Format.humanSize(task.downloadedSize)}")
            .setAutoCancel(true)
            .setOngoing(false)
            .setSilent(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()

    fun failed(task: DownloadTask, error: String): Notification {
        val builder = base(CHANNEL_EVENTS, task)
            .setContentTitle("Download failed")
            .setContentText(task.filename)
            .setStyle(NotificationCompat.BigTextStyle().bigText("${task.filename}\n$error"))
            .setAutoCancel(true)
            .setOngoing(false)
            .setSilent(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        if (task.isRetryable) {
            builder.addAction(action(task, ACTION_RETRY, "Retry", 3))
        }
        return builder.build()
    }

    fun cancelled(task: DownloadTask): Notification =
        base(CHANNEL_EVENTS, task)
            .setContentTitle("Download cancelled")
            .setContentText(task.filename)
            .setAutoCancel(true)
            .setOngoing(false)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    fun post(id: Int, notification: Notification) {
        // Notifications are best-effort: a missing POST_NOTIFICATIONS grant on
        // Android 13+ must never break the download itself.
        runCatching { manager.notify(id, notification) }
    }

    fun cancel(id: Int) {
        runCatching { manager.cancel(id) }
    }

    private fun base(channel: String, task: DownloadTask?): NotificationCompat.Builder {
        val builder = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_n13)
            .setColor(context.getColor(R.color.n13_accent))
            .setShowWhen(false)

        val contentIntent = PendingIntent.getActivity(
            context,
            task?.id?.toInt() ?: 0,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                if (task != null) putExtra(MainActivity.EXTRA_OPEN_TASK_ID, task.id)
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        builder.setContentIntent(contentIntent)
        return builder
    }

    private fun action(
        task: DownloadTask,
        actionName: String,
        label: String,
        requestOffset: Int,
    ): NotificationCompat.Action {
        val intent = Intent(context, DownloadService::class.java).apply {
            action = actionName
            putExtra(EXTRA_TASK_ID, task.id)
        }
        val pending = PendingIntent.getService(
            context,
            (task.id.toInt() * 10) + requestOffset,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Action(0, label, pending)
    }

    companion object {
        const val CHANNEL_PROGRESS = "n13_progress"
        const val CHANNEL_EVENTS = "n13_events"

        const val EXTRA_TASK_ID = "task_id"

        const val ACTION_PAUSE = "com.sohayb.n13download.action.PAUSE"
        const val ACTION_RESUME = "com.sohayb.n13download.action.RESUME"
        const val ACTION_CANCEL = "com.sohayb.n13download.action.CANCEL"
        const val ACTION_RETRY = "com.sohayb.n13download.action.RETRY"

        /** Notification id for a task.  Kept stable so updates replace in place. */
        fun notificationId(taskId: Long): Int = (TASK_ID_BASE + taskId).toInt()

        /** The foreground service reuses the first active task's notification. */
        const val SERVICE_NOTIFICATION_ID = 1

        private const val TASK_ID_BASE = 2000L
    }
}
