package com.sohayb.n13download.data.engine

import android.util.Log
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.core.ConnectionGovernor
import com.sohayb.n13download.core.DownloadPart
import com.sohayb.n13download.core.N13Errors
import com.sohayb.n13download.core.PartsPlanner
import com.sohayb.n13download.core.RetryPolicy
import com.sohayb.n13download.core.SmartOptimizer
import com.sohayb.n13download.core.SpeedTracker
import com.sohayb.n13download.domain.download.CancellableWork
import com.sohayb.n13download.domain.download.DownloadDestination
import com.sohayb.n13download.domain.download.DownloadEngine
import com.sohayb.n13download.domain.download.EngineControl
import com.sohayb.n13download.domain.download.EngineListener
import com.sohayb.n13download.domain.download.EngineOutcome
import com.sohayb.n13download.domain.download.EngineRequest
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.TaskStatus
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InterruptedIOException
import java.io.OutputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Real HTTP/HTTPS download engine built on OkHttp.
 *
 * Behaviour is ported from the Windows `core/download.py`:
 *
 *  - **Probe first, then decide.**  A server that supports byte ranges gets a
 *    segmented multi-connection transfer; one that does not gets a single stream.
 *    Multi-connection is never claimed for a server that cannot serve ranges.
 *  - **Parts are files.**  A part's length *is* its progress, so a crash can never
 *    desynchronise a counter from bytes on disk, and resume needs no state beyond
 *    the segment geometry.
 *  - **Retries are classified.**  Connection errors and 408/425/429/5xx are retried
 *    with exponential backoff and jitter; other 4xx fail immediately.  Before the
 *    first byte the budget is short and bounded so a dead server cannot stall
 *    startup.
 *  - **Pause is real.**  A barrier sits at every chunk boundary *and* after the
 *    throttle sleep, so a paused task stops writing immediately.
 *  - **Throttling shapes real bytes**, applied before each write.
 *
 * Everything runs on [Dispatchers.IO]; nothing touches the main thread, and no
 * file is ever read into memory as a whole.
 */
class OkHttpDownloadEngine : DownloadEngine {

    override suspend fun probe(
        url: String,
        settings: DownloadSettings,
        headOnly: Boolean,
    ): DownloadAnalysis = HttpProbe.probe(url, settings, headOnly)

    override suspend fun execute(
        request: EngineRequest,
        control: EngineControl,
        listener: EngineListener,
    ): EngineOutcome = withContext(Dispatchers.IO) {
        val settings = request.settings
        val client = OkHttpClients.transfer(settings)

        // ---- Discover the real size / Range support -----------------------
        listener.onPhase(TaskStatus.STARTING)
        val probe = runCatching { HttpProbe.probe(request.task.url, settings) }
            .onFailure { Log.d(TAG, "probe before transfer failed", it) }
            .getOrNull()

        val probeOk = probe?.ok == true
        val totalSize = if (probeOk) probe!!.totalSize else request.task.totalSize
        val supportsRange = if (probeOk) probe!!.supportsRange else request.task.supportsRange
        val transferUrl = probe?.finalUrl
            ?.takeIf { probeOk && it.isNotBlank() && it.startsWith("http") }
            ?: request.task.url

        if (!probeOk) {
            Log.d(TAG, "probe failed (${probe?.error}); attempting a direct download")
        }

        listener.onPhase(TaskStatus.DOWNLOADING)

        return@withContext if (supportsRange && totalSize > 0L) {
            downloadSegmented(request, transferUrl, totalSize, client, control, listener)
        } else {
            downloadSingleStream(request, transferUrl, totalSize, client, control, listener)
        }
    }

    override fun shutdown() = OkHttpClients.shutdown()

    // ------------------------------------------------------------------ //
    // Single-stream fallback
    // ------------------------------------------------------------------ //

    /**
     * Used when the server cannot serve byte ranges.
     *
     * Resume is impossible in that case — a ranged request would be answered with
     * the whole file — so bytes go straight into the destination and a retry
     * restarts from the beginning.  No staging copy is made, keeping disk usage at
     * one file instead of two.
     */
    private suspend fun downloadSingleStream(
        request: EngineRequest,
        transferUrl: String,
        totalSize: Long,
        client: OkHttpClient,
        control: EngineControl,
        listener: EngineListener,
    ): EngineOutcome {
        val settings = request.settings
        val destination = request.destination
        val name = request.targetFilename
        val speed = SpeedTracker()
        val maxAttempts = maxOf(1, settings.maxRetries)
        val startupAttempts = maxOf(1, minOf(maxAttempts, STARTUP_MAX_ATTEMPTS))

        var attempt = 0
        var firstByteSeen = false

        while (attempt < maxAttempts) {
            attempt++
            if (control.isCancelled) return EngineOutcome.Cancelled(0L)
            if (!control.awaitResumed()) return EngineOutcome.Paused(0L)

            // A fresh digest per attempt: the previous attempt's bytes are discarded.
            val digest = request.task.checksum.takeIf { it.isNotBlank() }?.let(DigestTarget::of)
            var written = 0L
            var output: OutputStream? = null

            try {
                destination.abort(name)
                output = destination.openOutput(name)
                val buffered = BufferedOutputStream(output, WRITE_BUFFER)

                val completed = run {
                    val call = client.newCall(buildGetRequest(transferUrl, settings, null))
                    val work = CancellableWork { call.cancel() }
                    control.register(work)
                    try {
                        call.execute().use { response ->
                        if (response.code !in ACCEPTED_STATUS) {
                            throw RetryPolicy.HttpStatusException(response.code, "HTTP ${response.code}")
                        }
                        if (attempt == 1 && RetryPolicy.isHtmlInterstitial(response.headers["Content-Type"])) {
                            throw RetryPolicy.HtmlInterstitialException()
                        }

                        val declaredTotal = response.headers["Content-Length"]?.toLongOrNull() ?: 0L
                        val effectiveTotal = if (totalSize > 0L) totalSize else declaredTotal
                        val source = response.body.source()
                        // Bound each read so a stalled socket cannot hold a pause.
                        source.timeout().timeout(READ_IDLE_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS)
                        val buffer = ByteArray(READ_CHUNK)
                        var reachedEnd = false
                        var idleRounds = 0

                        while (true) {
                            if (control.isCancelled) break
                            if (!control.awaitResumed()) break

                            val read = try {
                                source.read(buffer, 0, buffer.size)
                            } catch (idle: InterruptedIOException) {
                                if (control.stopRequested) break
                                idleRounds++
                                if (idleRounds > MAX_IDLE_ROUNDS) throw idle
                                continue
                            }
                            idleRounds = 0
                            if (read <= 0) {
                                reachedEnd = true
                                break
                            }

                            throttle(request, read)

                            // Pause barrier after the throttle sleep: nothing may
                            // reach the disk once the task is paused.
                            if (!control.awaitResumed()) break

                            buffered.write(buffer, 0, read)
                            digest?.update(buffer, 0, read)
                            written += read
                            firstByteSeen = true
                            speed.add(read.toLong())

                            val done = speed.bytesDownloaded()
                            listener.onProgress(
                                done,
                                effectiveTotal,
                                speed.averageSpeed(),
                                speed.overallSpeed(),
                                speed.etaSeconds(effectiveTotal),
                            )
                        }
                        buffered.flush()
                        reachedEnd
                    }
                    } finally {
                        control.unregister(work)
                    }
                }

                buffered.close()
                output = null

                if (control.isCancelled) {
                    destination.abort(name)
                    return EngineOutcome.Cancelled(0L)
                }
                if (control.isPaused) {
                    destination.abort(name)
                    return EngineOutcome.Paused(0L)
                }
                if (!completed) {
                    throw IOException("The server closed the connection early")
                }
                if (settings.verifySize && totalSize > 0L && written != totalSize) {
                    throw IOException("Incomplete download: $written/$totalSize bytes")
                }

                digest?.verify()?.let { mismatch ->
                    destination.abort(name)
                    return EngineOutcome.Failed(mismatch)
                }

                listener.onPhase(TaskStatus.VERIFYING)
                destination.finalize(name)
                listener.onResolvedPath(destination.filePath(name) ?: destination.displayPath)
                listener.onProgress(written, written, 0.0, speed.overallSpeed(), 0.0)
                Log.i(TAG, "single-stream download complete: $name ($written bytes)")
                return EngineOutcome.Completed(written, speed.overallSpeed())
            } catch (cancelled: CancellationException) {
                runCatching { output?.close() }
                runCatching { destination.abort(name) }
                throw cancelled
            } catch (failure: Throwable) {
                runCatching { output?.close() }
                runCatching { destination.abort(name) }

                // An aborted call is the pause/cancel taking effect, not an error.
                if (control.isCancelled) return EngineOutcome.Cancelled(0L)
                if (control.isPaused) return EngineOutcome.Paused(0L)

                val status = RetryPolicy.statusOf(failure)
                val retryable = (failure as? RetryPolicy.HttpStatusException)
                    ?.let { RetryPolicy.isRetryableStatus(it.code) }
                    ?: RetryPolicy.isRetryableException(failure)

                if (!retryable || attempt >= maxAttempts ||
                    (!firstByteSeen && attempt >= startupAttempts)
                ) {
                    val message = N13Errors.friendly(failure, status)
                    Log.w(TAG, "single-stream download failed after $attempt attempt(s): $message")
                    return EngineOutcome.Failed(message)
                }

                val backoff = RetryPolicy.delaySeconds(
                    attempt = attempt,
                    base = settings.retryDelay,
                    backoff = settings.retryBackoff,
                    jitter = settings.retryJitter,
                    maxDelay = settings.retryMaxDelay,
                    started = firstByteSeen,
                )
                Log.d(TAG, "retrying single-stream download in ${"%.1f".format(backoff)}s")
                if (!interruptibleSleep(backoff, control)) {
                    return if (control.isCancelled) EngineOutcome.Cancelled(0L) else EngineOutcome.Paused(0L)
                }
            }
        }
        return EngineOutcome.Failed("Download failed")
    }

    // ------------------------------------------------------------------ //
    // Segmented multi-connection download
    // ------------------------------------------------------------------ //

    private suspend fun downloadSegmented(
        request: EngineRequest,
        transferUrl: String,
        totalSize: Long,
        client: OkHttpClient,
        control: EngineControl,
        listener: EngineListener,
    ): EngineOutcome {
        val settings = request.settings
        val workingDirectory = request.workingDirectory
        val targetFile = File(workingDirectory, request.targetFilename)
        val store = PartStateStore(workingDirectory)

        // ---- Plan the segments -------------------------------------------
        val smartMode = settings.connectionMode == ConnectionMode.SMART
        val optimizer = SmartOptimizer(
            maxConnections = settings.smartMaxConnections,
            adaptive = settings.smartAdaptive,
            onStatus = { status -> listener.onSmartStatus(status) },
        )
        val plannedSegments: Int
        val governor: ConnectionGovernor?
        if (smartMode) {
            val started = optimizer.start(totalSize, supportsRange = true)
            plannedSegments = started.first
            governor = started.second
        } else {
            plannedSegments = settings.effectiveThreads(request.task.numThreads)
            governor = null
        }
        if (request.globalLimiter?.enabled == true || request.taskLimiter?.enabled == true) {
            optimizer.setSpeedLimited(true)
        }

        // ---- Reuse or rebuild the layout ---------------------------------
        val existing = store.load()
        val parts: List<DownloadPart> = when {
            existing != null && existing.url == transferUrl &&
                existing.totalSize == totalSize && existing.threads == plannedSegments -> {
                Log.i(TAG, "resuming saved segment layout (${existing.parts.size} segments)")
                store.toParts(existing, targetFile)
            }

            existing != null && existing.url == transferUrl && existing.totalSize == totalSize -> {
                Log.i(
                    TAG,
                    "connection count changed ${existing.threads} -> $plannedSegments; remapping segments",
                )
                PartsPlanner.remapForResume(
                    oldParts = store.toParts(existing, targetFile),
                    totalSize = totalSize,
                    numThreads = plannedSegments,
                    targetFile = targetFile,
                    minPartSize = MIN_PART_SIZE,
                )
            }

            else -> PartsPlanner.build(
                totalSize = totalSize,
                numThreads = plannedSegments,
                targetFile = targetFile,
                minPartSize = MIN_PART_SIZE,
            )
        }
        store.save(store.fromParts(transferUrl, totalSize, parts))

        val effectiveSegments = parts.size
        Log.i(
            TAG,
            "starting $effectiveSegments segment(s) for $totalSize bytes " +
                "(smart=$smartMode, adaptive=${settings.smartAdaptive})",
        )

        val speed = SpeedTracker()
        val alreadyDone = parts.sumOf { it.downloadedSize }
        speed.seed(alreadyDone)
        val completedBytes = AtomicLong(alreadyDone)
        val activeSegments = AtomicLong(0)

        listener.onConnections(0, effectiveSegments, "")

        // The segments run in a scope we can abandon.  A socket read that is already
        // blocked in the kernel is not guaranteed to wake up when the call is
        // cancelled, so waiting for every segment coroutine to return would make a
        // pause hang until the socket happened to give up.  Instead the loop below
        // watches for completion or a stop request, and on a stop the whole scope is
        // dropped immediately.
        val transferScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val reporter = transferScope.launch {
                while (isActive) {
                    val done = completedBytes.get()
                    listener.onProgress(
                        done,
                        totalSize,
                        speed.averageSpeed(),
                        speed.overallSpeed(),
                        speed.etaSeconds(totalSize),
                    )
                    optimizer.observe(done, totalSize)
                    delay(PROGRESS_INTERVAL_MILLIS)
                }
            }

            val partJobs = parts.map { part ->
                transferScope.async {
                    downloadPart(
                        part = part,
                        transferUrl = transferUrl,
                        client = client,
                        request = request,
                        control = control,
                        governor = governor,
                        optimizer = optimizer,
                        completedBytes = completedBytes,
                        speed = speed,
                        activeSegments = activeSegments,
                        listener = listener,
                        effectiveSegments = effectiveSegments,
                    )
                }
            }

            while (!control.stopRequested && !partJobs.all { it.isCompleted }) {
                delay(WATCH_INTERVAL_MILLIS)
            }

            reporter.cancel()
            if (control.stopRequested) {
                Log.i(TAG, "transfer abandoned on request; ${partJobs.count { it.isCompleted }} of ${partJobs.size} segments had finished")
            }
        } finally {
            // Cancelling a scope does not interrupt a kernel-blocked read, but it does
            // detach it, so this function can return on time either way.
            transferScope.cancel()
        }

        // ---- Decide what happened ----------------------------------------
        store.save(store.fromParts(transferUrl, totalSize, parts))

        if (control.isCancelled) return EngineOutcome.Cancelled(completedBytes.get())
        if (control.isPaused) return EngineOutcome.Paused(completedBytes.get())

        val incomplete = parts.filterNot { it.isComplete }
        if (incomplete.isNotEmpty()) {
            return EngineOutcome.Failed(
                "${incomplete.size} of ${parts.size} segments are incomplete - retry to resume",
            )
        }

        // ---- Merge and publish -------------------------------------------
        listener.onPhase(TaskStatus.MERGING)
        listener.onProgress(totalSize, totalSize, 0.0, speed.overallSpeed(), 0.0)

        val digest = request.task.checksum.takeIf { it.isNotBlank() }?.let(DigestTarget::of)
        val mergeError = mergeParts(parts, request.destination, request.targetFilename, totalSize, digest)
        if (mergeError != null) {
            Log.w(TAG, "merge failed: $mergeError")
            return EngineOutcome.Failed(mergeError)
        }

        digest?.verify()?.let { mismatch ->
            request.destination.abort(request.targetFilename)
            store.cleanup(parts)
            return EngineOutcome.Failed(mismatch)
        }

        listener.onPhase(TaskStatus.VERIFYING)
        store.cleanup(parts)
        listener.onResolvedPath(
            request.destination.filePath(request.targetFilename) ?: request.destination.displayPath,
        )
        listener.onProgress(totalSize, totalSize, 0.0, speed.overallSpeed(), 0.0)
        Log.i(TAG, "segmented download complete: ${request.targetFilename} ($totalSize bytes)")
        return EngineOutcome.Completed(totalSize, speed.overallSpeed())
    }

    /**
     * Downloads one byte range.
     *
     * A part file holds the bytes starting at its own range start, so on resume the
     * next request begins at `start + existingLength`.  A server that answers a
     * ranged request with a 200 sends the whole body, which is handled by
     * discarding the leading prefix and writing exactly this part's range.
     */
    private suspend fun downloadPart(
        part: DownloadPart,
        transferUrl: String,
        client: OkHttpClient,
        request: EngineRequest,
        control: EngineControl,
        governor: ConnectionGovernor?,
        optimizer: SmartOptimizer,
        completedBytes: AtomicLong,
        speed: SpeedTracker,
        activeSegments: AtomicLong,
        listener: EngineListener,
        effectiveSegments: Int,
    ): Boolean {
        val settings = request.settings
        val maxRetries = maxOf(1, settings.maxRetries)
        val startupAttempts = maxOf(1, minOf(maxRetries, STARTUP_MAX_ATTEMPTS))
        var firstByteSeen = false

        governor?.acquire()
        try {
            for (attempt in 1..maxRetries) {
                if (control.isCancelled) return false
                if (!control.awaitResumed()) return false

                if (part.isComplete) {
                    part.done = true
                    return true
                }

                val existingLength = part.downloadedSize
                var rangeStart = part.start + existingLength
                if (rangeStart > part.end) {
                    part.done = true
                    return true
                }

                try {
                    Log.d(TAG, "segment ${part.index}: request bytes=$rangeStart-${part.end}")
                    val call = client.newCall(
                        buildGetRequest(transferUrl, settings, "bytes=$rangeStart-${part.end}", segment = true),
                    )
                    // Registering the call means a pause or cancel aborts a blocked
                    // read immediately instead of waiting for the socket.
                    val work = CancellableWork { call.cancel() }
                    control.register(work)
                    try {
                        call.execute().use { response ->
                        if (response.code !in ACCEPTED_STATUS) {
                            throw RetryPolicy.HttpStatusException(response.code, "HTTP ${response.code}")
                        }
                        if (attempt == 1 && RetryPolicy.isHtmlInterstitial(response.headers["Content-Type"])) {
                            throw RetryPolicy.HtmlInterstitialException()
                        }
                        Log.d(
                            TAG,
                            "segment ${part.index}: HTTP ${response.code} " +
                                "contentLength=${response.body.contentLength()} " +
                                "contentRange=${response.headers["Content-Range"]}",
                        )

                        var skipBytes = 0L
                        var bytesRemaining: Long
                        var append: Boolean

                        if (response.code == 200) {
                            if (existingLength > 0L) {
                                // The server ignored the Range header: start over.
                                part.file.delete()
                                completedBytes.addAndGet(-existingLength)
                                rangeStart = part.start
                            }
                            skipBytes = rangeStart
                            bytesRemaining = part.size
                            append = false
                        } else {
                            bytesRemaining = part.end - rangeStart + 1
                            append = existingLength > 0L
                        }

                        activeSegments.incrementAndGet()
                        listener.onConnections(
                            activeSegments.get().toInt().coerceAtLeast(1),
                            effectiveSegments,
                            "",
                        )

                        try {
                            val source = response.body.source()
                            // Bound each read.  A stalled socket must never hold the
                            // transfer — and therefore a pause or cancel — hostage for
                            // the whole read timeout.
                            source.timeout().timeout(READ_IDLE_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS)
                            val buffer = ByteArray(READ_CHUNK)
                            FileOutputStream(part.file, append).use { fileOut ->
                                val buffered = BufferedOutputStream(fileOut, WRITE_BUFFER)
                                var stopped = false
                                var idleRounds = 0

                                try {
                                    while (true) {
                                        if (control.isCancelled) {
                                            stopped = true
                                            break
                                        }
                                        if (!control.awaitResumed()) {
                                            stopped = true
                                            break
                                        }

                                        val read = try {
                                            source.read(buffer, 0, buffer.size)
                                        } catch (idle: InterruptedIOException) {
                                            // No data for a while: not an error, just a
                                            // chance to re-check the control flags.
                                            if (control.stopRequested) {
                                                stopped = true
                                                break
                                            }
                                            idleRounds++
                                            if (idleRounds > MAX_IDLE_ROUNDS) throw idle
                                            continue
                                        }
                                        idleRounds = 0
                                        if (read <= 0) break

                                    var offset = 0
                                    var length = read

                                    if (skipBytes > 0L) {
                                        if (length <= skipBytes) {
                                            skipBytes -= length.toLong()
                                            continue
                                        }
                                        // skipBytes < length <= READ_CHUNK, so the cast is safe.
                                        offset = skipBytes.toInt()
                                        length -= offset
                                        skipBytes = 0L
                                    }
                                    if (length.toLong() > bytesRemaining) {
                                        length = bytesRemaining.toInt()
                                    }
                                    if (length <= 0) break

                                    throttle(request, length)

                                    // Pause barrier after the throttle sleep.
                                    if (!control.awaitResumed()) {
                                        stopped = true
                                        break
                                    }

                                    buffered.write(buffer, offset, length)
                                    bytesRemaining -= length
                                    firstByteSeen = true
                                    completedBytes.addAndGet(length.toLong())
                                    speed.add(length.toLong())

                                    if (bytesRemaining <= 0L) break
                                }
                                } finally {
                                    // Flush even when the call was aborted: bytes already
                                    // received must not be thrown away, or a resume would
                                    // re-download up to one buffer per segment.
                                    runCatching { buffered.flush() }
                                }

                                Log.d(
                                    TAG,
                                    "segment ${part.index}: loop ended stopped=$stopped " +
                                        "remaining=$bytesRemaining bytesOnDisk=${part.file.length()}",
                                )
                                if (stopped) return false
                            }
                        } finally {
                            activeSegments.decrementAndGet()
                        }
                    }
                    } finally {
                        control.unregister(work)
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failure: Throwable) {
                    // An aborted call is the pause/cancel taking effect, not an error.
                    if (control.stopRequested) {
                        Log.d(TAG, "segment ${part.index}: aborted by ${if (control.isCancelled) "cancel" else "pause"}")
                        return false
                    }

                    val status = RetryPolicy.statusOf(failure)
                    val retryable = (failure as? RetryPolicy.HttpStatusException)
                        ?.let { RetryPolicy.isRetryableStatus(it.code) }
                        ?: RetryPolicy.isRetryableException(failure)

                    optimizer.onServerError()

                    if (!retryable || attempt >= maxRetries ||
                        (!firstByteSeen && attempt >= startupAttempts)
                    ) {
                        Log.w(
                            TAG,
                            "segment ${part.index} failed after $attempt attempt(s): " +
                                N13Errors.friendly(failure, status),
                        )
                        return false
                    }

                    val backoff = RetryPolicy.delaySeconds(
                        attempt = attempt,
                        base = settings.retryDelay,
                        backoff = settings.retryBackoff,
                        jitter = settings.retryJitter,
                        maxDelay = settings.retryMaxDelay,
                        started = firstByteSeen,
                    )
                    if (!interruptibleSleep(backoff, control)) return false
                    continue
                }

                if (part.isComplete) {
                    part.done = true
                    return true
                }
            }
            return part.isComplete
        } finally {
            governor?.release()
            listener.onConnections(
                activeSegments.get().toInt(),
                effectiveSegments,
                "",
            )
        }
    }

    // ------------------------------------------------------------------ //
    // Merge and publish
    // ------------------------------------------------------------------ //

    /**
     * Writes the parts into the destination in order.
     *
     * Every part must exist and be complete before anything is written, so a
     * partial merge can never be published.  Returns an error message, or null on
     * success.
     */
    private suspend fun mergeParts(
        parts: List<DownloadPart>,
        destination: DownloadDestination,
        name: String,
        expectedSize: Long,
        digest: DigestTarget?,
    ): String? {
        for (part in parts) {
            if (!part.file.exists()) return "Missing segment file ${part.index}"
            if (part.file.length() < part.size) {
                return "Segment ${part.index} is incomplete " +
                    "(${part.file.length()}/${part.size} bytes)"
            }
        }

        var written = 0L
        return try {
            destination.abort(name)
            val output = BufferedOutputStream(destination.openOutput(name), WRITE_BUFFER)
            try {
                val buffer = ByteArray(MERGE_CHUNK)
                for (part in parts) {
                    part.file.inputStream().use { input ->
                        while (true) {
                            val read = input.read(buffer)
                            if (read <= 0) break
                            output.write(buffer, 0, read)
                            digest?.update(buffer, 0, read)
                            written += read
                        }
                    }
                }
                output.flush()
            } finally {
                runCatching { output.close() }
            }

            if (expectedSize > 0L && written != expectedSize) {
                destination.abort(name)
                return "Merged size mismatch: $written != $expectedSize bytes"
            }

            destination.finalize(name)
            null
        } catch (failure: Throwable) {
            runCatching { destination.abort(name) }
            N13Errors.friendly(failure)
        }
    }

    // ------------------------------------------------------------------ //
    // Helpers
    // ------------------------------------------------------------------ //

    private fun buildGetRequest(
        url: String,
        settings: DownloadSettings,
        rangeHeader: String?,
        segment: Boolean = false,
    ): Request {
        val headers = Headers.Builder().apply {
            BrowserHeaders.build(
                url = url,
                userAgent = settings.userAgent,
                rangeHeader = rangeHeader,
                accept = "*/*",
                segmentRequest = segment,
            ).forEach { (name, value) -> add(name, value) }
        }.build()
        return Request.Builder().url(url).headers(headers).build()
    }

    /** Applies the global and per-download caps to real bytes before they are written. */
    private suspend fun throttle(request: EngineRequest, byteCount: Int) {
        request.globalLimiter?.consume(byteCount)
        request.taskLimiter?.consume(byteCount)
    }

    /** Sleeps in slices so pause and cancel are honoured within ~250 ms. */
    private suspend fun interruptibleSleep(seconds: Double, control: EngineControl): Boolean {
        var remaining = seconds
        while (remaining > 0.0) {
            if (control.isCancelled) return false
            if (!control.awaitResumed()) return false
            val slice = minOf(remaining, 0.25)
            delay((slice * 1000).toLong().coerceAtLeast(1L))
            remaining -= slice
        }
        return !control.isCancelled
    }

    private companion object {
        const val TAG = "N13Engine"

        /** 64 KB reads keep pause/cancel responsive without hurting throughput. */
        const val READ_CHUNK = 64 * 1024
        const val WRITE_BUFFER = 256 * 1024
        const val MERGE_CHUNK = 1024 * 1024

        const val STARTUP_MAX_ATTEMPTS = 6
        const val MIN_PART_SIZE = 2L * 1024L * 1024L
        const val PROGRESS_INTERVAL_MILLIS = 250L

        /**
         * How long a single read may wait for data before the loop re-checks the
         * pause/cancel flags.  Two seconds keeps a pause responsive without
         * mistaking a slow server for a dead one.
         */
        const val READ_IDLE_TIMEOUT_MILLIS = 2_000L

        /** Consecutive idle rounds (≈40 s of silence) before the socket is declared dead. */
        const val MAX_IDLE_ROUNDS = 20

        /** How often the transfer loop checks whether to stop or whether it is done. */
        const val WATCH_INTERVAL_MILLIS = 100L

        val ACCEPTED_STATUS = setOf(200, 206)
    }
}

/**
 * Streams a digest while the file is written.
 *
 * Verification therefore costs no extra pass over the data, and it works for
 * MediaStore and SAF destinations where the finished file cannot be re-read by
 * path.
 */
internal class DigestTarget private constructor(
    private val expected: String,
    algorithm: String,
) {
    private val messageDigest: MessageDigest = MessageDigest.getInstance(algorithm)

    fun update(buffer: ByteArray, offset: Int, length: Int) {
        messageDigest.update(buffer, offset, length)
    }

    /** @return an error message on mismatch, null when the checksum matches. */
    fun verify(): String? {
        val actual = messageDigest.digest().joinToString("") { "%02x".format(it) }
        return if (actual.equals(expected, ignoreCase = true)) {
            null
        } else {
            N13Errors.CHECKSUM_MISMATCH
        }
    }

    companion object {
        /**
         * @param expected 32 hex characters selects MD5, 64 selects SHA-256,
         *   matching the Windows `detect_hash_algorithm`.
         */
        fun of(expected: String): DigestTarget? {
            val clean = expected.trim().lowercase()
            val algorithm = when {
                clean.length == 32 && clean.all { it.isDigit() || it in 'a'..'f' } -> "MD5"
                clean.length == 64 && clean.all { it.isDigit() || it in 'a'..'f' } -> "SHA-256"
                else -> return null
            }
            return DigestTarget(clean, algorithm)
        }
    }
}
