package com.sohayb.n13download

import android.content.Context
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.data.local.N13Database
import com.sohayb.n13download.data.repository.RoomDownloadRepository
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Verifies that finished downloads actually reach the History screen.
 *
 * The reported symptom was History staying on "No history yet" after downloads
 * had completed.  The list is derived from a SQL `status IN (...)` filter whose
 * literals must match exactly what the app writes to disk, so the risk is a
 * silent divergence between the two — nothing crashes, the rows simply never
 * match and the screen stays empty.
 *
 * These checks read through the same [RoomDownloadRepository] the UI's view
 * model uses, so a mismatch here is the same one a user would see.
 */
@RunWith(AndroidJUnit4::class)
class HistoryDeviceTest {

    private lateinit var context: Context
    private lateinit var database: N13Database
    private lateinit var repository: RoomDownloadRepository

    @Before
    fun setUp() {
        context = InstrumentationRegistry.getInstrumentation().targetContext
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        database.clearAllTables()
        repository = RoomDownloadRepository(database.downloadTaskDao())
    }

    @After
    fun tearDown() {
        database.close()
        File(context.getDatabasePath(DB_NAME).parentFile ?: context.filesDir, DB_NAME).delete()
    }

    /**
     * Every status a user would expect to find in History must be recognised by
     * the history query.  If any one of them were missing from the SQL list,
     * those downloads would silently vanish from the screen.
     */
    @Test
    fun everyUserVisibleTerminalStatus_appearsInHistory() = runBlocking {
        val visible = listOf(TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

        visible.forEachIndexed { index, status ->
            repository.insert(
                task(
                    id = index + 1L,
                    filename = "finished-${status.value}.bin",
                    status = status,
                    completedAt = 1_000L + index,
                ),
            )
        }

        val names = repository.observeHistory().first().map { it.filename }.toSet()

        for (status in visible) {
            assertTrue(
                "a ${status.value} download must be listed in history; saw $names",
                names.contains("finished-${status.value}.bin"),
            )
        }
    }

    /**
     * REMOVED is terminal but deliberately *not* history.
     *
     * It is the internal marker for "the user deleted this entry", so surfacing
     * it would resurrect exactly what they asked to get rid of.  Pinned here so
     * a future change to the terminal set cannot quietly reintroduce it.
     */
    @Test
    fun removedEntries_doNotAppearInHistory() = runBlocking {
        repository.insert(
            task(id = 1L, filename = "deleted.bin", status = TaskStatus.REMOVED, completedAt = 3_000L),
        )

        val names = repository.observeHistory().first().map { it.filename }.toSet()
        assertTrue(
            "a removed entry must not be shown in history; saw $names",
            !names.contains("deleted.bin"),
        )
    }

    /** Unfinished work must not pollute History. */
    @Test
    fun activeWork_isNotHistory() = runBlocking {
        repository.insert(task(id = 1L, filename = "done.bin", status = TaskStatus.COMPLETED, completedAt = 2_000L))
        repository.insert(task(id = 2L, filename = "running.bin", status = TaskStatus.DOWNLOADING))
        repository.insert(task(id = 3L, filename = "waiting.bin", status = TaskStatus.QUEUED))
        repository.insert(task(id = 4L, filename = "held.bin", status = TaskStatus.PAUSED))

        val names = repository.observeHistory().first().map { it.filename }.toSet()
        assertEquals(setOf("done.bin"), names)
    }

    /** The queue view is the exact complement: unfinished work only. */
    @Test
    fun queueExcludesEverythingInHistory() = runBlocking {
        repository.insert(task(id = 1L, filename = "done.bin", status = TaskStatus.COMPLETED, completedAt = 2_000L))
        repository.insert(task(id = 2L, filename = "running.bin", status = TaskStatus.DOWNLOADING))

        val queued = repository.observeQueue().first().map { it.filename }.toSet()
        assertEquals(setOf("running.bin"), queued)
    }

    /**
     * The History the user sees must survive the app being closed and reopened,
     * which for this layer means the rows are read back from a fresh handle on
     * the same database file.
     */
    @Test
    fun history_survivesADatabaseReopen() = runBlocking {
        repository.insert(
            task(
                id = 1L,
                filename = "kept.bin",
                status = TaskStatus.COMPLETED,
                completedAt = 5_000L,
            ),
        )
        database.close()

        database = Room.databaseBuilder(context, N13Database::class.java, DB_NAME).build()
        val reopened = RoomDownloadRepository(database.downloadTaskDao())

        val names = reopened.observeHistory().first().map { it.filename }.toSet()
        assertTrue("the completed download must still be in history; saw $names", names.contains("kept.bin"))
    }

    /** Newest finish first, which is the order the screen presents. */
    @Test
    fun history_isOrderedNewestFinishFirst() = runBlocking {
        repository.insert(task(id = 1L, filename = "old.bin", status = TaskStatus.COMPLETED, completedAt = 1_000L))
        repository.insert(task(id = 2L, filename = "new.bin", status = TaskStatus.COMPLETED, completedAt = 9_000L))
        repository.insert(task(id = 3L, filename = "mid.bin", status = TaskStatus.COMPLETED, completedAt = 5_000L))

        val order = repository.observeHistory().first().map { it.filename }
        assertEquals(listOf("new.bin", "mid.bin", "old.bin"), order)
    }

    private fun task(
        id: Long,
        filename: String,
        status: TaskStatus,
        completedAt: Long? = null,
    ) = DownloadTask(
        id = id,
        url = "http://example.test/$filename",
        filename = filename,
        directory = "Downloads/N13-Download",
        status = status,
        totalSize = 1024L,
        downloadedSize = if (status == TaskStatus.COMPLETED) 1024L else 0L,
        createdAt = 1_000L,
        completedAt = completedAt,
    )

    private companion object {
        const val DB_NAME = "n13-history-test.db"
    }
}
