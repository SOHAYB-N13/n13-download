package com.sohayb.n13download.data.local

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus

/**
 * Persisted download row.
 *
 * One flat table: every field the task model needs survives process death, so a
 * restart can resume exactly where the transfer stopped.  Status is stored as
 * the canonical N13 string, never an ordinal, so the database stays readable and
 * survives enum reordering.
 */
@Entity(
    tableName = "download_tasks",
    indices = [
        Index(value = ["status"]),
        Index(value = ["url"]),
        Index(value = ["priority", "created_at"]),
    ],
)
data class DownloadTaskEntity(
    @PrimaryKey(autoGenerate = true)
    @ColumnInfo(name = "id")
    val id: Long = 0L,

    @ColumnInfo(name = "url")
    val url: String,

    @ColumnInfo(name = "filename")
    val filename: String,

    @ColumnInfo(name = "directory")
    val directory: String,

    @ColumnInfo(name = "label")
    val label: String = "",

    @ColumnInfo(name = "category")
    val category: String = "General",

    @ColumnInfo(name = "total_size")
    val totalSize: Long = 0L,

    @ColumnInfo(name = "downloaded_size")
    val downloadedSize: Long = 0L,

    @ColumnInfo(name = "current_speed")
    val currentSpeed: Double = 0.0,

    @ColumnInfo(name = "average_speed")
    val averageSpeed: Double = 0.0,

    @ColumnInfo(name = "eta_seconds")
    val etaSeconds: Double? = null,

    @ColumnInfo(name = "status")
    val status: String = TaskStatus.QUEUED.value,

    @ColumnInfo(name = "priority")
    val priority: Int = DownloadPriority.DEFAULT_VALUE,

    @ColumnInfo(name = "created_at")
    val createdAt: Long = 0L,

    @ColumnInfo(name = "started_at")
    val startedAt: Long? = null,

    @ColumnInfo(name = "completed_at")
    val completedAt: Long? = null,

    @ColumnInfo(name = "retry_count")
    val retryCount: Int = 0,

    @ColumnInfo(name = "error")
    val error: String = "",

    @ColumnInfo(name = "connections")
    val connections: Int = 1,

    @ColumnInfo(name = "checksum")
    val checksum: String = "",

    @ColumnInfo(name = "content_type")
    val contentType: String = "",

    @ColumnInfo(name = "server")
    val server: String = "",

    @ColumnInfo(name = "supports_range")
    val supportsRange: Boolean = false,

    @ColumnInfo(name = "etag")
    val etag: String = "",

    @ColumnInfo(name = "last_modified")
    val lastModified: String = "",

    @ColumnInfo(name = "autostart")
    val autostart: Boolean = true,

    @ColumnInfo(name = "speed_limit_bps")
    val speedLimitBps: Long = 0L,

    @ColumnInfo(name = "num_threads")
    val numThreads: Int = 0,

    @ColumnInfo(name = "resolved_path")
    val resolvedPath: String = "",

    @ColumnInfo(name = "destination_kind")
    val destinationKind: String = DestinationKind.APP.storageValue,

    @ColumnInfo(name = "destination_uri")
    val destinationUri: String = "",

    @ColumnInfo(name = "smart_status")
    val smartStatus: String = "",

    @ColumnInfo(name = "segment_count")
    val segmentCount: Int = 0,
)

fun DownloadTaskEntity.toDomain(): DownloadTask = DownloadTask(
    id = id,
    url = url,
    filename = filename,
    directory = directory,
    label = label,
    category = category,
    totalSize = totalSize,
    downloadedSize = downloadedSize,
    currentSpeed = currentSpeed,
    averageSpeed = averageSpeed,
    etaSeconds = etaSeconds,
    status = TaskStatus.fromValue(status),
    priority = DownloadPriority.of(priority),
    createdAt = createdAt,
    startedAt = startedAt,
    completedAt = completedAt,
    retryCount = retryCount,
    error = error,
    connections = connections,
    checksum = checksum,
    contentType = contentType,
    server = server,
    supportsRange = supportsRange,
    etag = etag,
    lastModified = lastModified,
    autostart = autostart,
    speedLimitBps = speedLimitBps,
    numThreads = numThreads,
    resolvedPath = resolvedPath,
    destinationKind = DestinationKind.fromStorage(destinationKind),
    destinationUri = destinationUri,
    smartStatus = smartStatus,
    segmentCount = segmentCount,
)

fun DownloadTask.toEntity(): DownloadTaskEntity = DownloadTaskEntity(
    id = id,
    url = url,
    filename = filename,
    directory = directory,
    label = label,
    category = category,
    totalSize = totalSize,
    downloadedSize = downloadedSize,
    currentSpeed = currentSpeed,
    averageSpeed = averageSpeed,
    etaSeconds = etaSeconds,
    status = status.value,
    priority = priority.value,
    createdAt = createdAt,
    startedAt = startedAt,
    completedAt = completedAt,
    retryCount = retryCount,
    error = error,
    connections = connections,
    checksum = checksum,
    contentType = contentType,
    server = server,
    supportsRange = supportsRange,
    etag = etag,
    lastModified = lastModified,
    autostart = autostart,
    speedLimitBps = speedLimitBps,
    numThreads = numThreads,
    resolvedPath = resolvedPath,
    destinationKind = destinationKind.storageValue,
    destinationUri = destinationUri,
    smartStatus = smartStatus,
    segmentCount = segmentCount,
)
