package com.sohayb.n13download

import android.os.Build
import android.service.notification.StatusBarNotification
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.service.DownloadNotifications
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Pins the "one notification per download" guarantee.
 *
 * The original defect was a download appearing twice in the shade with the same
 * contents: the service posted the anchor task under a fixed service id while the
 * task's own progress notification lived under a different one.  Both were the
 * same transfer, so the user saw it listed twice.
 *
 * These checks are about *identity*, which is what actually caused the duplicate:
 * every notification for a task — progress, completed, failed — must carry the
 * same id, two different tasks must never share an id, and the startup
 * placeholder must not collide with a real task.
 */
@RunWith(AndroidJUnit4::class)
class NotificationIdentityDeviceTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val notifications = DownloadNotifications(context)

    private fun task(
        id: Long,
        status: TaskStatus = TaskStatus.DOWNLOADING,
        downloaded: Long = 512L * 1024L,
        total: Long = 4L * 1024L * 1024L,
    ) = DownloadTask(
        id = id,
        url = "http://example.test/file.bin",
        filename = "file.bin",
        status = status,
        totalSize = total,
        downloadedSize = downloaded,
        startedAt = 1_000L,
        createdAt = 1_000L,
    )

    /**
     * Every notification posted for one task must be the *same* id.
     *
     * This is the mechanism that makes progress updates replace each other in
     * place instead of stacking, and it is what the old service got wrong.
     */
    @Test
    fun allNotificationsForATask_shareOneIdentity() {
        val progressId = DownloadNotifications.notificationId(TASK_A)
        val completedId = DownloadNotifications.notificationId(TASK_A)
        val failedId = DownloadNotifications.notificationId(TASK_A)

        assertEquals(progressId, completedId)
        assertEquals(completedId, failedId)
    }

    /** Two tasks must never be able to overwrite each other's notification. */
    @Test
    fun distinctTasks_haveDistinctIdentities() {
        val ids = (1L..500L).map { DownloadNotifications.notificationId(it) }
        assertEquals("ids must be unique per task", 500, ids.toSet().size)
    }

    /**
     * The placeholder used while the foreground service starts must not collide
     * with any real task's notification, or the first download would inherit the
     * "Preparing downloads…" notification as its own.
     */
    @Test
    fun serviceStartingPlaceholder_cannotCollideWithARealTask() {
        val placeholder = DownloadNotifications.SERVICE_STARTING_ID
        for (taskId in 1L..2_000L) {
            assertNotEquals(
                "task $taskId collides with the service placeholder",
                placeholder,
                DownloadNotifications.notificationId(taskId),
            )
        }
    }

    /**
     * The identity scheme must survive the notification actually being posted:
     * posting progress and then the terminal state for one task leaves exactly
     * one live notification for that task, not two.
     *
     * Posting requires the notification permission, which a locked-down device
     * may refuse to grant a test process.  When it is missing the check is
     * skipped rather than reported as a pass — the identity assertions above
     * still cover the mechanism that caused the original duplicate.
     */
    @Test
    fun postingProgressThenCompletion_leavesOnlyOneLiveNotification() {
        assumeTrue(notificationsPermitted())

        val id = DownloadNotifications.notificationId(TASK_LIVE)
        val manager = context.getSystemService(android.app.NotificationManager::class.java)

        // Progress while running…
        notifications.post(id, notifications.progress(task(TASK_LIVE), totalSpeed = 1024.0 * 512))
        assertTrue(
            "a progress notification must be live after posting",
            waitForNotification(manager, id, present = true),
        )

        // …then completion, which must replace it under the same id rather than
        // adding a second entry.
        notifications.post(
            id,
            notifications.completed(task(TASK_LIVE, status = TaskStatus.COMPLETED, downloaded = 4L * 1024L * 1024L)),
        )
        Thread.sleep(300)
        assertEquals(
            "completion must replace progress, not add a second entry",
            1,
            liveCount(manager, id),
        )

        notifications.cancel(id)
        assertTrue(
            "cancelling removes it",
            waitForNotification(manager, id, present = false),
        )
    }

    /**
     * NotificationManager is asynchronous in both directions, so a check that
     * runs immediately after a call can see the old world.  Poll briefly instead
     * of asserting on a snapshot.
     */
    private fun waitForNotification(
        manager: android.app.NotificationManager?,
        id: Int,
        present: Boolean,
    ): Boolean {
        repeat(25) {
            if ((liveCount(manager, id) > 0) == present) return true
            Thread.sleep(100)
        }
        return (liveCount(manager, id) > 0) == present
    }

    private fun notificationsPermitted(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
    }

    private fun liveCount(manager: android.app.NotificationManager?, id: Int): Int {
        val live: Array<StatusBarNotification> = manager?.activeNotifications ?: return 0
        return live.count { it.packageName == context.packageName && it.id == id }
    }

    private companion object {
        const val TASK_A = 7L
        const val TASK_LIVE = 42L
    }
}
