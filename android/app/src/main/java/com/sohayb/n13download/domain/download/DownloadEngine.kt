package com.sohayb.n13download.domain.download

import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import java.io.File

/**
 * The transfer engine contract.
 *
 * It owns bytes on the wire; [com.sohayb.n13download.domain.repository.DownloadRepository]
 * owns state.  The implementation is OkHttp-based and completely independent of
 * Compose, so it is testable and reusable on its own.
 */
interface DownloadEngine {

    /**
     * Inspects a link: follows redirects, determines the filename, size, MIME
     * type, server and Range support.  Never downloads the body.
     */
    suspend fun probe(
        url: String,
        settings: DownloadSettings,
        headOnly: Boolean = false,
    ): DownloadAnalysis

    /**
     * Transfers one task to completion.
     *
     * @param request everything the engine needs, including the working folder
     *   for `.part` files and the destination to publish into.
     * @param control pause/cancel signal, polled at every chunk boundary.
     * @param listener progress and phase callbacks, invoked from a background
     *   dispatcher and throttled internally.
     */
    suspend fun execute(
        request: EngineRequest,
        control: EngineControl,
        listener: EngineListener,
    ): EngineOutcome

    /** Releases pooled connections; called when the last download finishes. */
    fun shutdown()
}

/** Everything the engine needs for one transfer. */
data class EngineRequest(
    val task: DownloadTask,
    val settings: DownloadSettings,
    /** Folder for `.part` files.  Always app-private and always writable. */
    val workingDirectory: File,
    /** Where the merged file is published. */
    val destination: DownloadDestination,
    /** Resolved name, already de-duplicated against the destination. */
    val targetFilename: String,
    /** Applied to real bytes before they are written. */
    val globalLimiter: com.sohayb.n13download.core.BandwidthLimiter? = null,
    /** Per-download cap, layered on top of [globalLimiter]. */
    val taskLimiter: com.sohayb.n13download.core.BandwidthLimiter? = null,
)

/**
 * A piece of in-flight work the engine can abort immediately.
 *
 * Needed because a blocking socket read can sit for the whole read timeout: without
 * this, "pause" would only take effect once the server happened to send something,
 * which is not a real pause.
 */
fun interface CancellableWork {
    fun cancel()
}

/** Pause / cancel signal. */
interface EngineControl {
    val isCancelled: Boolean
    val isPaused: Boolean

    /** Suspends while paused; returns false when the task was cancelled. */
    suspend fun awaitResumed(): Boolean

    /** Registers in-flight work so a pause or cancel can abort it right away. */
    fun register(work: CancellableWork) = Unit

    fun unregister(work: CancellableWork) = Unit

    /** True once the user has asked to pause or cancel. */
    val stopRequested: Boolean get() = isPaused || isCancelled
}

/** Progress and phase reporting. */
interface EngineListener {
    /** @param speed bytes/second over the last sample window. */
    fun onProgress(
        downloadedBytes: Long,
        totalBytes: Long,
        speed: Double,
        averageSpeed: Double,
        etaSeconds: Double?,
    )

    /** Phase change: STARTING, DOWNLOADING, MERGING, VERIFYING. */
    fun onPhase(status: TaskStatus)

    /** Live connection count and Smart status text. */
    fun onConnections(connections: Int, segmentCount: Int, smartStatus: String)

    /** Smart-mode scaling note, e.g. "4->6". Kept separate from the connection count. */
    fun onSmartStatus(status: String) = Unit

    /** The resolved destination path, once known. */
    fun onResolvedPath(path: String)
}

sealed interface EngineOutcome {
    /**
     * @param totalBytes bytes written — carried on the outcome so the caller can
     *   persist the final counters *before* flipping the status.  Progress
     *   reporting is throttled and asynchronous, so without this a short download
     *   could be marked complete while still showing zero bytes.
     */
    data class Completed(val totalBytes: Long, val averageSpeed: Double) : EngineOutcome

    /** @param downloadedBytes bytes preserved on disk for a later resume. */
    data class Paused(val downloadedBytes: Long) : EngineOutcome

    data class Cancelled(val downloadedBytes: Long) : EngineOutcome

    data class Failed(val message: String) : EngineOutcome
}
