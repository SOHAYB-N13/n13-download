package com.sohayb.n13download.service

import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The rules that decide what a download is allowed to show in the shade.
 *
 * This is the part of the notification system that used to be spread across a
 * progress collector, a terminal-event collector and the service, and it is the
 * part that produced the bugs worth fixing: a finished download repainted as
 * running, a queued one claiming to download, a cancelled one that never left.
 * Every case below is a state the app can genuinely be in, so the table doubles
 * as the specification.
 */
class NotificationPolicyTest {

    private fun task(
        id: Long = 1L,
        status: TaskStatus = TaskStatus.DOWNLOADING,
        total: Long = 10_000_000L,
        downloaded: Long = 1_000_000L,
        speed: Double = 500_000.0,
        eta: Double? = 12.0,
        startedAt: Long? = 1L,
        error: String = "",
        filename: String = "file.zip",
    ) = DownloadTask(
        id = id,
        url = "https://example.com/$filename",
        filename = filename,
        status = status,
        totalSize = total,
        downloadedSize = downloaded,
        currentSpeed = speed,
        etaSeconds = eta,
        startedAt = startedAt,
        error = error,
    )

    private fun decide(
        task: DownloadTask,
        previous: TaskStatus? = null,
        settings: DownloadSettings = DownloadSettings(),
        reconcile: Boolean = false,
        dismissed: Boolean = false,
    ) = NotificationPolicy.decide(task, previous, settings, reconcile, dismissed)

    // -- every state --------------------------------------------------- //

    @Test
    fun `queued downloads never own a notification`() {
        // A queued task is waiting for a slot; showing it as active would be a lie.
        val decision = decide(task(status = TaskStatus.QUEUED, downloaded = 0L, startedAt = null))
        assertEquals(NotificationKind.NONE, decision.kind)
        assertFalse(decision.ongoing)
    }

    @Test
    fun `every running phase shows an ongoing progress notification`() {
        val running = listOf(
            TaskStatus.ANALYZING,
            TaskStatus.STARTING,
            TaskStatus.DOWNLOADING,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
        )
        for (status in running) {
            val decision = decide(task(status = status))
            assertEquals("status $status", NotificationKind.PROGRESS, decision.kind)
            assertTrue("status $status must not be dismissible", decision.ongoing)
        }
    }

    @Test
    fun `a paused download keeps a notification that can be dismissed`() {
        val decision = decide(task(status = TaskStatus.PAUSED, speed = 0.0, eta = null))
        assertEquals(NotificationKind.PROGRESS, decision.kind)
        assertFalse("a paused transfer may be swiped away", decision.ongoing)
    }

    @Test
    fun `a task paused before it ever started stays silent`() {
        // "Pause all" parks waiting tasks too; those never had a transfer to report.
        val decision = decide(task(status = TaskStatus.PAUSED, startedAt = null, downloaded = 0L))
        assertEquals(NotificationKind.NONE, decision.kind)
    }

    @Test
    fun `cancelled and removed downloads leave no notification behind`() {
        assertEquals(
            NotificationKind.NONE,
            decide(task(status = TaskStatus.CANCELLED), previous = TaskStatus.DOWNLOADING).kind,
        )
        assertEquals(
            NotificationKind.NONE,
            decide(task(status = TaskStatus.REMOVED), previous = TaskStatus.DOWNLOADING).kind,
        )
    }

    @Test
    fun `completion is announced once, not on every refresh`() {
        val done = task(status = TaskStatus.COMPLETED, downloaded = 10_000_000L, speed = 0.0)

        assertEquals(
            NotificationKind.COMPLETED,
            decide(done, previous = TaskStatus.DOWNLOADING).kind,
        )
        // The next emission of the same row must not re-post it.
        assertEquals(
            NotificationKind.NONE,
            decide(done, previous = TaskStatus.COMPLETED).kind,
        )
    }

    @Test
    fun `failure is announced once, with its own kind`() {
        val failed = task(status = TaskStatus.FAILED, error = "Connection lost")

        assertEquals(NotificationKind.FAILED, decide(failed, previous = TaskStatus.DOWNLOADING).kind)
        assertEquals(NotificationKind.NONE, decide(failed, previous = TaskStatus.FAILED).kind)
    }

    @Test
    fun `a retried download can be announced again`() {
        val done = task(status = TaskStatus.COMPLETED, downloaded = 10_000_000L, speed = 0.0)
        // COMPLETED -> QUEUED -> DOWNLOADING -> COMPLETED is a new lifecycle.
        assertEquals(
            NotificationKind.COMPLETED,
            decide(done, previous = TaskStatus.DOWNLOADING).kind,
        )
    }

    @Test
    fun `the first pass after start never resurrects old terminal states`() {
        val done = task(status = TaskStatus.COMPLETED, downloaded = 10_000_000L, speed = 0.0)
        val failed = task(status = TaskStatus.FAILED, error = "404")

        // Reconciling: these belong to a previous session, so they are recorded
        // rather than announced.
        assertEquals(
            NotificationKind.NONE,
            decide(done, previous = null, reconcile = true).kind,
        )
        assertEquals(
            NotificationKind.NONE,
            decide(failed, previous = null, reconcile = true).kind,
        )
    }

    @Test
    fun `a download that finishes before the first pass is still announced`() {
        // previous == null but this is not the reconciling pass: the download ran
        // and ended inside this service's lifetime, so the user has not seen it.
        val done = task(status = TaskStatus.COMPLETED, downloaded = 10_000_000L, speed = 0.0)
        assertEquals(
            NotificationKind.COMPLETED,
            decide(done, previous = null, reconcile = false).kind,
        )
    }

    // -- settings ------------------------------------------------------ //

    @Test
    fun `switching notifications off hides every state`() {
        val off = DownloadSettings(notificationsEnabled = false)
        val states = listOf(
            TaskStatus.QUEUED,
            TaskStatus.ANALYZING,
            TaskStatus.STARTING,
            TaskStatus.DOWNLOADING,
            TaskStatus.PAUSED,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REMOVED,
        )
        for (status in states) {
            val decision = decide(
                task(status = status),
                previous = TaskStatus.DOWNLOADING,
                settings = off,
            )
            assertEquals("status $status", NotificationKind.NONE, decision.kind)
        }
    }

    @Test
    fun `completion alerts can be switched off without silencing progress`() {
        val settings = DownloadSettings(notifyCompleted = false)
        val done = task(status = TaskStatus.COMPLETED, downloaded = 10_000_000L, speed = 0.0)

        assertEquals(NotificationKind.NONE, decide(done, previous = TaskStatus.DOWNLOADING, settings = settings).kind)
        assertEquals(NotificationKind.PROGRESS, decide(task(), settings = settings).kind)
    }

    @Test
    fun `failure alerts can be switched off independently`() {
        val settings = DownloadSettings(notifyFailed = false)
        val failed = task(status = TaskStatus.FAILED, error = "404")
        assertEquals(
            NotificationKind.NONE,
            decide(failed, previous = TaskStatus.DOWNLOADING, settings = settings).kind,
        )
    }

    // -- dismissal ----------------------------------------------------- //

    @Test
    fun `a swiped paused notification does not come back on refresh`() {
        val paused = task(status = TaskStatus.PAUSED, speed = 0.0, eta = null)
        assertEquals(
            NotificationKind.NONE,
            decide(paused, previous = TaskStatus.PAUSED, dismissed = true).kind,
        )
    }

    @Test
    fun `resuming a dismissed download brings its notification back`() {
        // The centre clears the dismissal when the status changes, so this asks
        // the policy the same question the centre would.
        val running = task(status = TaskStatus.DOWNLOADING)
        val decision = decide(running, previous = TaskStatus.PAUSED, dismissed = false)
        assertEquals(NotificationKind.PROGRESS, decision.kind)
        assertTrue(decision.ongoing)
    }

    // -- notification-worthy ------------------------------------------- //

    @Test
    fun `only live work owns a progress notification`() {
        // Asked through `decide` rather than a second helper: two functions
        // answering "should there be a notification?" is exactly how the old
        // three-writer design drifted apart.
        fun kindOf(status: TaskStatus, startedAt: Long? = 1L) =
            decide(task(status = status, startedAt = startedAt), previous = null).kind

        assertEquals(NotificationKind.PROGRESS, kindOf(TaskStatus.DOWNLOADING))
        assertEquals(NotificationKind.PROGRESS, kindOf(TaskStatus.ANALYZING))
        assertEquals(NotificationKind.PROGRESS, kindOf(TaskStatus.PAUSED))
        assertEquals(NotificationKind.NONE, kindOf(TaskStatus.PAUSED, startedAt = null))
        assertEquals(NotificationKind.NONE, kindOf(TaskStatus.QUEUED))
        assertEquals(NotificationKind.NONE, kindOf(TaskStatus.CANCELLED))
        assertEquals(NotificationKind.NONE, kindOf(TaskStatus.REMOVED))
        // Terminal states are announced once, and only through their own kind.
        assertEquals(NotificationKind.COMPLETED, kindOf(TaskStatus.COMPLETED))
        assertEquals(NotificationKind.FAILED, kindOf(TaskStatus.FAILED))
    }

    // -- presentation numbers ------------------------------------------ //

    @Test
    fun `percentage comes from real bytes`() {
        assertEquals(25, NotificationPolicy.displayPercent(task(total = 4_000L, downloaded = 1_000L)))
    }

    @Test
    fun `an unknown size reports no percentage`() {
        val unknown = task(total = 0L, downloaded = 1_234_567L)
        assertEquals(0, NotificationPolicy.displayPercent(unknown))
        assertNull(NotificationPolicy.displayEtaSeconds(unknown))
    }

    @Test
    fun `remaining time is only offered while downloading`() {
        assertNull(NotificationPolicy.displayEtaSeconds(task(status = TaskStatus.PAUSED, eta = 30.0)))
        assertNull(NotificationPolicy.displayEtaSeconds(task(status = TaskStatus.MERGING, eta = 30.0)))
        assertNull(NotificationPolicy.displayEtaSeconds(task(status = TaskStatus.COMPLETED, eta = 30.0)))
        assertEquals(30L, NotificationPolicy.displayEtaSeconds(task(eta = 34.0)))
    }

    @Test
    fun `an absurd remaining time is not shown`() {
        assertNull(NotificationPolicy.displayEtaSeconds(task(eta = 9.0 * 3600.0)))
        assertNull(NotificationPolicy.displayEtaSeconds(task(eta = Double.NaN)))
        assertNull(NotificationPolicy.displayEtaSeconds(task(eta = -5.0)))
        assertNull(NotificationPolicy.displayEtaSeconds(task(eta = null)))
    }

    @Test
    fun `the last few seconds still show an estimate`() {
        // Bucketing rounds down, which used to erase every estimate under ten
        // seconds — exactly when the number matters most.
        assertEquals(10L, NotificationPolicy.displayEtaSeconds(task(eta = 9.0)))
        assertEquals(10L, NotificationPolicy.displayEtaSeconds(task(eta = 0.4)))
        assertEquals(10L, NotificationPolicy.displayEtaSeconds(task(eta = 10.0)))
        // Buckets round down, so 19.9s is reported as 10s rather than promising
        // less time than there is.
        assertEquals(10L, NotificationPolicy.displayEtaSeconds(task(eta = 19.9)))
        assertEquals(20L, NotificationPolicy.displayEtaSeconds(task(eta = 20.0)))
    }
}
