package com.sohayb.n13download.domain.model

/**
 * A single download, from the moment it is queued until it is finished or
 * discarded.
 *
 * The field set is the Windows N13 `DownloadTask` (`core/task.py`) plus the
 * Android-specific destination metadata.  It is free of Room, OkHttp and Compose
 * types so the engine, the queue and the UI can all share one representation.
 *
 * Mutation happens through [transitionTo] / [copy], never by flipping flags —
 * a task has exactly one [status].
 */
data class DownloadTask(
    val id: Long = 0L,
    val url: String = "",
    val filename: String = "",
    /** Display folder shown in the UI; the real target may be a content URI. */
    val directory: String = "",
    val label: String = "",
    val category: String = "General",

    val totalSize: Long = 0L,
    val downloadedSize: Long = 0L,
    val currentSpeed: Double = 0.0,
    val averageSpeed: Double = 0.0,
    val etaSeconds: Double? = null,

    val status: TaskStatus = TaskStatus.QUEUED,
    val priority: DownloadPriority = DownloadPriority.DEFAULT,
    val createdAt: Long = 0L,
    val startedAt: Long? = null,
    val completedAt: Long? = null,
    val retryCount: Int = 0,

    val error: String = "",
    val connections: Int = 1,
    val checksum: String = "",
    val contentType: String = "",
    val server: String = "",
    val supportsRange: Boolean = false,
    val etag: String = "",
    val lastModified: String = "",

    /** Start as soon as a queue slot frees up. */
    val autostart: Boolean = true,
    /** Per-download cap in bytes/second; 0 = inherit the global setting. */
    val speedLimitBps: Long = 0L,
    /** Per-download connection override; 0 = inherit the global setting. */
    val numThreads: Int = 0,

    /** Resolved destination of the finished file. */
    val resolvedPath: String = "",
    val destinationKind: DestinationKind = DestinationKind.APP,
    /** SAF tree or MediaStore document URI, when the destination needs one. */
    val destinationUri: String = "",

    /** Live Smart-mode connection status, e.g. "4" or "4->6". */
    val smartStatus: String = "",
    /** How many segments the current transfer is split into. */
    val segmentCount: Int = 0,
) {
    val percent: Double get() = com.sohayb.n13download.core.N13Format.percent(downloadedSize, totalSize)

    val remainingBytes: Long get() = (totalSize - downloadedSize).coerceAtLeast(0L)

    val hasKnownSize: Boolean get() = totalSize > 0L

    val isRunning: Boolean get() = status.isRunning

    /** True once the task will never progress again on its own. */
    val isTerminal: Boolean get() = status.isTerminal

    val elapsedSeconds: Double
        get() {
            val end = completedAt ?: System.currentTimeMillis()
            val start = startedAt ?: createdAt
            return ((end - start) / 1000.0).coerceAtLeast(0.0)
        }

    /** Whether a retry makes sense for the current state. */
    val isRetryable: Boolean
        get() = status == TaskStatus.FAILED || status == TaskStatus.CANCELLED ||
            status == TaskStatus.COMPLETED

    /** The MIME type derived from the server response, for share/open intents. */
    val mimeType: String
        get() = contentType.substringBefore(';').trim().ifBlank { "*/*" }

    /** True once the file exists on disk and can be opened or shared. */
    val isOpenable: Boolean
        get() = status == TaskStatus.COMPLETED && (resolvedPath.isNotBlank() || destinationUri.isNotBlank())

    /**
     * Validated state change.  Throws [TransitionException] on an illegal move so
     * a bug cannot silently corrupt the lifecycle.
     */
    fun transitionTo(target: TaskStatus, errorMessage: String? = null): DownloadTask {
        if (target == status) return this
        if (!status.canTransitionTo(target)) {
            throw TransitionException("task $id: illegal transition ${status.value} -> ${target.value}")
        }
        return applyStatus(target, errorMessage)
    }

    /**
     * Unchecked status setter, reserved for crash recovery: the recovery scanner
     * may find a task mid-flight and forcing it back to QUEUED is not a legal
     * transition by design.
     */
    fun forceStatus(target: TaskStatus, errorMessage: String? = null): DownloadTask =
        applyStatus(target, errorMessage)

    private fun applyStatus(target: TaskStatus, errorMessage: String?): DownloadTask {
        val now = System.currentTimeMillis()
        return copy(
            status = target,
            error = errorMessage ?: if (target == TaskStatus.QUEUED) "" else error,
            startedAt = when {
                target == TaskStatus.STARTING && startedAt == null -> now
                target == TaskStatus.QUEUED -> null
                else -> startedAt
            },
            completedAt = when {
                target.isTerminal -> completedAt ?: now
                target == TaskStatus.QUEUED -> null
                else -> completedAt
            },
            currentSpeed = if (target.isTerminal || target == TaskStatus.PAUSED) 0.0 else currentSpeed,
            etaSeconds = if (target.isTerminal || target == TaskStatus.PAUSED) null else etaSeconds,
        )
    }

    /** Sends a failed / cancelled / completed task back to the waiting queue. */
    fun requeued(reason: String = ""): DownloadTask = copy(
        status = TaskStatus.QUEUED,
        error = reason,
        completedAt = null,
        startedAt = null,
        currentSpeed = 0.0,
        averageSpeed = 0.0,
        etaSeconds = null,
        downloadedSize = if (status == TaskStatus.COMPLETED) 0L else downloadedSize,
    )
}
