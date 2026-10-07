package com.sohayb.n13download

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
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
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.ui.util.FileActions
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
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
 * End-to-end proof of the "Open Folder" fix.
 *
 * This downloads a **real** file through the real engine and storage layer, then
 * asks the shipped [FileActions] for the folder intents for that finished task and
 * checks them against the device's real package manager.
 *
 * That is the closest automated equivalent of the manual steps: download a file,
 * tap Open Folder, and confirm a file manager would actually be launched instead
 * of the "No app can open this file type" error.
 */
@RunWith(AndroidJUnit4::class)
class OpenFolderEndToEndDeviceTest {

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

    private val body = ByteArray(2 * 1024 * 1024) { index -> ((index * 5) and 0xFF).toByte() }

    @Before
    fun setUp() {
        context = InstrumentationRegistry.getInstrumentation().targetContext

        testRoot = File(context.getExternalFilesDir(null), "openfolder-e2e").apply {
            deleteRecursively()
            mkdirs()
        }
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        database.clearAllTables()
        repository = RoomDownloadRepository(database.downloadTaskDao())

        engine = OkHttpDownloadEngine()
        destinations = DestinationFactoryImpl(context)
        settingsProvider = FakeSettingsProvider(
            DownloadSettings(
                // A real on-disk folder, so both the app-storage and the path
                // fallback branches are exercised for real.
                destinationKind = DestinationKind.APP,
                destinationFolder = "openfolder-e2e/downloads",
                connectionMode = ConnectionMode.MANUAL,
                numThreads = 4,
                maxConcurrent = 2,
                blockPrivateUrls = false,
                maxRetries = 2,
                retryDelay = 1.0,
                duplicatePolicy = DuplicatePolicy.ALLOW,
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

        server = LocalHttpServer(body = body, supportsRange = true, filename = "openfolder.bin")
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

    /**
     * The real thing: complete a download, then confirm Open Folder points at the
     * folder and resolves to a launchable activity.
     */
    @Test
    fun afterARealDownload_openFolderResolvesToAFileManager() = runBlocking {
        val outcome = manager.addDownload(
            AddDownloadRequest(url = server.url(), filename = "openfolder.bin", startNow = true),
        )
        val taskId = (outcome as? AddDownloadOutcome.Queued)?.taskId
            ?: error("addDownload did not queue a task: $outcome")

        val finished = withTimeoutOrNull(60_000) {
            var current: DownloadTask? = null
            while (current == null || current.status != TaskStatus.COMPLETED) {
                current = repository.allNow().firstOrNull { it.id == taskId }
                if (current?.status?.isTerminal == true) break
                delay(200)
            }
            current
        }
        assertNotNull("The test download never finished", finished)
        assertEquals(
            "Download should have completed before Open Folder is exercised",
            TaskStatus.COMPLETED,
            finished!!.status,
        )

        // The file is genuinely there.
        val filePath = manager.filePathFor(finished)
        assertNotNull("A completed app-storage download must have a file path", filePath)
        assertTrue("The downloaded file should exist on disk", File(filePath!!).exists())

        // Now the actual assertion under test: the folder intents for this task.
        val intents = FileActions.folderIntentsForTask(finished, manager)
        assertTrue("Open Folder produced no candidate intents", intents.isNotEmpty())

        // At least one candidate must resolve, otherwise the user gets the toast
        // this bug was reported for. openFolder() tries them in order, so it is
        // the *set* that has to cover the device.
        val resolvable = intents.filter { it.resolveActivity(context.packageManager) != null }
        assertTrue(
            "No candidate folder intent resolves on this device — the user would see " +
                "\"No file manager can open this folder\". Tried: " +
                intents.map { "${it.data} [${it.type}]" },
            resolvable.isNotEmpty(),
        )

        // Every candidate must target a *folder*, never the downloaded file.
        val fileUri = Uri.fromFile(File(filePath!!))
        resolvable.forEach { intent ->
            assertTrue(
                "Open Folder must never address the downloaded file (${intent.data})",
                intent.data != fileUri,
            )
            assertTrue(
                "A folder intent must carry a folder MIME type, was ${intent.type}",
                intent.type == DocumentsContract.Document.MIME_TYPE_DIR ||
                    intent.type == "resource/folder",
            )
        }

        // The path fallback must point at the containing folder.
        val expectedFolder = File(filePath).parentFile!!.absolutePath
        val pathIntents = intents.filter { it.data?.scheme == "file" }
        assertTrue("Open Folder must offer a file:// fallback", pathIntents.isNotEmpty())
        pathIntents.forEach { intent ->
            assertEquals(
                "Open Folder must reveal the folder that holds the file",
                expectedFolder,
                intent.data!!.path,
            )
        }
    }

    /** Settings held in memory so the test never writes to the user's preferences. */
    private class FakeSettingsProvider(initial: DownloadSettings) : SettingsProvider {
        private val state = kotlinx.coroutines.flow.MutableStateFlow(initial)

        override val settings: kotlinx.coroutines.flow.StateFlow<DownloadSettings> = state

        override suspend fun current(): DownloadSettings = state.value

        override suspend fun update(settings: DownloadSettings) {
            state.value = settings
        }
    }

    private companion object {
        const val DB_NAME = "openfolder-e2e.db"
    }
}
