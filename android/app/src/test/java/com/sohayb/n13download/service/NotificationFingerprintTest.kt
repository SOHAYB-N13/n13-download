package com.sohayb.n13download.service

import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Change detection for notifications.
 *
 * The fingerprint is the whole defence against notification churn: the engine
 * writes progress about three times a second, and every one of those writes
 * invalidates the task table.  Rebuilding a notification for a change the user
 * cannot see costs a binder round trip and a SystemUI redraw each time, so the
 * rule is that the rendered values must be identical — not merely similar — for
 * a pass to be a no-op.
 *
 * Equally important in the other direction: a change the user *can* see must
 * never share a fingerprint with the old one, or the notification would silently
 * stop updating.
 */
class NotificationFingerprintTest {

    private fun task(
        status: TaskStatus = TaskStatus.DOWNLOADING,
        total: Long = 1_073_741_824L,
        downloaded: Long = 104_857_600L,
        speed: Double = 500_000.0,
        eta: Double? = 120.0,
        error: String = "",
        filename: String = "file.zip",
    ) = DownloadTask(
        id = 7L,
        filename = filename,
        status = status,
        totalSize = total,
        downloadedSize = downloaded,
        currentSpeed = speed,
        etaSeconds = eta,
        startedAt = 1L,
        error = error,
    )

    private fun fingerprint(
        task: DownloadTask,
        previous: TaskStatus? = TaskStatus.DOWNLOADING,
        settings: DownloadSettings = DownloadSettings(),
    ): NotificationFingerprint {
        val decision = NotificationPolicy.decide(task, previous, settings)
        return NotificationPolicy.fingerprint(task, decision)
    }

    @Test
    fun `an unchanged row produces an identical fingerprint`() {
        assertEquals(fingerprint(task()), fingerprint(task()))
    }

    @Test
    fun `progress below the display resolution is not a change`() {
        // 100 KiB on a 1 GiB download moves neither the percentage nor the bucket.
        val before = fingerprint(task())
        val after = fingerprint(task(downloaded = 104_857_600L + 102_400L))
        assertEquals(before, after)
    }

    @Test
    fun `a percentage change is a change`() {
        val before = fingerprint(task())
        val after = fingerprint(task(downloaded = 200_000_000L))
        assertNotEquals(before, after)
        assertTrue(after.percent > before.percent)
    }

    @Test
    fun `a speed change within the bucket is not a change`() {
        val before = fingerprint(task(speed = 500_000.0))
        val after = fingerprint(task(speed = 510_000.0))
        assertEquals(before, after)
    }

    @Test
    fun `a meaningful speed change is a change`() {
        val before = fingerprint(task(speed = 500_000.0))
        val after = fingerprint(task(speed = 700_000.0))
        assertNotEquals(before, after)
    }

    @Test
    fun `remaining time is quantised so it does not tick every second`() {
        val a = fingerprint(task(eta = 122.0))
        val b = fingerprint(task(eta = 128.0))
        assertEquals("within the same ten-second bucket", a, b)

        val c = fingerprint(task(eta = 140.0))
        assertNotEquals(a, c)
    }

    @Test
    fun `an unknown size is indeterminate and reports no percentage`() {
        val unknown = fingerprint(task(total = 0L, downloaded = 5_000_000L))
        assertTrue(unknown.indeterminate)
        assertEquals(0, unknown.percent)
    }

    @Test
    fun `byte progress still refreshes an unknown-size download`() {
        val before = fingerprint(task(total = 0L, downloaded = 5_000_000L))
        val after = fingerprint(task(total = 0L, downloaded = 6_000_000L))
        assertNotEquals("an unknown-length transfer must still show progress", before, after)
    }

    @Test
    fun `a status change always reaches the notification`() {
        val downloading = fingerprint(task(status = TaskStatus.DOWNLOADING))
        val paused = fingerprint(
            task(status = TaskStatus.PAUSED, speed = 0.0, eta = null),
            previous = TaskStatus.DOWNLOADING,
        )
        val verifying = fingerprint(task(status = TaskStatus.VERIFYING), previous = TaskStatus.DOWNLOADING)

        assertNotEquals(downloading, paused)
        assertNotEquals(downloading, verifying)
        assertFalse(paused.ongoing)
        assertTrue(downloading.ongoing)
    }

    @Test
    fun `the notification kind is part of the identity`() {
        val progress = fingerprint(task())
        val completed = fingerprint(
            task(status = TaskStatus.COMPLETED, downloaded = 1_073_741_824L, speed = 0.0, eta = null),
            previous = TaskStatus.DOWNLOADING,
        )
        assertEquals(NotificationKind.COMPLETED, completed.kind)
        assertNotEquals(progress, completed)
    }

    @Test
    fun `the error text belongs to failures only`() {
        val failed = fingerprint(
            task(status = TaskStatus.FAILED, error = "Connection lost"),
            previous = TaskStatus.DOWNLOADING,
        )
        val otherFailure = fingerprint(
            task(status = TaskStatus.FAILED, error = "404 Not Found"),
            previous = TaskStatus.DOWNLOADING,
        )
        assertEquals("Connection lost", failed.error)
        assertNotEquals(failed, otherFailure)

        // A non-failed task never carries an error into its fingerprint, so a
        // stale message from an earlier attempt cannot leak into a progress one.
        val running = fingerprint(task(status = TaskStatus.DOWNLOADING, error = "old failure"))
        assertEquals("", running.error)
    }

    @Test
    fun `the filename is part of the identity`() {
        assertNotEquals(fingerprint(task(filename = "a.zip")), fingerprint(task(filename = "b.zip")))
    }

    @Test
    fun `hidden decisions never produce a fingerprint worth posting`() {
        val queued = task(status = TaskStatus.QUEUED, downloaded = 0L)
        val decision = NotificationPolicy.decide(queued, previous = null, settings = DownloadSettings())
        assertEquals(NotificationKind.NONE, decision.kind)
    }
}
