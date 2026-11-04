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
 * # The service does not own notifications
 *
 * [DownloadNotificationCenter] does.  The service reads one stream — the
 * persisted rows — hands each snapshot to the centre, and promotes itself onto
 * the notification the centre says belongs to the first *running* download.  It
 * never builds, edits or cancels a notification of its own, which is what
 * removed the old duplicate where the service anchored a fixed id (`1`) while
 * the same download also lived under `2000 + taskId`.
 *
 * # Why a running task anchors, and a paused one does not
 *
 * A foreground service exists to keep work alive.  With only paused downloads
 * there is no work, so the service drops out of the foreground and stops — the
 * paused notifications stay in the shade as ordinary, dismissible notifications.
 * Anchoring on a paused task would keep the CPU awake for a transfer that is not
 * moving, and would leave the service without a notification the moment the user
 * swiped it away.
 *
 * # Startup
 *
 * `startForegroundService` gives the service a few seconds to show something, so
 * `onCreate` posts a placeholder immediately.  The first render pass then either
 * promotes a real task onto its own id (and removes the placeholder) or finds
 * nothing running, in which case the idle path stops the service again.
 *
 * # Reconciliation
 *
 * Notifications survive a process death; the app's knowledge of them does not.
 * The application container therefore empties the shade once, before this service
 * can exist, and the first render pass rebuilds it from the persisted rows — so a
 * progress notification left behind by a process killed mid-transfer cannot
 * outlive the restart.  See [DownloadNotificationCenter.resetShade].
 */
class DownloadService : Service() {

    /**
     * Off the main thread on purpose.
     *
     * Everything this scope does — reading rows, formatting notification text,
     * building `PendingIntent`s — used to run on `Main.immediate`, i.e. on the
     * thread the UI is drawn on, for every progress tick of every download.
     * None of it touches the view tree, and the service APIs called from here
     * (`startForeground`, `stopForeground`, `stopSelf`) are plain binder calls
     * that are safe off the main thread.
     */
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var container: AppContainer
    private lateinit var center: DownloadNotificationCenter

    @Volatile
    private var foregroundActive = false

    /** Id the service is currently promoted under, or [NO_FOREGROUND_ID]. */
    @Volatile
    private var foregroundId = NO_FOREGROUND_ID

    private var wakeLock: PowerManager.WakeLock? = null
    private var idleJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as N13Application).container
        center = container.notificationCenter

        // Android requires a foreground notification within a few seconds of
        // startForegroundService.  This placeholder is replaced by the first real
        // task notification as soon as one exists, so it is never duplicated.
        promoteToForeground(
            DownloadNotifications.SERVICE_STARTING_ID,
            center.serviceStartingNotification(),
        )

        observeTasks()
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
        // The queue re-reads the row and only acts if the operation still makes
        // sense for its current state, so a stale action (a Pause tapped on a
        // notification the engine has already finished with) is a no-op rather
        // than a conflicting transition.
        scope.launch { runCatching { action(taskId) } }
    }

    /**
     * The only notification input: the authoritative task table.
     *
     * Driven by the repository's throttled progress rows rather than by engine
     * callbacks or queue events, so a notification can never disagree with the
     * list.  One collector means one writer, which means no ordering problem to
     * solve — an older snapshot cannot overtake a newer one.
     */
    private fun observeTasks() {
        scope.launch {
            container.repository.observeAll().collect { tasks ->
                updateForeground(center.render(tasks))
            }
        }
    }

    /** Promotes the first running download, or leaves the current anchor alone. */
    private fun updateForeground(result: RenderResult) {
        val taskId = result.anchorTaskId ?: return
        val notification = result.anchorNotification ?: return
        promoteToForeground(DownloadNotifications.notificationId(taskId), notification)
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
            // Same task, and its notification was just posted by the centre:
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
        if (id != DownloadNotifications.SERVICE_STARTING_ID) center.clearPlaceholder()
    }

    /**
     * Leaves the foreground and stops, without leaving a lie behind.
     *
     * `STOP_FOREGROUND_DETACH` keeps the notification on screen — correct for a
     * download that just finished.  The catch is that a cancellation issued while
     * the notification *was* the foreground one is ignored by the system, so once
     * the service is out of the way the centre is asked to settle that id for
     * real: cancel it if it should not exist, leave it if it should.
     */
    private suspend fun stopForegroundAndSelf() {
        val lastForegroundId = foregroundId
        if (foregroundActive) {
            stopForeground(STOP_FOREGROUND_DETACH)
            foregroundActive = false
            foregroundId = NO_FOREGROUND_ID
        }
        center.clearPlaceholder()
        if (lastForegroundId != NO_FOREGROUND_ID) center.releaseForeground(lastForegroundId)
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
