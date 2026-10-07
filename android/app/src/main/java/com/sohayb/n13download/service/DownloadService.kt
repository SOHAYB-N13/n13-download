package com.sohayb.n13download.service

import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.sohayb.n13download.N13Application
import com.sohayb.n13download.di.AppContainer
import com.sohayb.n13download.domain.download.TaskEvent
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Keeps the download queue alive while the user is elsewhere.
 *
 * The queue itself lives in the application process; this service exists so that
 * process is not frozen or killed while transfers are running, and so the user
 * gets the N13 progress notification.  It starts when work appears and stops
 * itself the moment the last download finishes, so it never lingers.
 *
 * A partial wake lock is held only while transfers are active, so the CPU keeps
 * servicing the sockets with the screen off without draining the battery when
 * nothing is running.
 */
class DownloadService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var container: AppContainer

    private var foregroundActive = false
    private var wakeLock: PowerManager.WakeLock? = null
    private var idleJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as N13Application).container

        // Android requires a foreground notification within a few seconds of
        // startForegroundService, so one is posted before anything else.
        promoteToForeground(container.notifications.serviceIdle())

        observeEvents()
        observeProgress()
        observeActivity()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            DownloadNotifications.ACTION_PAUSE -> controlTask(intent) { container.queue.pause(it) }
            DownloadNotifications.ACTION_RESUME -> controlTask(intent) { container.queue.resume(it) }
            DownloadNotifications.ACTION_CANCEL -> controlTask(intent) { container.queue.cancel(it) }
            DownloadNotifications.ACTION_RETRY -> controlTask(intent) { container.queue.retry(it) }
            else -> Unit
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        releaseWakeLock()
        scope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ //

    private fun controlTask(intent: Intent, action: suspend (Long) -> Unit) {
        val taskId = intent.getLongExtra(DownloadNotifications.EXTRA_TASK_ID, -1L)
        if (taskId <= 0L) return
        scope.launch { runCatching { action(taskId) } }
    }

    /** Completion / failure / cancellation notifications. */
    private fun observeEvents() {
        scope.launch {
            container.queue.events.collect { event ->
                val settings = container.settingsSnapshot()
                val notificationId = DownloadNotifications.notificationId(event.taskId)

                when (event) {
                    is TaskEvent.Completed -> {
                        container.notifications.cancel(notificationId)
                        val task = container.repository.get(event.taskId)
                        if (task != null && settings.notificationsEnabled && settings.notifyCompleted) {
                            container.notifications.post(
                                notificationId,
                                container.notifications.completed(task),
                            )
                        }
                    }

                    is TaskEvent.Failed -> {
                        container.notifications.cancel(notificationId)
                        val task = container.repository.get(event.taskId)
                        if (task != null && settings.notificationsEnabled && settings.notifyFailed) {
                            container.notifications.post(
                                notificationId,
                                container.notifications.failed(task, event.error),
                            )
                        }
                    }

                    is TaskEvent.Cancelled -> container.notifications.cancel(notificationId)
                    is TaskEvent.Started -> Unit
                }
            }
        }
    }

    /**
     * Live progress notifications.
     *
     * Driven by the repository's throttled progress rows rather than by the engine,
     * so there is exactly one source of truth and a notification can never disagree
     * with the list.
     */
    private fun observeProgress() {
        scope.launch {
            container.repository.observeAll().collectLatest { tasks ->
                val settings = container.settingsSnapshot()

                if (!settings.notificationsEnabled) {
                    tasks.forEach { container.notifications.cancel(DownloadNotifications.notificationId(it.id)) }
                    return@collectLatest
                }

                val notifiable = tasks.filter { it.isNotifiable() }
                val totalSpeed = notifiable.sumOf { it.currentSpeed }

                notifiable.forEach { task ->
                    container.notifications.post(
                        DownloadNotifications.notificationId(task.id),
                        container.notifications.progress(task, totalSpeed),
                    )
                }

                // A task that is no longer active loses its progress notification.
                tasks.filterNot { it.isNotifiable() }
                    .forEach { container.notifications.cancel(DownloadNotifications.notificationId(it.id)) }

                // The foreground notification follows the first active task, so the
                // service never shows a second, redundant notification.
                notifiable.firstOrNull()?.let { anchor ->
                    promoteToForeground(container.notifications.progress(anchor, totalSpeed))
                }
            }
        }
    }

    /** Starts and stops the service with the queue, and manages the wake lock. */
    private fun observeActivity() {
        scope.launch {
            container.queue.activeCount.collect { active ->
                if (active > 0) {
                    idleJob?.cancel()
                    idleJob = null
                    acquireWakeLock()
                } else {
                    releaseWakeLock()
                    // Give the queue a moment to pick up the next task before giving up.
                    if (idleJob == null) {
                        idleJob = launch {
                            delay(IDLE_GRACE_MILLIS)
                            if (container.queue.activeCount.value == 0) stopForegroundAndSelf()
                        }
                    }
                }
            }
        }
    }

    private fun promoteToForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                DownloadNotifications.SERVICE_NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(DownloadNotifications.SERVICE_NOTIFICATION_ID, notification)
        }
        foregroundActive = true
    }

    private fun stopForegroundAndSelf() {
        if (foregroundActive) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundActive = false
        }
        stopSelf()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val power = getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return
        wakeLock = runCatching {
            power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG).apply {
                setReferenceCounted(false)
                acquire()
            }
        }.onFailure { Log.w(TAG, "could not acquire the wake lock", it) }.getOrNull()
    }

    private fun releaseWakeLock() {
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
    }

    private companion object {
        const val TAG = "N13Service"
        const val WAKE_LOCK_TAG = "n13:downloads"
        const val IDLE_GRACE_MILLIS = 3_000L
    }
}

/**
 * Whether a task deserves a notification.
 *
 * Only work that has actually started: a task parked as PAUSED straight from the
 * waiting queue has no transfer to report and would just be noise.
 */
private fun DownloadTask.isNotifiable(): Boolean = when (status) {
    TaskStatus.PAUSED -> startedAt != null
    else -> status.isActive
}
