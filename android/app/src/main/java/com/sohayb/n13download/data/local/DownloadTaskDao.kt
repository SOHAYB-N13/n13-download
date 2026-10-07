package com.sohayb.n13download.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import com.sohayb.n13download.domain.model.TaskStatusValues
import kotlinx.coroutines.flow.Flow

/**
 * Data access for download tasks.
 *
 * Ordering lives in SQL rather than in Kotlin so the list the UI observes is
 * already in the order the queue will serve it, and so a large history never has
 * to be sorted in memory.
 */
@Dao
interface DownloadTaskDao {

    @Query("SELECT * FROM download_tasks ORDER BY created_at DESC")
    fun observeAll(): Flow<List<DownloadTaskEntity>>

    /** Unfinished work, in service order: highest priority (lowest number) first. */
    @Query(
        """
        SELECT * FROM download_tasks
        WHERE status NOT IN (${TaskStatusValues.SQL_TERMINAL})
        ORDER BY priority ASC, created_at ASC
        """,
    )
    fun observeQueue(): Flow<List<DownloadTaskEntity>>

    /**
     * Completed, failed and cancelled work, newest finish first.
     *
     * The status list comes from [TaskStatusValues.SQL_HISTORY], so it is impossible
     * for this query to stop matching the values the app actually writes.
     */
    @Query(
        """
        SELECT * FROM download_tasks
        WHERE status IN (${TaskStatusValues.SQL_HISTORY})
        ORDER BY COALESCE(completed_at, created_at) DESC
        """,
    )
    fun observeHistory(): Flow<List<DownloadTaskEntity>>

    @Query("SELECT * FROM download_tasks WHERE id = :id")
    fun observeById(id: Long): Flow<DownloadTaskEntity?>

    @Query("SELECT * FROM download_tasks WHERE id = :id")
    suspend fun getById(id: Long): DownloadTaskEntity?

    /**
     * A one-shot read of the whole table.
     *
     * Exists so the scheduler can see the database as it is *now*.  The `Flow`
     * queries above are cached and replayed for the UI, so a one-shot read of
     * one of them can return a snapshot that predates the caller's own write.
     */
    @Query("SELECT * FROM download_tasks ORDER BY created_at DESC")
    suspend fun getAllOnce(): List<DownloadTaskEntity>

    @Query("SELECT * FROM download_tasks WHERE url = :url ORDER BY created_at DESC LIMIT 1")
    suspend fun findByUrl(url: String): DownloadTaskEntity?

    @Query(
        """
        SELECT * FROM download_tasks
        WHERE filename = :filename AND directory = :directory
        ORDER BY created_at DESC LIMIT 1
        """,
    )
    suspend fun findByDestination(filename: String, directory: String): DownloadTaskEntity?

    @Query(
        """
        SELECT * FROM download_tasks
        WHERE status IN (${TaskStatusValues.SQL_ACTIVE})
        ORDER BY priority ASC, created_at ASC
        """,
    )
    suspend fun pendingTasks(): List<DownloadTaskEntity>

    @Insert
    suspend fun insert(entity: DownloadTaskEntity): Long

    @Update
    suspend fun update(entity: DownloadTaskEntity)

    @Query(
        """
        UPDATE download_tasks
        SET downloaded_size = :downloadedBytes,
            current_speed = :currentSpeed,
            average_speed = :averageSpeed,
            eta_seconds = :etaSeconds
        WHERE id = :id
        """,
    )
    suspend fun updateProgress(
        id: Long,
        downloadedBytes: Long,
        currentSpeed: Double,
        averageSpeed: Double,
        etaSeconds: Double?,
    )

    @Query(
        """
        UPDATE download_tasks
        SET status = :status,
            error = COALESCE(:error, error),
            started_at = CASE WHEN :status = ${TaskStatusValues.SQL_STARTING} THEN COALESCE(started_at, :now) ELSE started_at END,
            completed_at = CASE
                WHEN :status IN (${TaskStatusValues.SQL_TERMINAL}) THEN COALESCE(completed_at, :now)
                WHEN :status = ${TaskStatusValues.SQL_QUEUED} THEN NULL
                ELSE completed_at END,
            current_speed = CASE WHEN :status IN (${TaskStatusValues.SQL_TERMINAL}, ${TaskStatusValues.SQL_PAUSED}) THEN 0 ELSE current_speed END
        WHERE id = :id
        """,
    )
    suspend fun updateStatus(id: Long, status: String, error: String?, now: Long)

    @Query(
        """
        UPDATE download_tasks
        SET connections = :connections, segment_count = :segmentCount, smart_status = :smartStatus
        WHERE id = :id
        """,
    )
    suspend fun updateConnections(id: Long, connections: Int, segmentCount: Int, smartStatus: String)

    @Query("DELETE FROM download_tasks WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("DELETE FROM download_tasks WHERE status IN (${TaskStatusValues.SQL_HISTORY})")
    suspend fun clearHistory()

    /**
     * A task found mid-transfer after a crash goes back to the waiting queue.
     * PAUSED is deliberately excluded: that was a user decision, not an
     * interruption, so it survives a restart as PAUSED.
     */
    @Query(
        """
        UPDATE download_tasks
        SET status = ${TaskStatusValues.SQL_QUEUED}, started_at = NULL,
            current_speed = 0, average_speed = 0, eta_seconds = NULL
        WHERE status IN (${TaskStatusValues.SQL_INTERRUPTED})
        """,
    )
    suspend fun requeueInterrupted(): Int

    /** Clears live counters for rows that are not actually running any more. */
    @Query(
        """
        UPDATE download_tasks
        SET current_speed = 0, eta_seconds = NULL, connections = 1
        WHERE status NOT IN (${TaskStatusValues.SQL_RUNNING})
        """,
    )
    suspend fun clearStaleRuntimeState()
}
