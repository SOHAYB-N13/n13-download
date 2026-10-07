package com.sohayb.n13download.data.repository

import com.sohayb.n13download.data.local.DownloadTaskDao
import com.sohayb.n13download.data.local.toDomain
import com.sohayb.n13download.data.local.toEntity
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.domain.repository.DownloadRepository
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

/**
 * Room-backed [DownloadRepository].
 *
 * This is what makes the queue survive process death: every status change and
 * every throttled progress write lands in SQLite, and the UI observes the same
 * rows through Flows.
 */
class RoomDownloadRepository(
    private val dao: DownloadTaskDao,
) : DownloadRepository {

    override fun observeAll(): Flow<List<DownloadTask>> =
        dao.observeAll().map { rows -> rows.map { it.toDomain() } }.distinctUntilChanged()

    override fun observeQueue(): Flow<List<DownloadTask>> =
        dao.observeQueue().map { rows -> rows.map { it.toDomain() } }.distinctUntilChanged()

    override fun observeHistory(): Flow<List<DownloadTask>> =
        dao.observeHistory().map { rows -> rows.map { it.toDomain() } }.distinctUntilChanged()

    override fun observeTask(id: Long): Flow<DownloadTask?> =
        dao.observeById(id).map { it?.toDomain() }.distinctUntilChanged()

    override suspend fun get(id: Long): DownloadTask? = dao.getById(id)?.toDomain()

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
}
