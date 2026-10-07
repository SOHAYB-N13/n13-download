package com.sohayb.n13download.domain.download

import com.sohayb.n13download.core.BandwidthLimiter
import com.sohayb.n13download.core.N13Errors
import com.sohayb.n13download.core.UniqueNaming
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.domain.repository.DownloadRepository
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/** Edge-triggered lifecycle events, used to drive notifications. */
sealed interface TaskEvent {
    val taskId: Long
    val filename: String

    data class Started(override val taskId: Long, override val filename: String) : TaskEvent
    data class Completed(override val taskId: Long, override val filename: String) : TaskEvent
    data class Failed(
        override val taskId: Long,
        override val filename: String,
        val error: String,
    ) : TaskEvent

    data class Cancelled(override val taskId: Long, override val filename: String) : TaskEvent
}

/**
 * The download queue.
 *
 * Owns the scheduling rules the Windows product uses: a hard cap on
 * simultaneous downloads, ordering by priority then insertion order, a queue
 * gate that stops new work without touching in-flight transfers, and
 * pause/resume/cancel that operate on real coroutines.
 *
 * It lives for the whole process (owned by the application container), so
 * transfers keep running while the UI is gone — the foreground service only
 * exists to keep the process alive and show the notification.
 */
class DownloadQueue(
    private val repository: DownloadRepository,
    private val engine: DownloadEngine,
    private val settingsProvider: SettingsProvider,
    private val destinations: DestinationFactory,
    /** Root folder for `.part` files; always app-private and writable. */
    private val workingRoot: File,
    private val scope: CoroutineScope,
) {

    private val mutex = Mutex()
    private val jobs = mutableMapOf<Long, Job>()
    private val controls = mutableMapOf<Long, TaskControl>()
    private val taskLimiters = mutableMapOf<Long, BandwidthLimiter>()

    private val _activeCount = MutableStateFlow(0)
    val activeCount: StateFlow<Int> = _activeCount.asStateFlow()

    private val _queuePaused = MutableStateFlow(false)
    val queuePaused: StateFlow<Boolean> = _queuePaused.asStateFlow()

    private val _events = MutableSharedFlow<TaskEvent>(
        replay = 0,
        extraBufferCapacity = 32,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<TaskEvent> = _events.asSharedFlow()

    /** Global bandwidth limiter; its cap follows the settings. */
    private val globalLimiter = BandwidthLimiter(0)

    init {
        scope.launch {
            settingsProvider.settings.collect { settings ->
                globalLimiter.updateLimit(settings.maxSpeedBps)
                pump()
            }
        }
    }

    // ------------------------------------------------------------------ //
    // Public control surface
    // ------------------------------------------------------------------ //

    fun enqueue(taskId: Long) {
        scope.launch { pump() }
    }

    fun enqueueAllPending() {
        scope.launch { pump() }
    }

    suspend fun pause(taskId: Long) {
        val task = repository.get(taskId) ?: return
        val control = mutex.withLock { controls[taskId] }

        if (control != null) {
            // A live transfer: ask the engine to stop at the next chunk boundary.
            control.requestPause()
            // The job writes PAUSED once the engine returns. If the engine is
            // wedged, force the state after a grace period so the UI never lies.
            scope.launch {
                withTimeoutOrNull(PAUSE_GRACE_MILLIS) {
                    while (repository.get(taskId)?.status?.isRunning == true) delay(100)
                }
                val current = repository.get(taskId)
                if (current != null && current.status.isRunning) {
                    control.requestCancel()
                    runCatching { repository.updateStatus(taskId, TaskStatus.PAUSED) }
                }
            }
            return
        }

        if (task.status == TaskStatus.QUEUED) {
            repository.updateStatus(taskId, TaskStatus.PAUSED)
        }
    }

    suspend fun resume(taskId: Long) {
        val task = repository.get(taskId) ?: return
        val requeued = when (task.status) {
            TaskStatus.PAUSED -> task.forceStatus(TaskStatus.QUEUED)
            TaskStatus.FAILED, TaskStatus.CANCELLED -> task.forceStatus(TaskStatus.QUEUED)
            else -> task
        }
        repository.update(requeued.copy(autostart = true))
        pump()
    }

    suspend fun cancel(taskId: Long) {
        val control = mutex.withLock { controls[taskId] }
        if (control != null) {
            control.requestCancel()
            return
        }
        val task = repository.get(taskId) ?: return
        if (task.status != TaskStatus.CANCELLED && task.status != TaskStatus.REMOVED) {
            repository.updateStatus(taskId, TaskStatus.CANCELLED)
        }
        emit(TaskEvent.Cancelled(taskId, task.filename))
    }

    suspend fun retry(taskId: Long) {
        val task = repository.get(taskId) ?: return
        val restarting = task.status == TaskStatus.COMPLETED
        repository.update(
            task.copy(
                status = TaskStatus.QUEUED,
                error = "",
                completedAt = null,
                startedAt = null,
                currentSpeed = 0.0,
                averageSpeed = 0.0,
                etaSeconds = null,
                retryCount = task.retryCount + 1,
                autostart = true,
                downloadedSize = if (restarting) 0L else task.downloadedSize,
            ),
        )
        if (restarting) cleanupWorkingFiles(taskId)
        pump()
    }

    suspend fun retryAllFailed() {
        val candidates = repository.allNow()
            .filter { it.status == TaskStatus.FAILED || it.status == TaskStatus.CANCELLED }
        candidates.forEach { retry(it.id) }
    }

    suspend fun remove(taskId: Long, deleteFile: Boolean) {
        mutex.withLock { controls[taskId] }?.requestCancel()
        val task = repository.get(taskId)
        if (task != null) {
            if (deleteFile && task.resolvedPath.isNotBlank()) {
                // Deleting may hit MediaStore or a SAF provider; never on the caller.
                withContext(Dispatchers.IO) {
                    runCatching {
                        val destination = destinations.create(
                            settingsProvider.current().copy(
                                destinationKind = task.destinationKind,
                                destinationUri = task.destinationUri,
                            ),
                        )
                        destination.delete(task.filename)
                    }
                }
            }
            cleanupWorkingFiles(taskId)
            repository.delete(taskId)
        }
        pump()
    }

    suspend fun clearHistory() {
        repository.clearHistory()
    }

    suspend fun pauseAll() {
        _queuePaused.value = true
        repository.allNow()
            .filter { it.status == TaskStatus.QUEUED }
            .forEach { repository.updateStatus(it.id, TaskStatus.PAUSED) }
        updateActiveCount()
    }

    suspend fun resumeAll() {
        _queuePaused.value = false
        repository.allNow()
            .filter { it.status == TaskStatus.PAUSED }
            .forEach { repository.update(it.copy(status = TaskStatus.QUEUED, autostart = true)) }
        pump()
    }

    suspend fun setQueuePaused(paused: Boolean) {
        _queuePaused.value = paused
        if (!paused) pump()
    }

    suspend fun setPriority(taskId: Long, priority: DownloadPriority) {
        repository.get(taskId)?.let { repository.update(it.copy(priority = priority)) }
        pump()
    }

    suspend fun setSpeedLimit(taskId: Long, bytesPerSecond: Long) {
        repository.get(taskId)?.let { repository.update(it.copy(speedLimitBps = bytesPerSecond)) }
        mutex.withLock { taskLimiters[taskId] }?.updateLimit(bytesPerSecond)
    }

    // ------------------------------------------------------------------ //
    // Scheduling
    // ------------------------------------------------------------------ //

    /** Starts as many queued tasks as the slot budget allows. */
    suspend fun pump() {
        if (_queuePaused.value) {
            updateActiveCount()
            return
        }

        val settings = settingsProvider.current()
        val limit = settings.maxConcurrent.coerceAtLeast(1)

        // Read the candidate list outside the lock, straight from the database.
        // This must not be the shared UI stream: a one-shot read of that can be
        // answered from its replay buffer and miss the row the caller just
        // inserted, leaving a freshly added download queued at 0% forever.
        val pending = repository.allNow()
            .filter { it.status == TaskStatus.QUEUED && it.autostart }
            .sortedWith(QUEUE_ORDER)

        mutex.withLock {
            jobs.entries.removeAll { it.value.isCompleted }

            var slots = limit - jobs.size
            for (task in pending) {
                if (slots <= 0) break
                if (jobs.containsKey(task.id)) continue
                jobs[task.id] = startJob(task, settings)
                slots--
            }
        }
        updateActiveCount()
    }

    private fun startJob(task: DownloadTask, settings: DownloadSettings): Job = scope.launch {
        val control = TaskControl()
        mutex.withLock { controls[task.id] = control }
        try {
            // Everything below touches the network, the filesystem or a content
            // provider, so it must never run on the caller's dispatcher.
            withContext(Dispatchers.IO) { runTask(task, settings, control) }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: Throwable) {
            val message = N13Errors.friendly(failure)
            runCatching { repository.updateStatus(task.id, TaskStatus.FAILED, message) }
            emit(TaskEvent.Failed(task.id, task.filename, message))
        } finally {
            mutex.withLock {
                controls.remove(task.id)
                taskLimiters.remove(task.id)
            }
            updateActiveCount()
            // A finished task frees a slot; keep the queue moving.
            scope.launch { pump() }
        }
    }

    private suspend fun runTask(task: DownloadTask, settings: DownloadSettings, control: TaskControl) {
        val destination = destinations.create(
            settings.copy(
                destinationKind = task.destinationKind,
                destinationUri = task.destinationUri,
            ),
        )
        if (!destination.isAvailable()) {
            repository.updateStatus(task.id, TaskStatus.FAILED, N13Errors.DESTINATION_UNAVAILABLE)
            emit(TaskEvent.Failed(task.id, task.filename, N13Errors.DESTINATION_UNAVAILABLE))
            return
        }

        val targetName = resolveTargetName(task, destination, settings)
        if (targetName != task.filename) {
            repository.get(task.id)?.let { repository.update(it.copy(filename = targetName)) }
        }

        repository.updateStatus(task.id, TaskStatus.ANALYZING)

        val workingDir = File(workingRoot, task.id.toString()).apply { mkdirs() }

        val taskLimiter = if (task.speedLimitBps > 0L) {
            BandwidthLimiter(task.speedLimitBps).also { limiter ->
                mutex.withLock { taskLimiters[task.id] = limiter }
            }
        } else {
            null
        }

        val listener = QueueEngineListener(task.id, repository, scope)
        val current = repository.get(task.id) ?: task

        val request = EngineRequest(
            task = current,
            settings = settings,
            workingDirectory = workingDir,
            destination = destination,
            targetFilename = targetName,
            globalLimiter = globalLimiter.takeIf { it.enabled },
            taskLimiter = taskLimiter,
        )

        emit(TaskEvent.Started(task.id, targetName))

        val outcome = engine.execute(
            request = request,
            control = control,
            listener = listener,
        )

        // Let every progress/phase write the engine queued land before this method
        // writes the authoritative final state.  Without this, a throttled progress
        // tick or a late VERIFYING phase can arrive *after* the final status and
        // leave a finished download showing the wrong status or zero bytes.
        listener.drain()

        when (outcome) {
            is EngineOutcome.Completed -> {
                // Write the final counters synchronously before the status flips, so a
                // completed row can never claim zero bytes: progress reporting is
                // throttled, and a fast download may not have emitted its last tick.
                repository.updateProgress(
                    task.id,
                    outcome.totalBytes,
                    0.0,
                    outcome.averageSpeed,
                    0.0,
                )
                repository.updateStatus(task.id, TaskStatus.COMPLETED)
                cleanupWorkingFiles(task.id)
                emit(TaskEvent.Completed(task.id, targetName))
            }

            is EngineOutcome.Paused -> {
                repository.updateProgress(task.id, outcome.downloadedBytes, 0.0, 0.0, null)
                repository.updateStatus(task.id, TaskStatus.PAUSED)
            }

            is EngineOutcome.Cancelled -> {
                repository.updateProgress(task.id, outcome.downloadedBytes, 0.0, 0.0, null)
                repository.updateStatus(task.id, TaskStatus.CANCELLED)
                emit(TaskEvent.Cancelled(task.id, targetName))
            }

            is EngineOutcome.Failed -> {
                if (control.isPaused) {
                    repository.updateStatus(task.id, TaskStatus.PAUSED)
                } else {
                    repository.updateStatus(task.id, TaskStatus.FAILED, outcome.message)
                    emit(TaskEvent.Failed(task.id, targetName, outcome.message))
                }
            }
        }
    }

    /** Re-checks the destination right before transfer so nothing is clobbered. */
    private suspend fun resolveTargetName(
        task: DownloadTask,
        destination: DownloadDestination,
        settings: DownloadSettings,
    ): String {
        if (!destination.exists(task.filename)) return task.filename
        return when (settings.duplicatePolicy) {
            DuplicatePolicy.REPLACE -> {
                destination.delete(task.filename)
                task.filename
            }

            DuplicatePolicy.ALLOW -> task.filename
            DuplicatePolicy.ASK, DuplicatePolicy.RENAME ->
                UniqueNaming.unique(destination.listNames(), task.filename)
        }
    }

    private suspend fun cleanupWorkingFiles(taskId: Long) = withContext(Dispatchers.IO) {
        val directory = File(workingRoot, taskId.toString())
        if (directory.exists()) directory.deleteRecursively()
    }

    private suspend fun updateActiveCount() {
        val count = mutex.withLock { jobs.count { it.value.isActive } }
        _activeCount.value = count
    }

    private fun emit(event: TaskEvent) {
        _events.tryEmit(event)
    }

    private companion object {
        const val PAUSE_GRACE_MILLIS = 8_000L

        /** Lower priority value first, then oldest request first. */
        val QUEUE_ORDER = compareBy<DownloadTask> { it.priority.value }
            .thenBy { it.createdAt }
    }
}

/** Pause / cancel signal handed to the engine. */
class TaskControl : EngineControl {
    @Volatile
    private var paused = false

    @Volatile
    private var cancelled = false

    /**
     * In-flight work that must be aborted the moment a pause or cancel arrives.
     *
     * A blocking read can otherwise sit for the whole read timeout, which would
     * make "pause" take effect minutes later — or not at all if the server stalled.
     */
    private val inFlight = java.util.concurrent.CopyOnWriteArrayList<CancellableWork>()

    override val isCancelled: Boolean get() = cancelled
    override val isPaused: Boolean get() = paused

    override fun register(work: CancellableWork) {
        inFlight.add(work)
        // A request that arrives after the stop was already requested must not
        // start at all.
        if (paused || cancelled) work.cancel()
    }

    override fun unregister(work: CancellableWork) {
        inFlight.remove(work)
    }

    fun requestPause() {
        paused = true
        abortInFlight()
    }

    fun requestCancel() {
        cancelled = true
        paused = false
        abortInFlight()
    }

    private fun abortInFlight() {
        inFlight.forEach { runCatching { it.cancel() } }
    }

    override suspend fun awaitResumed(): Boolean {
        while (paused) {
            if (cancelled) return false
            delay(100)
        }
        return !cancelled
    }
}

/**
 * Bridges engine callbacks into the repository.
 *
 * Database writes are throttled to ~3 Hz.  The UI reads live speed and ETA from
 * the same rows, and one progress write per chunk would be exactly the
 * "excessive database writes" a Redmi 8 cannot afford.
 *
 * Writes are serialised through a mutex and the last one is tracked, so
 * [drain] can guarantee that every queued write has landed before the caller
 * writes the final state.
 */
private class QueueEngineListener(
    private val taskId: Long,
    private val repository: DownloadRepository,
    private val scope: CoroutineScope,
) : EngineListener {

    private val writeMutex = Mutex()
    private var lastWrite: Job? = null

    private var lastWriteMillis = 0L
    private var lastConnections = -1
    private var pendingSpeed = 0.0
    private var pendingAverage = 0.0
    private var pendingEta: Double? = null

    override fun onProgress(
        downloadedBytes: Long,
        totalBytes: Long,
        speed: Double,
        averageSpeed: Double,
        etaSeconds: Double?,
    ) {
        pendingSpeed = speed
        pendingAverage = averageSpeed
        pendingEta = etaSeconds
        val now = System.currentTimeMillis()
        if (now - lastWriteMillis < WRITE_INTERVAL_MILLIS) return
        lastWriteMillis = now
        write {
            repository.updateProgress(taskId, downloadedBytes, speed, averageSpeed, etaSeconds)
        }
    }

    override fun onPhase(status: TaskStatus) {
        write { repository.updateStatus(taskId, status) }
    }

    override fun onConnections(connections: Int, segmentCount: Int, smartStatus: String) {
        if (connections == lastConnections && smartStatus.isEmpty()) return
        lastConnections = connections
        write { repository.updateConnections(taskId, connections, segmentCount, smartStatus) }
    }

    override fun onSmartStatus(status: String) {
        write { repository.updateConnections(taskId, lastConnections.coerceAtLeast(1), 0, status) }
    }

    override fun onResolvedPath(path: String) {
        write {
            repository.get(taskId)?.let { repository.update(it.copy(resolvedPath = path)) }
        }
    }

    /** Suspends until every write submitted so far has completed. */
    suspend fun drain() {
        lastWrite?.join()
    }

    private fun write(block: suspend () -> Unit) {
        // Fire-and-forget so a progress write never blocks the transfer loop, but
        // serialised so writes land in the order they were produced.
        lastWrite = scope.launch(Dispatchers.IO) {
            writeMutex.withLock { runCatching { block() } }
        }
    }

    private companion object {
        const val WRITE_INTERVAL_MILLIS = 300L
    }
}
