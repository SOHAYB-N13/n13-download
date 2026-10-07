package com.sohayb.n13download.domain.repository

import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import kotlinx.coroutines.flow.Flow

/**
 * Persistent store for download tasks.
 *
 * The Android implementation is Room, replacing the in-memory map the first
 * foundation build used.  Callers only ever see this interface.
 */
interface DownloadRepository {

    /** Every task, newest first. */
    fun observeAll(): Flow<List<DownloadTask>>

    /** Unfinished tasks, ordered the way the queue should be served. */
    fun observeQueue(): Flow<List<DownloadTask>>

    /** Finished tasks (completed, failed, cancelled), newest first. */
    fun observeHistory(): Flow<List<DownloadTask>>

    fun observeTask(id: Long): Flow<DownloadTask?>

    /**
     * An authoritative, uncached read of every task.
     *
     * [observeAll] is a UI stream: it is de-duplicated, shared and replayed so
     * screens stay cheap.  That makes it wrong as a scheduling input — a
     * one-shot read of it can be served instantly from the replay buffer and
     * therefore miss a row written a moment earlier.  The queue must decide what
     * to start from what is actually in the database, so it reads through this
     * instead.
     */
    suspend fun allNow(): List<DownloadTask>

    suspend fun get(id: Long): DownloadTask?

    /** Existing task for this URL, used by duplicate detection. */
    suspend fun findByUrl(url: String): DownloadTask?

    /** Existing task that already targets this file name in this folder. */
    suspend fun findByDestination(filename: String, directory: String): DownloadTask?

    suspend fun insert(task: DownloadTask): Long

    suspend fun update(task: DownloadTask)

    /**
     * Hot-path update: only the counters change, so the rest of the row is left
     * alone.  Called a few times per second per task at most.
     */
    suspend fun updateProgress(
        id: Long,
        downloadedBytes: Long,
        currentSpeed: Double,
        averageSpeed: Double,
        etaSeconds: Double?,
    )

    suspend fun updateStatus(id: Long, status: TaskStatus, error: String? = null)

    suspend fun updateConnections(id: Long, connections: Int, segmentCount: Int, smartStatus: String)

    suspend fun delete(id: Long)

    suspend fun clearHistory()

    /**
     * Sends tasks that were mid-transfer when the process died back to the
     * waiting queue.  Paused tasks are left paused because that was an explicit
     * user decision, not an interruption.
     *
     * @return how many tasks were recovered.
     */
    suspend fun recoverInterrupted(): Int
}
