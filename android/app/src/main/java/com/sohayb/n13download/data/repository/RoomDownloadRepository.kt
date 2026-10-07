package com.sohayb.n13download.data.repository

import com.sohayb.n13download.data.local.DownloadTaskDao
import com.sohayb.n13download.data.local.DownloadTaskEntity
import com.sohayb.n13download.data.local.toDomain
import com.sohayb.n13download.data.local.toEntity
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.domain.repository.DownloadRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.shareIn

/**
 * Room-backed [DownloadRepository].
 *
 * This is what makes the queue survive process death: every status change and
 * every throttled progress write lands in SQLite, and the UI observes the same
 * rows through Flows.
 *
 * ## Why everything shares one upstream flow
 *
 * Room invalidates on *writes to the table*, not on the specific columns a query
 * reads.  A single progress tick therefore re-runs every active `@Query`, and
 * mapping each one to domain objects separately meant the same rows were decoded
 * three or four times per tick.  The three public lists are instead derived from
 * one shared, mapped stream, so a tick decodes the table once and the projections
 * are cheap in-memory filters.
 */
class RoomDownloadRepository(
    private val dao: DownloadTaskDao,
    scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
) : DownloadRepository {

    /**
     * The single upstream: every row, decoded once, de-duplicated by content.
     *
     * `replay = 1` keeps the newest snapshot available to a subscriber that
     * arrives between writes (a screen opened while idle), which avoids a
     * redundant re-query on every navigation.
     */
    private val allTasks: Flow<List<DownloadTask>> = dao.observeAll()
        .map { rows -> rows.map(DownloadTaskEntity::toDomain) }
        .distinctUntilChanged()
        .shareIn(scope, SharingStarted.WhileSubscribed(REPLAY_TIMEOUT_MILLIS), replay = 1)

    override fun observeAll(): Flow<List<DownloadTask>> = allTasks

    override fun observeQueue(): Flow<List<DownloadTask>> =
        allTasks.map { tasks -> tasks.filter { !it.status.isTerminal } }
            .distinctUntilChanged()

    override fun observeHistory(): Flow<List<DownloadTask>> =
        allTasks.map { tasks ->
            tasks.filter { it.status == TaskStatus.COMPLETED || it.status == TaskStatus.FAILED || it.status == TaskStatus.CANCELLED }
                .sortedByDescending { it.completedAt ?: it.createdAt }
        }.distinctUntilChanged()

    override fun observeTask(id: Long): Flow<DownloadTask?> =
        dao.observeById(id).map { it?.toDomain() }.distinctUntilChanged()

    override suspend fun get(id: Long): DownloadTask? = dao.getById(id)?.toDomain()

    /**
     * Straight from SQLite, bypassing the shared UI stream.
     *
     * The queue schedules from this: reading [observeAll] with `.first()` can be
     * satisfied by the replay buffer and hand back a snapshot from before the
     * row was written, which is how a freshly added download could sit in the
     * list at 0% forever while the queue believed there was nothing to start.
     */
    override suspend fun allNow(): List<DownloadTask> =
        dao.getAllOnce().map(DownloadTaskEntity::toDomain)

    override suspend fun findByUrl(url: String): DownloadTask? = dao.findByUrl(url)?.toDomain()

    override suspend fun findByDestination(filename: String, directory: String): DownloadTask? =
        dao.findByDestination(filename, directory)?.toDomain()

    override suspend fun insert(task: DownloadTask): Long = dao.insert(task.toEntity())

    override suspend fun update(task: DownloadTask) = dao.update(task.toEntity())

    override suspend fun updateProgress(
        id: Long,
        downloadedBytes: Long,
        currentSpeed: Double,
        averageSpeed: Double,
        etaSeconds: Double?,
    ) = dao.updateProgress(id, downloadedBytes, currentSpeed, averageSpeed, etaSeconds)

    override suspend fun updateStatus(id: Long, status: TaskStatus, error: String?) {
        // The lifecycle is validated against the same transition table the Windows
        // engine uses.  A violation is logged rather than thrown: after a crash or
        // a killed foreground service the stored state can legitimately be behind
        // reality, and a bookkeeping mismatch must never wedge a user's download.
        val current = dao.getById(id)?.toDomain()
        if (current != null && !current.status.canTransitionTo(status) && current.status != status) {
            android.util.Log.w(
                "N13Repository",
                "task $id: ${current.status.value} -> ${status.value} is not a legal transition; " +
                    "applying it as a recovery",
            )
        }
        dao.updateStatus(id, status.value, error, System.currentTimeMillis())
    }

    override suspend fun updateConnections(
        id: Long,
        connections: Int,
        segmentCount: Int,
        smartStatus: String,
    ) = dao.updateConnections(id, connections, segmentCount, smartStatus)

    override suspend fun delete(id: Long) = dao.deleteById(id)

    override suspend fun clearHistory() = dao.clearHistory()

    override suspend fun recoverInterrupted(): Int {
        val recovered = dao.requeueInterrupted()
        // Rows that are not running any more must not keep advertising a speed.
        dao.clearStaleRuntimeState()
        return recovered
    }

    private companion object {
        /**
         * How long the shared stream keeps its buffer alive after the last
         * collector leaves.  Long enough to survive a screen switch, short
         * enough that an idle app holds nothing.
         */
        const val REPLAY_TIMEOUT_MILLIS = 5_000L
    }
}
