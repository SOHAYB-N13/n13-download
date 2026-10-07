package com.sohayb.n13download.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
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
        WHERE status NOT IN ('Complete', 'Failed', 'Cancelled', 'Removed')
        ORDER BY priority ASC, created_at ASC
        """,
    )
    fun observeQueue(): Flow<List<DownloadTaskEntity>>

    @Query(
        """
        SELECT * FROM download_tasks
        WHERE status IN ('Complete', 'Failed', 'Cancelled')
        ORDER BY COALESCE(completed_at, created_at) DESC
        """,
    )
    fun observeHistory(): Flow<List<DownloadTaskEntity>>

    @Query("SELECT * FROM download_tasks WHERE id = :id")
    fun observeById(id: Long): Flow<DownloadTaskEntity?>

    @Query("SELECT * FROM download_tasks WHERE id = :id")
    suspend fun getById(id: Long): DownloadTaskEntity?

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
        WHERE status IN ('Queued', 'Analyzing', 'Starting', 'Downloading', 'Paused', 'Merging', 'Verifying')
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
            started_at = CASE WHEN :status = 'Starting' THEN COALESCE(started_at, :now) ELSE started_at END,
            completed_at = CASE
                WHEN :status IN ('Complete', 'Failed', 'Cancelled', 'Removed') THEN COALESCE(completed_at, :now)
                WHEN :status = 'Queued' THEN NULL
                ELSE completed_at END,
            current_speed = CASE WHEN :status IN ('Complete', 'Failed', 'Cancelled', 'Paused') THEN 0 ELSE current_speed END
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

    @Query("DELETE FROM download_tasks WHERE status IN ('Complete', 'Failed', 'Cancelled')")
    suspend fun clearHistory()

    /**
     * A task found mid-transfer after a crash goes back to the waiting queue.
     * PAUSED is deliberately excluded: that was a user decision, not an
     * interruption, so it survives a restart as PAUSED.
     */
    @Query(
        """
        UPDATE download_tasks
        SET status = 'Queued', started_at = NULL, current_speed = 0, average_speed = 0, eta_seconds = NULL
        WHERE status IN ('Analyzing', 'Starting', 'Downloading', 'Merging', 'Verifying')
        """,
    )
    suspend fun requeueInterrupted(): Int

    /** Clears live counters for rows that are not actually running any more. */
    @Query(
        """
        UPDATE download_tasks
        SET current_speed = 0, eta_seconds = NULL, connections = 1
        WHERE status IN ('Queued', 'Paused', 'Complete', 'Failed', 'Cancelled', 'Removed')
        """,
    )
    suspend fun clearStaleRuntimeState()
}
