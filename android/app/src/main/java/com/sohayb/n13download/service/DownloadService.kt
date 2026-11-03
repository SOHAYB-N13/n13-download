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
import androidx.annotation.RequiresApi
import com.sohayb.n13download.N13Application
import com.sohayb.n13download.di.AppContainer
import com.sohayb.n13download.domain.download.TaskEvent
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Keeps the download queue alive while the user is elsewhere.
 *
 * The queue itself lives in the application process; this service exists so that
 * process is not frozen or killed while transfers are running, and so the user
 * gets progress notifications.
 *
 * # One notification per download
 *
 * The foreground notification *is* a task's progress notification.  The service
 * never owns a separate, long-lived notification of its own: it picks the first
 * active task, promotes itself onto **that task's id** with `startForeground`,
 * and every further progress tick updates the same id.  This is what removes the
 * old duplicate — the service used to post the anchor task under a fixed id
 * (`1`) while the task notification also lived under `2000 + taskId`, so the
 * user saw the same download listed twice.
 *
 * When the last transfer ends the service gives the queue a grace period to
 * start the next task, then drops out of the foreground and stops, leaving
 * completed/failed notifications behind under their own task ids.
 *
 * A partial wake lock is held only while transfers are active, so the CPU keeps
 * servicing the sockets with the screen off without draining the battery when
 * nothing is running.
 */
class DownloadService : Service() {

    /**
     * Off the main thread on purpose.
     *
     * Everything this scope does — reading rows, formatting notification text,
     * building `PendingIntent`s — used to run on `Main.immediate`, i.e. on the
     * thread the UI is drawn on, for every progress tick of every download.
     * None of it touches the view tree, and the service APIs called from here
     * (`startForeground`, `stopForeground`, `stopSelf`, `notify`) are plain
     * binder calls that are safe off the main thread.
     */
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var container: AppContainer

    @Volatile
    private var foregroundActive = false

    /** Id the service is currently promoted under, or [NO_FOREGROUND_ID]. */
    @Volatile
    private var foregroundId = NO_FOREGROUND_ID

    /**
     * Notification ids this service has posted progress for.
     *
     * Used to clear a stale progress notification the moment its task stops
     * being active, so nothing is ever cancelled that a completion/failure event
     * still owns.
     *
     * Concurrent because two collectors touch it: the progress stream and the
     * terminal-event stream are separate coroutines on the same dispatcher, and
     * the dispatcher is free to run them on different threads.
     */
    private val postedProgressIds: MutableSet<Int> = ConcurrentHashMap.newKeySet()

    /**
     * What the notification currently on screen for each id says.
     *
     * Comparing against this is what makes "rebuild only when something the
     * user can see changed" true.  The flow's `distinctUntilChanged()` alone was
     * not enough: the projection carried the whole task, so any row change made
     * every notification look stale and they were all rebuilt.
     */
    private val postedContent = ConcurrentHashMap<Int, NotificationContent>()

    private var wakeLock: PowerManager.WakeLock? = null
    private var idleJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as N13Application).container

        // Android requires a foreground notification within a few seconds of
        // startForegroundService.  This placeholder is replaced by the first real
        // task notification as soon as one exists, so it is never duplicated.
        promoteToForeground(
            DownloadNotifications.SERVICE_STARTING_ID,
            container.notifications.serviceStarting(),
        )

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

    /**
     * Android 15 puts a six-hour ceiling on `dataSync` foreground services.
     *
     * When the limit is reached the system calls this and the app **must** stop
     * the service itself; otherwise the platform throws
     * `ForegroundServiceDidNotStopInTimeException` and kills the process — a
     * crash in the middle of a transfer, with no state saved.  Pausing first
     * means the queue records real PAUSED rows with their byte counts, so the
     * downloads resume from where they were instead of restarting.
     */
    @RequiresApi(Build.VERSION_CODES.VANILLA_ICE_CREAM)
    override fun onTimeout(startId: Int) {
        Log.w(TAG, "dataSync foreground time limit reached; pausing the queue")
        scope.launch {
            runCatching { container.queue.pauseAll() }
            stopForegroundAndSelf()
        }
    }

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

    /**
     * Completion / failure / cancellation notifications.
     *
     * These reuse the task's own notification id, so a finished download
     * replaces its own progress notification rather than stacking a second one
     * on top of it.  The id is dropped from [postedProgressIds] first so the
     * terminal notification is not mistaken for stale progress.
     */
    private fun observeEvents() {
        scope.launch {
            container.queue.events.collect { event ->
                val settings = container.settingsSnapshot()
                val notificationId = DownloadNotifications.notificationId(event.taskId)
                postedProgressIds.remove(notificationId)
                // The terminal notification replaces the progress one, so the
                // cached content no longer describes what is on screen.
                postedContent.remove(notificationId)

                when (event) {
                    is TaskEvent.Completed -> {
                        val task = container.repository.get(event.taskId)
                        if (task != null && settings.notificationsEnabled && settings.notifyCompleted) {
                            container.notifications.post(
                                notificationId,
                                container.notifications.completed(task),
                            )
                        } else {
                            container.notifications.cancel(notificationId)
                        }
                    }

                    is TaskEvent.Failed -> {
                        val task = container.repository.get(event.taskId)
                        if (task != null && settings.notificationsEnabled && settings.notifyFailed) {
                            container.notifications.post(
                                notificationId,
                                container.notifications.failed(task, event.error),
                            )
                        } else {
                            container.notifications.cancel(notificationId)
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
     * Driven by the repository's throttled progress rows rather than by the
     * engine, so there is exactly one source of truth and a notification can
     * never disagree with the list.
     *
     * The stream is projected down to the fields a notification actually shows
     * and then de-duplicated, so a database write that does not change what the
     * user sees (a connection-count tweak, a re-emitted row) costs nothing and
     * cannot rebuild a notification needlessly.
     */
    private fun observeProgress() {
        scope.launch {
            container.repository.observeAll().collect { tasks ->
                val settings = container.settingsSnapshot()

                if (!settings.notificationsEnabled) {
                    postedProgressIds.forEach { container.notifications.cancel(it) }
                    postedProgressIds.clear()
                    postedContent.clear()
                    return@collect
                }

                val states = tasks.map { it.toNotificationState() }
                val notifiable = states.filter { it.notifiable }
                val totalSpeed = notifiable.sumOf { it.fullTask?.currentSpeed ?: 0.0 }
                val totalSpeedBucket = (totalSpeed / SPEED_BUCKET).toLong()
                val liveIds = notifiable.mapTo(HashSet()) { it.notificationId }

                // Rebuild each active task's notification from the row we
                // already have, so no extra database read is needed — but only
                // when the rendered fields actually changed.
                notifiable.forEach { state ->
                    val task = state.fullTask ?: return@forEach
                    val content = state.content(totalSpeedBucket)
                    if (postedContent[state.notificationId] == content) return@forEach

                    container.notifications.post(
                        state.notificationId,
                        container.notifications.progress(task, totalSpeed),
                    )
                    postedProgressIds.add(state.notificationId)
                    postedContent[state.notificationId] = content
                }

                // A task that stopped being active loses its progress
                // notification.  Terminal events remove their own id from
                // `postedProgressIds` before posting, so this only ever
                // cancels genuinely abandoned progress.
                val stale = postedProgressIds.filter { it !in liveIds }
                stale.forEach { container.notifications.cancel(it) }
                postedProgressIds.retainAll(liveIds)
                postedContent.keys.retainAll(liveIds)

                // The foreground notification follows the first active task.
                // Promoting onto *its* id keeps the count at exactly one.
                val anchor = notifiable.firstOrNull()?.fullTask
                if (anchor != null) {
                    promoteToForeground(
                        DownloadNotifications.notificationId(anchor.id),
                        container.notifications.progress(anchor, totalSpeed),
                    )
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

    private fun promoteToForeground(id: Int, notification: Notification) {
        if (foregroundActive && foregroundId == id) {
            // Same task, and its notification was just posted by the caller:
            // re-entering startForeground on every progress tick is pure churn.
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(id, notification)
        }
        foregroundActive = true
        foregroundId = id
        // The placeholder used before the first task existed is no longer needed.
        if (id != DownloadNotifications.SERVICE_STARTING_ID) {
            container.notifications.cancel(DownloadNotifications.SERVICE_STARTING_ID)
        }
    }

    private fun stopForegroundAndSelf() {
        if (foregroundActive) {
            stopForeground(STOP_FOREGROUND_DETACH)
            foregroundActive = false
            foregroundId = NO_FOREGROUND_ID
        }
        // The startup placeholder must never outlive the service.
        container.notifications.cancel(DownloadNotifications.SERVICE_STARTING_ID)
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
        const val NO_FOREGROUND_ID = -1
    }
}

/**
 * The only fields a notification renders, plus the task they came from.
 *
 * Projecting a task down to these lets the service skip work when a database row
 * changed in a way the user cannot see, which is what keeps notification churn
 * (and the battery cost behind it) proportional to real progress.  The task is
 * carried along so rebuilding the notification needs no extra database read.
 */
private data class NotificationState(
    val taskKey: Long,
    val notificationId: Int,
    val status: TaskStatus,
    val percent: Int,
    val downloadedBucket: Long,
    val speedBucket: Long,
    val notifiable: Boolean,
    val fullTask: DownloadTask?,
) {
    /**
     * The rendered fields, for change detection.
     *
     * Byte counts are compared in [BYTE_BUCKET] steps and speeds in
     * [SPEED_BUCKET] steps.  Both are finer than what the notification prints,
     * so text can never be left stale — it just stops being rebuilt for
     * differences the formatter would have rounded away.
     */
    fun content(totalSpeedBucket: Long): NotificationContent = NotificationContent(
        filename = fullTask?.filename.orEmpty(),
        percent = percent,
        downloadedBucket = downloadedBucket,
        totalSize = fullTask?.totalSize ?: 0L,
        speedBucket = speedBucket,
        totalSpeedBucket = totalSpeedBucket,
        status = status,
    )
}

/**
 * Everything a progress notification actually renders.
 *
 * Two notifications with equal content are indistinguishable to the user, so
 * posting the second one is pure cost: a binder round trip, a notification
 * rebuild in SystemUI, and a wake-up for a UI that has nothing new to show.
 */
private data class NotificationContent(
    val filename: String,
    val percent: Int,
    val downloadedBucket: Long,
    val totalSize: Long,
    val speedBucket: Long,
    val totalSpeedBucket: Long,
    val status: TaskStatus,
)

private fun DownloadTask.toNotificationState(): NotificationState = NotificationState(
    taskKey = id,
    notificationId = DownloadNotifications.notificationId(id),
    status = status,
    // Unknown-size transfers report 0% and a growing byte count instead, so they
    // still refresh on real progress.
    percent = if (hasKnownSize) percent.toInt().coerceIn(0, 100) else 0,
    downloadedBucket = downloadedSize / BYTE_BUCKET,
    // Speeds are shown rounded; finer changes would redraw the notification for
    // no visible benefit.
    speedBucket = (currentSpeed / SPEED_BUCKET).toLong(),
    notifiable = isNotifiable(),
    fullTask = this,
)

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

/** ~512 KiB, so an unknown-length download refreshes at most every few hundred KB. */
private const val BYTE_BUCKET = 512L * 1024L

/** 100 KB/s, so the displayed speed only changes meaningfully. */
private const val SPEED_BUCKET = 100.0 * 1024.0
