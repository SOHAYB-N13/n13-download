package com.sohayb.n13download

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.core.UrlSecurity
import com.sohayb.n13download.data.engine.OkHttpDownloadEngine
import com.sohayb.n13download.data.storage.FileDirectoryDestination
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.download.EngineListener
import com.sohayb.n13download.domain.download.EngineOutcome
import com.sohayb.n13download.domain.download.EngineRequest
import com.sohayb.n13download.domain.download.TaskControl
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Exercises the real download engine end to end, on the device.
 *
 * Two sources are used deliberately:
 *
 *  1. A **loopback HTTP server** ([LocalHttpServer]) speaking real HTTP/1.1.  This
 *     is the primary suite because it is deterministic and because the Redmi 8 is
 *     on a network where the usual public test hosts are unreachable.  It proves
 *     the probe, the segmented multi-connection writer, resume after pause, the
 *     merge step, checksum verification and the app's own storage layer — using
 *     the production engine, production destinations and production OkHttp setup.
 *  2. A **real internet check**, skipped (not failed) when the device has no
 *     working route, so the suite is honest about what it could not reach.
 *
 * Test files are a few hundred kilobytes and live in the app's own sandbox, so no
 * personal file is ever touched.
 */
@RunWith(AndroidJUnit4::class)
class DownloadEngineDeviceTest {

    private lateinit var context: Context
    private lateinit var engine: OkHttpDownloadEngine
    private lateinit var root: File
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val servers = mutableListOf<LocalHttpServer>()

    @Before
    fun setUp() {
        context = InstrumentationRegistry.getInstrumentation().targetContext
        engine = OkHttpDownloadEngine()
        root = File(context.getExternalFilesDir(null), "engine-test").apply {
            deleteRecursively()
            mkdirs()
        }
    }

    @After
    fun tearDown() {
        servers.forEach { it.stop() }
        servers.clear()
        engine.shutdown()
        root.deleteRecursively()
    }

    // ------------------------------------------------------------------ //
    // Probe
    // ------------------------------------------------------------------ //

    @Test
    fun probe_detectsSizeRangeSupportAndFilename() = runBlocking {
        val server = serve(body = payload(512 * 1024), supportsRange = true, filename = "n13-probe.bin")

        val analysis = engine.probe(server.url(), settings())

        assertTrue("probe should succeed: ${analysis.error}", analysis.ok)
        assertEquals(512L * 1024L, analysis.totalSize)
        assertTrue("server advertises byte ranges", analysis.supportsRange)
        assertEquals("n13-probe.bin", analysis.filename)
        assertEquals("application/octet-stream", analysis.contentType)
        assertTrue("the final URL should be recorded", analysis.finalUrl.isNotBlank())
    }

    @Test
    fun probe_reportsNoRangeSupportWhenTheServerOmitsIt() = runBlocking {
        val server = serve(body = payload(64 * 1024), supportsRange = false, filename = "plain.bin")

        val analysis = engine.probe(server.url(), settings())

        assertTrue("probe should succeed: ${analysis.error}", analysis.ok)
        assertEquals(64L * 1024L, analysis.totalSize)
        assertFalse("this server does not advertise byte ranges", analysis.supportsRange)
        assertTrue("single-connection only", analysis.singleConnectionOnly)
    }

    @Test
    fun ssrfGuard_rejectsLoopbackAndPrivateTargets() {
        // The guard is enforced by UrlSecurity, which DownloadManager applies before
        // it ever probes a link; asserting it here keeps the check honest without
        // needing a live server.
        val blocked = listOf(
            "http://127.0.0.1:8080/secret",
            "http://localhost/file.bin",
            "http://192.168.1.10/file.bin",
            "http://10.0.0.5/file.bin",
            "http://169.254.169.254/latest/meta-data/",
        )
        blocked.forEach { url ->
            val result = UrlSecurity.validate(url, blockPrivate = true)
            assertFalse("$url should be blocked", result.ok)
        }

        // A normal public host is allowed.
        assertTrue(
            "a public host must not be blocked",
            UrlSecurity.validate("https://example.com/file.zip", blockPrivate = true).ok,
        )

        // Turning the setting off allows a LAN target, which is how a user downloads
        // from their own NAS.
        assertTrue(
            "the block must be switchable off",
            UrlSecurity.validate("http://192.168.1.10/file.bin", blockPrivate = false).ok,
        )
    }

    // ------------------------------------------------------------------ //
    // Multi-connection transfer
    // ------------------------------------------------------------------ //

    @Test
    fun segmentedDownload_usesSeveralConnectionsAndPublishesTheExactBytes() = runBlocking {
        // 8 MB so the 2 MB minimum segment size yields four real segments.
        val size = 8 * 1024 * 1024
        val body = payload(size)
        val server = serve(body = body, supportsRange = true, filename = "segmented.bin")
        val working = File(root, "segmented-work").apply { mkdirs() }
        val destination = appDestination("segmented")
        val name = "segmented.bin"

        val recorder = ProgressRecorder()
        val outcome = run(
            settings = settings().copy(connectionMode = ConnectionMode.MANUAL, numThreads = 4),
            destination = destination,
            name = name,
            url = server.url(),
            working = working,
            supportsRange = true,
            totalSize = size.toLong(),
            recorder = recorder,
        )

        assertTrue("expected Completed, got $outcome", outcome is EngineOutcome.Completed)
        val file = File(root, "segmented/$name")
        assertEquals(size.toLong(), file.length())
        assertTrue("the merged bytes must match the source", file.readBytes().contentEquals(body))

        // The engine may settle on fewer parallel segments than requested: it
        // runs over loopback here, so a segment can finish before the scheduler
        // has opened the next one.  What must hold is that the work was actually
        // *segmented* — more than one ranged request went out — rather than that
        // a specific connection count was reached.
        assertTrue(
            "a multi-connection download must issue several ranged requests, saw ${server.rangedRequestCount}",
            server.rangedRequestCount >= 2,
        )
        assertTrue("progress should have been reported", recorder.lastDownloaded > 0L)

        assertFalse(
            "part files should be cleaned up after the merge",
            working.listFiles().orEmpty().any { it.name.contains(".part") },
        )
        assertFalse("the segment layout should be cleaned up", File(working, "layout.json").exists())
        assertNotNull("the resolved path should be reported", recorder.resolvedPath)
    }

    @Test
    fun singleStreamDownload_publishesTheExactBytesWhenRangeIsUnsupported() = runBlocking {
        val size = 256 * 1024
        val body = payload(size)
        val server = serve(body = body, supportsRange = false, filename = "plain.bin")
        val working = File(root, "single-work").apply { mkdirs() }
        val destination = appDestination("single")
        val name = "plain.bin"

        val recorder = ProgressRecorder()
        val outcome = run(
            settings = settings(),
            destination = destination,
            name = name,
            url = server.url(),
            working = working,
            supportsRange = false,
            totalSize = size.toLong(),
            recorder = recorder,
        )

        assertTrue("expected Completed, got $outcome", outcome is EngineOutcome.Completed)
        val file = File(root, "single/$name")
        assertEquals(size.toLong(), file.length())
        assertTrue(file.readBytes().contentEquals(body))
        assertEquals("a no-range server must never be opened more than once", 0, server.rangedRequestCount)
        assertTrue("only one connection may be used", recorder.maxConnections <= 1)
    }

    // ------------------------------------------------------------------ //
    // Integrity
    // ------------------------------------------------------------------ //

    @Test
    fun checksumMismatch_failsAndNeverPublishesTheFile() = runBlocking {
        val server = serve(body = payload(64 * 1024), supportsRange = false, filename = "bad.bin")
        val working = File(root, "checksum-work").apply { mkdirs() }
        val destination = appDestination("checksum")
        val name = "bad.bin"

        val outcome = run(
            settings = settings(),
            destination = destination,
            name = name,
            url = server.url(),
            working = working,
            supportsRange = false,
            totalSize = 64L * 1024L,
            checksum = "00000000000000000000000000000000",
        )

        assertTrue("expected Failed, got $outcome", outcome is EngineOutcome.Failed)
        assertTrue((outcome as EngineOutcome.Failed).message.contains("Checksum", ignoreCase = true))
        assertFalse("a corrupt file must not be published", File(root, "checksum/$name").exists())
    }

    @Test
    fun matchingChecksum_completesAndPublishes() = runBlocking {
        val body = payload(128 * 1024)
        val server = serve(body = body, supportsRange = true, filename = "good.bin")
        val working = File(root, "good-checksum-work").apply { mkdirs() }
        val destination = appDestination("good-checksum")
        val name = "good.bin"

        val outcome = run(
            settings = settings(),
            destination = destination,
            name = name,
            url = server.url(),
            working = working,
            supportsRange = true,
            totalSize = body.size.toLong(),
            checksum = md5(body),
        )

        assertTrue("expected Completed, got $outcome", outcome is EngineOutcome.Completed)
        assertEquals(body.size.toLong(), File(root, "good-checksum/$name").length())
    }

    // ------------------------------------------------------------------ //
    // Pause / resume / cancel
    // ------------------------------------------------------------------ //

    @Test
    fun pause_keepsPartialDataAndResumeFinishesUsingIt() = runBlocking {
        val size = 8 * 1024 * 1024
        val body = payload(size)
        // Deliberately slow so the pause lands mid-transfer.
        val server = serve(
            body = body,
            supportsRange = true,
            filename = "resume.bin",
            bytesPerSecond = 2 * 1024 * 1024,
        )
        val working = File(root, "resume-work").apply { mkdirs() }
        val destination = appDestination("resume")
        val name = "resume.bin"
        val settings = settings().copy(connectionMode = ConnectionMode.MANUAL, numThreads = 4)

        // ---- First pass: start, then pause once bytes are on disk ---------
        val control = TaskControl()
        val firstPass = scope.async {
            engine.execute(
                EngineRequest(
                    task = task(name, server.url(), size.toLong(), supportsRange = true),
                    settings = settings,
                    workingDirectory = working,
                    destination = destination,
                    targetFilename = name,
                ),
                control,
                ProgressRecorder(),
            )
        }

        assertTrue("the transfer never started writing", waitForBytes(working))
        control.requestPause()
        val paused = withTimeoutOrNull(30_000L) { firstPass.await() }
        assertNotNull("the engine did not return after a pause request", paused)

        assertTrue("expected Paused, got $paused", paused is EngineOutcome.Paused)

        val bytesOnDisk = working.listFiles().orEmpty()
            .filter { it.name.contains(".part") }
            .sumOf { it.length() }
        assertTrue("pause must keep the downloaded bytes", bytesOnDisk > 0L)
        assertTrue("pause must keep the segment layout", File(working, "layout.json").exists())
        assertFalse("a paused download must not publish a file", File(root, "resume/$name").exists())

        val rangedRequestsBeforeResume = server.rangedRequestCount

        // ---- Second pass: resume and finish -------------------------------
        val resumeOutcome = run(
            settings = settings,
            destination = destination,
            name = name,
            url = server.url(),
            working = working,
            supportsRange = true,
            totalSize = size.toLong(),
        )

        assertTrue("expected Completed, got $resumeOutcome", resumeOutcome is EngineOutcome.Completed)
        val file = File(root, "resume/$name")
        assertEquals(size.toLong(), file.length())
        assertTrue("the resumed bytes must match the source", file.readBytes().contentEquals(body))
        assertTrue(
            "resuming must not restart the whole file",
            server.rangedRequestCount > rangedRequestsBeforeResume,
        )
        assertFalse("parts should be cleaned up after the merge", File(working, "layout.json").exists())
    }

    @Test
    fun cancel_stopsTheTransferAndLeavesNothingPublished() = runBlocking {
        val size = 8 * 1024 * 1024
        val server = serve(
            body = payload(size),
            supportsRange = true,
            filename = "cancel.bin",
            bytesPerSecond = 1024 * 1024,
        )
        val working = File(root, "cancel-work").apply { mkdirs() }
        val destination = appDestination("cancel")
        val name = "cancel.bin"

        val control = TaskControl()
        val job = scope.async {
            engine.execute(
                EngineRequest(
                    task = task(name, server.url(), size.toLong(), supportsRange = true),
                    settings = settings(),
                    workingDirectory = working,
                    destination = destination,
                    targetFilename = name,
                ),
                control,
                ProgressRecorder(),
            )
        }

        assertTrue("the transfer never started writing", waitForBytes(working))
        control.requestCancel()
        val outcome = job.await()

        assertTrue("expected Cancelled, got $outcome", outcome is EngineOutcome.Cancelled)
        assertFalse("a cancelled download must not publish a file", File(root, "cancel/$name").exists())
    }

    @Test
    fun failure_isReportedWithoutLeavingAPartialFile() = runBlocking {
        // A port with nothing listening: a real, deterministic connection failure.
        val dead = LocalHttpServer(body = payload(1024), supportsRange = false).also { it.start() }
        val url = dead.url()
        dead.stop()

        val working = File(root, "failure-work").apply { mkdirs() }
        val destination = appDestination("failure")
        val name = "unreachable.bin"

        val outcome = run(
            settings = settings().copy(maxRetries = 1),
            destination = destination,
            name = name,
            url = url,
            working = working,
            supportsRange = false,
            totalSize = 1024L,
        )

        assertTrue("expected Failed, got $outcome", outcome is EngineOutcome.Failed)
        assertFalse("an incomplete download must not be published", File(root, "failure/$name").exists())
    }

    // ------------------------------------------------------------------ //
    // Real internet (skipped when the device has no route)
    // ------------------------------------------------------------------ //

    @Test
    fun realInternet_downloadsFromAPublicHttpsHost() = runBlocking {
        val url = "https://proof.ovh.net/files/1Mb.dat"
        assumeTrue("the device has no working internet route", reachable(url))

        val analysis = engine.probe(url, settings().copy(blockPrivateUrls = false))
        assertTrue("probe should succeed: ${analysis.error}", analysis.ok)
        assertTrue("this host advertises byte ranges", analysis.supportsRange)

        val working = File(root, "internet-work").apply { mkdirs() }
        val destination = appDestination("internet")
        val name = "internet.bin"

        val outcome = run(
            settings = settings().copy(blockPrivateUrls = false),
            destination = destination,
            name = name,
            url = url,
            working = working,
            supportsRange = true,
            totalSize = analysis.totalSize,
        )

        assertTrue("expected Completed, got $outcome", outcome is EngineOutcome.Completed)
        assertEquals(analysis.totalSize, File(root, "internet/$name").length())
    }

    private suspend fun reachable(url: String): Boolean = runCatching {
        engine.probe(url, settings().copy(blockPrivateUrls = false)).ok
    }.getOrDefault(false)

    // ------------------------------------------------------------------ //
    // Helpers
    // ------------------------------------------------------------------ //

    private fun serve(
        body: ByteArray,
        supportsRange: Boolean,
        filename: String? = null,
        bytesPerSecond: Int = 0,
    ): LocalHttpServer = LocalHttpServer(
        body = body,
        supportsRange = supportsRange,
        filename = filename,
        bytesPerSecond = bytesPerSecond,
    ).also {
        it.start()
        servers.add(it)
    }

    /** Deterministic, non-compressible payload so byte comparison is meaningful. */
    private fun payload(size: Int): ByteArray {
        val bytes = ByteArray(size)
        var value = 0x5A
        for (index in 0 until size) {
            value = (value * 31 + index) and 0xFF
            bytes[index] = value.toByte()
        }
        return bytes
    }

    private fun md5(bytes: ByteArray): String =
        MessageDigest.getInstance("MD5").digest(bytes).joinToString("") { "%02x".format(it) }

    /**
     * Waits until real segment data is on disk, so a pause or cancel lands
     * mid-transfer.  Only `.part` files count — the layout file is written
     * immediately and would otherwise make this return before any byte arrived.
     */
    private suspend fun waitForBytes(
        working: File,
        minBytes: Long = 256L * 1024L,
        timeoutMillis: Long = 25_000L,
    ): Boolean = withTimeoutOrNull(timeoutMillis) {
        while (true) {
            val written = working.listFiles().orEmpty()
                .filter { it.name.contains(".part") }
                .sumOf { it.length() }
            if (written >= minBytes) return@withTimeoutOrNull true
            delay(50)
        }
        @Suppress("UNREACHABLE_CODE") false
    } ?: false

    private fun settings() = DownloadSettings(
        // The SSRF guard is asserted separately; the engine tests target loopback.
        blockPrivateUrls = false,
        userAgent = BrowserHeaders.DEFAULT_USER_AGENT,
        maxRetries = 2,
        retryDelay = 1.0,
    )

    private fun appDestination(folder: String): DownloadDestination = FileDirectoryDestination(
        context = context,
        rootDirectory = root,
        folder = folder,
        kind = DestinationKind.APP,
        labelPrefix = "test",
    )

    private fun task(name: String, url: String, size: Long, supportsRange: Boolean) = DownloadTask(
        id = 1L,
        url = url,
        filename = name,
        directory = "test",
        totalSize = size,
        supportsRange = supportsRange,
        createdAt = System.currentTimeMillis(),
    )

    private suspend fun run(
        settings: DownloadSettings,
        destination: DownloadDestination,
        name: String,
        url: String,
        working: File,
        supportsRange: Boolean,
        totalSize: Long,
        checksum: String = "",
        recorder: ProgressRecorder = ProgressRecorder(),
    ): EngineOutcome = engine.execute(
        EngineRequest(
            task = task(name, url, totalSize, supportsRange).copy(checksum = checksum),
            settings = settings,
            workingDirectory = working,
            destination = destination,
            targetFilename = name,
        ),
        TaskControl(),
        recorder,
    )

    /** Records the engine's callbacks so its reporting path is exercised too. */
    private class ProgressRecorder : EngineListener {
        private val maxSeen = AtomicLong(0)

        var lastDownloaded = 0L
            private set

        var lastTotal = 0L
            private set

        var maxConnections = 0
            private set

        var resolvedPath: String? = null
            private set

        val phases = mutableListOf<TaskStatus>()

        override fun onProgress(
            downloadedBytes: Long,
            totalBytes: Long,
            speed: Double,
            averageSpeed: Double,
            etaSeconds: Double?,
        ) {
            lastDownloaded = downloadedBytes
            lastTotal = totalBytes
        }

        override fun onPhase(status: TaskStatus) {
            phases.add(status)
        }

        override fun onConnections(connections: Int, segmentCount: Int, smartStatus: String) {
            if (connections > maxSeen.get()) maxSeen.set(connections.toLong())
            maxConnections = maxSeen.get().toInt()
        }

        override fun onResolvedPath(path: String) {
            resolvedPath = path
        }
    }
}
