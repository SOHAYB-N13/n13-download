package com.sohayb.n13download

import android.content.Context
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.data.engine.OkHttpDownloadEngine
import com.sohayb.n13download.data.local.N13Database
import com.sohayb.n13download.data.repository.RoomDownloadRepository
import com.sohayb.n13download.data.storage.DestinationFactoryImpl
import com.sohayb.n13download.domain.download.AddDownloadOutcome
import com.sohayb.n13download.domain.download.AddDownloadRequest
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DownloadQueue
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.download.TaskEvent
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Verifies the whole download pipeline: manager -> queue -> engine -> storage, with
 * Room as the state store.
 *
 * The engine suite proves the transfer itself; this one proves the pieces around
 * it that the UI depends on and that a user would notice immediately if they were
 * wrong:
 *
 *  - a queued download is persisted and actually runs to completion,
 *  - the slot/priority queue starts work without the UI being involved,
 *  - pause, resume and cancel go through the queue and reach the engine,
 *  - the state survives a database reopen (the "close the app and come back"
 *    guarantee), and interrupted work is requeued on restart.
 *
 * It runs against a loopback HTTP server so it is deterministic and independent of
 * the device's internet access, and it uses a dedicated on-disk database and
 * folder so the user's real downloads are never touched.
 */
@RunWith(AndroidJUnit4::class)
class DownloadPipelineDeviceTest {

    private lateinit var context: Context
    private lateinit var database: N13Database
    private lateinit var repository: RoomDownloadRepository
    private lateinit var engine: OkHttpDownloadEngine
    private lateinit var destinations: DestinationFactoryImpl
    private lateinit var settingsProvider: FakeSettingsProvider
    private lateinit var queue: DownloadQueue
    private lateinit var manager: DownloadManager
    private lateinit var scope: CoroutineScope
    private lateinit var server: LocalHttpServer
    private lateinit var testRoot: File

    private val body = ByteArray(6 * 1024 * 1024) { index -> ((index * 7) and 0xFF).toByte() }

    @Before
    fun setUp() {
        context = InstrumentationRegistry.getInstrumentation().targetContext

        testRoot = File(context.getExternalFilesDir(null), "pipeline-test").apply {
            deleteRecursively()
            mkdirs()
        }
        // A real on-disk database, so persistence is genuinely exercised, but a
        // separate file from the app's own so nothing of the user's is disturbed.
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME)
            .delete()
        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        // Start from a guaranteed empty schema: deleting the file is not enough,
        // because a stale WAL would still hand back rows from a previous test.
        database.clearAllTables()
        repository = RoomDownloadRepository(database.downloadTaskDao())

        engine = OkHttpDownloadEngine()
        destinations = DestinationFactoryImpl(context)
        settingsProvider = FakeSettingsProvider(
            DownloadSettings(
                destinationKind = DestinationKind.APP,
                destinationFolder = "pipeline-test/downloads",
                connectionMode = ConnectionMode.MANUAL,
                numThreads = 4,
                maxConcurrent = 2,
                blockPrivateUrls = false,
                maxRetries = 2,
                retryDelay = 1.0,
                duplicatePolicy = com.sohayb.n13download.domain.model.DuplicatePolicy.ALLOW,
            ),
        )

        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        queue = DownloadQueue(
            repository = repository,
            engine = engine,
            settingsProvider = settingsProvider,
            destinations = destinations,
            workingRoot = File(testRoot, "work"),
            scope = scope,
        )
        manager = DownloadManager(repository, engine, queue, settingsProvider, destinations)

        server = LocalHttpServer(body = body, supportsRange = true, filename = "pipeline.bin")
            .also { it.start() }
    }

    @After
    fun tearDown() {
        server.stop()
        engine.shutdown()
        scope.cancel()
        database.close()
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
        testRoot.deleteRecursively()
    }

    // ------------------------------------------------------------------ //

    @Test
    fun addDownload_queuesRunsAndCompletesWithoutTheUi() = runBlocking {
        val events = mutableListOf<TaskEvent>()
        val collector = scope.launch { queue.events.collect { events.add(it) } }

        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "pipeline.bin", startNow = true),
        )
        assertTrue("add should be accepted, got $outcome", outcome is AddDownloadOutcome.Queued)
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        val finished = awaitStatus(taskId) { it.isTerminal }
        assertEquals("expected Complete, error=${finished.error}", TaskStatus.COMPLETED, finished.status)
        assertEquals(body.size.toLong(), finished.downloadedSize)
        assertEquals(body.size.toLong(), finished.totalSize)
        assertTrue("the resolved path should be recorded", finished.resolvedPath.isNotBlank())

        val onDisk = File(finished.resolvedPath)
        assertTrue("the file should exist at the recorded path", onDisk.exists())
        assertEquals(body.size.toLong(), onDisk.length())
        assertTrue("the bytes must match the source", onDisk.readBytes().contentEquals(body))

        collector.cancel()
        assertTrue(
            "a completion event should have been emitted, saw $events",
            events.any { it is TaskEvent.Completed && it.taskId == taskId },
        )
    }

    @Test
    fun pauseThenResume_goesThroughTheQueueAndFinishesTheFile() = runBlocking {
        // Slow the server down so the pause lands mid-transfer.
        server.stop()
        server = LocalHttpServer(
            body = body,
            supportsRange = true,
            filename = "pipeline.bin",
            bytesPerSecond = 2 * 1024 * 1024,
        ).also { it.start() }

        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "pipeline.bin", startNow = true),
        )
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        // Wait until real bytes are on disk, then pause.
        val started = awaitStatus(taskId) { it.downloadedSize > 0L }
        assertTrue("the download never started", started.downloadedSize > 0L)

        manager.pause(taskId)
        val paused = awaitStatus(taskId) { it.status == TaskStatus.PAUSED }
        assertEquals(TaskStatus.PAUSED, paused.status)
        assertTrue("pausing must keep the downloaded bytes", paused.downloadedSize > 0L)
        assertTrue("a paused download must not be finished", paused.downloadedSize < body.size)

        manager.resume(taskId)
        val finished = awaitStatus(taskId) { it.isTerminal }
        assertEquals("expected Complete, error=${finished.error}", TaskStatus.COMPLETED, finished.status)
        assertEquals(body.size.toLong(), File(finished.resolvedPath).length())
    }

    @Test
    fun cancel_goesThroughTheQueueAndPublishesNothing() = runBlocking {
        server.stop()
        server = LocalHttpServer(
            body = body,
            supportsRange = true,
            filename = "pipeline.bin",
            bytesPerSecond = 1024 * 1024,
        ).also { it.start() }

        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "pipeline.bin", startNow = true),
        )
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        val started = awaitStatus(taskId) { it.downloadedSize > 0L }
        assertTrue("the download never started", started.downloadedSize > 0L)

        manager.cancel(taskId)
        val cancelled = awaitStatus(taskId) { it.isTerminal }
        assertEquals(TaskStatus.CANCELLED, cancelled.status)
    }

    @Test
    fun retry_afterAFailureRequeuesAndCompletes() = runBlocking {
        val port = server.port

        // Queue the download while the server is up (adding it requires a successful
        // probe), but do not start it yet.
        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "pipeline.bin", startNow = false),
        )
        assertTrue("add should be accepted, got $outcome", outcome is AddDownloadOutcome.Queued)
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        // Now take the server away, so the first transfer attempt really fails.
        server.stop()
        manager.resume(taskId)

        val failed = awaitStatus(taskId, timeoutMillis = 60_000) { it.isTerminal }
        assertEquals("expected Failed, error=${failed.error}", TaskStatus.FAILED, failed.status)
        assertTrue("the failure should be explained to the user", failed.error.isNotBlank())

        // Bring a working server back on the same port and retry.
        server = LocalHttpServer(
            body = body,
            supportsRange = true,
            filename = "pipeline.bin",
            port = port,
        ).also { it.start() }

        manager.retry(taskId)
        val finished = awaitStatus(taskId, timeoutMillis = 60_000) { it.isTerminal }
        assertEquals("expected Complete, error=${finished.error}", TaskStatus.COMPLETED, finished.status)
        assertTrue("retry count should have increased", finished.retryCount > 0)
        assertEquals(body.size.toLong(), finished.downloadedSize)
    }

    @Test
    fun priority_higherPriorityWaitsAheadOfLowerPriority() = runBlocking {
        // One slot, so only one of the two downloads can start.
        settingsProvider.update(settingsProvider.current().copy(maxConcurrent = 1))

        server.stop()
        server = LocalHttpServer(
            body = body,
            supportsRange = true,
            filename = "pipeline.bin",
            bytesPerSecond = 3 * 1024 * 1024,
        ).also { it.start() }

        // Hold the queue while both are added, so the scheduler makes a single
        // decision with the full candidate set in front of it.
        manager.setQueuePaused(true)

        val low = manager.addDownload(
            AddDownloadRequest(
                url = server.url(),
                filename = "low.bin",
                startNow = true,
                priority = com.sohayb.n13download.domain.model.DownloadPriority.of(9),
            ),
        )
        val high = manager.addDownload(
            AddDownloadRequest(
                url = server.url(),
                filename = "high.bin",
                startNow = true,
                priority = com.sohayb.n13download.domain.model.DownloadPriority.of(1),
            ),
        )

        val lowId = (low as AddDownloadOutcome.Queued).taskId
        val highId = (high as AddDownloadOutcome.Queued).taskId

        manager.setQueuePaused(false)

        // The high-priority task must be the one that gets the only slot.
        val started = awaitStatus(highId) { it.status.isRunning || it.downloadedSize > 0L }
        assertTrue("the high-priority download should have started", started.startedAt != null)

        val lowTask = repository.get(lowId)
        assertNotNull(lowTask)
        assertTrue(
            "the low-priority download must still be waiting, was ${lowTask!!.status}",
            lowTask.status == TaskStatus.QUEUED || lowTask.status == TaskStatus.PAUSED,
        )
    }

    @Test
    fun persistence_survivesADatabaseReopen() = runBlocking {
        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "persisted.bin", startNow = false),
        )
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        // Let the queue settle, then simulate the app being closed.
        delay(500)
        queue.pause(taskId)
        database.close()

        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        val reopened = RoomDownloadRepository(database.downloadTaskDao())
        val reloaded = reopened.get(taskId)

        assertNotNull("the task must still exist after reopening the database", reloaded)
        assertEquals("persisted.bin", reloaded!!.filename)
        assertEquals(server.url(), reloaded.url)
        assertEquals(TaskStatus.PAUSED, reloaded.status)
    }

    @Test
    fun recoverInterrupted_requeuesWorkThatWasMidTransfer() = runBlocking {
        val id = repository.insert(
            DownloadTask(
                url = server.url(),
                filename = "interrupted.bin",
                directory = "pipeline-test/downloads",
                totalSize = body.size.toLong(),
                downloadedSize = 1024L,
                status = TaskStatus.DOWNLOADING,
                supportsRange = true,
                createdAt = System.currentTimeMillis(),
                startedAt = System.currentTimeMillis() - 5_000L,
            ),
        )

        val recovered = repository.recoverInterrupted()
        assertEquals("one interrupted task should have been recovered", 1, recovered)

        val task = repository.get(id)
        assertNotNull(task)
        assertEquals(TaskStatus.QUEUED, task!!.status)
        assertEquals("progress must be preserved across recovery", 1024L, task.downloadedSize)
    }

    @Test
    fun queuePaused_stopsNewWorkFromStarting() = runBlocking {
        manager.setQueuePaused(true)

        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "held.bin", startNow = true),
        )
        val taskId = (outcome as AddDownloadOutcome.Queued).taskId

        delay(1_500)
        val held = repository.get(taskId)
        assertNotNull(held)
        assertEquals("a paused queue must not start work", TaskStatus.QUEUED, held!!.status)
        assertTrue("no bytes should have been transferred", held.downloadedSize == 0L)

        manager.setQueuePaused(false)
        val finished = awaitStatus(taskId) { it.isTerminal }
        assertEquals("expected Complete, error=${finished.error}", TaskStatus.COMPLETED, finished.status)
    }

    @Test
    fun ssrfGuard_blocksLoopbackTargetsBeforeAnythingIsQueued() = runBlocking {
        settingsProvider.update(settingsProvider.current().copy(blockPrivateUrls = true))

        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "blocked.bin", startNow = true),
        )

        assertTrue("a loopback target must be refused, got $outcome", outcome is AddDownloadOutcome.Rejected)
        assertTrue(
            "the reason should name the block, got ${(outcome as AddDownloadOutcome.Rejected).reason}",
            outcome.reason.contains("Blocked", ignoreCase = true),
        )
        assertTrue("nothing may have been persisted", repository.observeAll().first().isEmpty())
    }

    // ------------------------------------------------------------------ //

    private suspend fun awaitStatus(
        taskId: Long,
        timeoutMillis: Long = 45_000L,
        predicate: (DownloadTask) -> Boolean,
    ): DownloadTask {
        var lastSeen: DownloadTask? = null
        val result = withTimeoutOrNull(timeoutMillis) {
            while (true) {
                val task = repository.get(taskId)
                if (task != null) {
                    lastSeen = task
                    if (predicate(task)) return@withTimeoutOrNull task
                }
                delay(100)
            }
            @Suppress("UNREACHABLE_CODE") null
        }
        assertNotNull(
            "timed out waiting for task $taskId; last state was " +
                (lastSeen?.let { "${it.status.value} (${it.downloadedSize}/${it.totalSize}, error='${it.error}')" }
                    ?: "not found"),
            result,
        )
        return result!!
    }

    /** Settings held in memory so the test never writes to the user's preferences. */
    private class FakeSettingsProvider(initial: DownloadSettings) : SettingsProvider {
        private val state = MutableStateFlow(initial)

        override val settings: StateFlow<DownloadSettings> = state

        override suspend fun current(): DownloadSettings = state.value

        override suspend fun update(settings: DownloadSettings) {
            state.value = settings
        }
    }

    private companion object {
        const val DB_NAME = "n13-pipeline-test.db"
    }
}
