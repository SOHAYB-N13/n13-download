package com.sohayb.n13download.domain.download

import com.sohayb.n13download.core.CategoryDetector
import com.sohayb.n13download.core.FilenameResolver
import com.sohayb.n13download.core.N13Errors
import com.sohayb.n13download.core.UniqueNaming
import com.sohayb.n13download.core.UrlSecurity
import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.domain.repository.DownloadRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

/**
 * Builds the destination for the current settings.
 *
 * Implemented in the data layer; the domain only needs the abstraction so
 * duplicate detection and publishing can be expressed without knowing whether
 * the target is a plain file, MediaStore or a SAF document tree.
 */
interface DestinationFactory {
    suspend fun create(settings: DownloadSettings): DownloadDestination
}

/** Where settings come from. */
interface SettingsProvider {
    val settings: Flow<DownloadSettings>
    suspend fun current(): DownloadSettings
    suspend fun update(settings: DownloadSettings)
}

/**
 * The single entry point the UI talks to.
 *
 * It owns the flow *validate link -> inspect server -> resolve name -> record
 * task -> hand to the queue*.  Persistence stays in the repository, byte
 * transfer stays in the engine, and scheduling stays in [DownloadQueue], so the
 * UI never has to know about any of them.
 */
class DownloadManager(
    private val repository: DownloadRepository,
    private val engine: DownloadEngine,
    private val queue: DownloadQueue,
    private val settingsProvider: SettingsProvider,
    private val destinations: DestinationFactory,
) {

    fun observeAll() = repository.observeAll()
    fun observeQueue() = repository.observeQueue()
    fun observeHistory() = repository.observeHistory()
    fun observeTask(id: Long) = repository.observeTask(id)
    fun observeSettings() = settingsProvider.settings

    suspend fun task(id: Long): DownloadTask? = repository.get(id)
    suspend fun settings(): DownloadSettings = settingsProvider.current()

    /** Validates a pasted link without touching the network. */
    fun validate(rawUrl: String): UrlCheck {
        val normalized = FilenameResolver.normalizeUrl(rawUrl)
        if (normalized.isEmpty()) return UrlCheck.Invalid(N13Errors.INVALID_URL)
        if (!FilenameResolver.isValidHttpUrl(normalized)) return UrlCheck.Invalid(N13Errors.INVALID_URL)
        return UrlCheck.Valid(normalized)
    }

    /**
     * Inspects a link so the Add Download dialog can show what it found.
     *
     * Runs on [Dispatchers.IO]: the SSRF guard resolves the host, which is a
     * blocking DNS lookup and must never happen on the main thread.
     */
    suspend fun inspect(url: String): DownloadAnalysis = withContext(Dispatchers.IO) {
        val settings = settingsProvider.current()
        val check = UrlSecurity.validate(url, settings.blockPrivateUrls)
        if (!check.ok) {
            return@withContext DownloadAnalysis(ok = false, url = url, error = check.reason)
        }
        engine.probe(url, settings)
    }

    /**
     * Records a download and hands it to the queue.
     *
     * Returns [AddDownloadOutcome.Duplicate] when the policy is "ask" and
     * something is in the way; the caller then re-submits with a
     * [DuplicateChoice] once the user has decided.
     *
     * Runs on [Dispatchers.IO]: the SSRF guard resolves the host and the
     * destination is inspected on disk.
     */
    suspend fun addDownload(request: AddDownloadRequest): AddDownloadOutcome =
        withContext(Dispatchers.IO) {
            addDownloadInternal(request)
        }

    private suspend fun addDownloadInternal(request: AddDownloadRequest): AddDownloadOutcome {
        val settings = settingsProvider.current()

        val security = UrlSecurity.validate(request.url, settings.blockPrivateUrls)
        if (!security.ok) return AddDownloadOutcome.Rejected(security.reason)

        val analysis = request.analysis ?: inspect(request.url)
        if (!analysis.ok) {
            return AddDownloadOutcome.Rejected(
                analysis.error.ifBlank { "Could not inspect this link" },
            )
        }

        val requestedName = request.filename?.takeIf { it.isNotBlank() }
            ?: analysis.filename.takeIf { it.isNotBlank() }
            ?: FilenameResolver.FALLBACK
        val filename = FilenameResolver.sanitize(requestedName)
        if (!FilenameResolver.isSafeName(filename)) {
            return AddDownloadOutcome.Rejected("That file name is not valid")
        }

        val category = request.category
            ?: if (settings.autoCategorize) {
                CategoryDetector.detect(filename, analysis.contentType)
            } else {
                "General"
            }

        val destination = destinations.create(settings)
        if (!destination.isAvailable()) {
            return AddDownloadOutcome.Rejected(N13Errors.DESTINATION_UNAVAILABLE)
        }

        // ---- Duplicate handling -------------------------------------------
        val activeWithSameUrl = repository.findByUrl(request.url)
            ?.takeIf { !it.status.isTerminal }

        val nameExists = destination.exists(filename)

        val choice = request.duplicateChoice ?: when {
            activeWithSameUrl != null -> settings.duplicatePolicy.autoChoice()
                ?: return AddDownloadOutcome.Duplicate(
                    existingTaskId = activeWithSameUrl.id,
                    filename = activeWithSameUrl.filename,
                    kind = DuplicateKind.SAME_URL_ACTIVE,
                )

            nameExists -> settings.duplicatePolicy.autoChoice()
                ?: return AddDownloadOutcome.Duplicate(
                    existingTaskId = repository.findByDestination(filename, destination.displayPath)?.id,
                    filename = filename,
                    kind = DuplicateKind.FILE_EXISTS,
                )

            else -> DuplicateChoice.DOWNLOAD_ANYWAY
        }

        if (choice == DuplicateChoice.OPEN_EXISTING) {
            return AddDownloadOutcome.OpenedExisting(
                activeWithSameUrl?.id
                    ?: repository.findByDestination(filename, destination.displayPath)?.id
                    ?: 0L,
            )
        }

        var finalName = filename
        when (choice) {
            DuplicateChoice.RENAME -> finalName = uniqueName(destination, filename)
            DuplicateChoice.REPLACE -> destination.delete(filename)
            DuplicateChoice.DOWNLOAD_ANYWAY, DuplicateChoice.OPEN_EXISTING -> Unit
        }

        val task = DownloadTask(
            url = analysis.finalUrl.takeIf { it.isNotBlank() } ?: request.url,
            filename = finalName,
            directory = destination.displayPath,
            label = request.label,
            category = category,
            totalSize = analysis.totalSize,
            status = TaskStatus.QUEUED,
            priority = request.priority,
            createdAt = System.currentTimeMillis(),
            checksum = request.checksum.trim(),
            contentType = analysis.contentType,
            server = analysis.server,
            supportsRange = analysis.supportsRange,
            etag = analysis.etag,
            lastModified = analysis.lastModified,
            autostart = request.startNow,
            speedLimitBps = request.speedLimitBps,
            destinationKind = settings.destinationKind,
            destinationUri = settings.destinationUri,
        )

        val id = repository.insert(task)
        queue.enqueue(id)
        return AddDownloadOutcome.Queued(id, finalName)
    }

    /** `report.pdf` -> `report (1).pdf`, never overwriting an existing file. */
    suspend fun uniqueName(destination: DownloadDestination, filename: String): String =
        UniqueNaming.unique(destination.listNames(), filename)

    // --- Task control -------------------------------------------------------

    suspend fun pause(id: Long) = queue.pause(id)
    suspend fun resume(id: Long) = queue.resume(id)
    suspend fun cancel(id: Long) = queue.cancel(id)
    suspend fun retry(id: Long) = queue.retry(id)
    suspend fun remove(id: Long, deleteFile: Boolean = false) = queue.remove(id, deleteFile)
    suspend fun clearHistory() = queue.clearHistory()

    suspend fun pauseAll() = queue.pauseAll()
    suspend fun resumeAll() = queue.resumeAll()
    suspend fun retryAllFailed() = queue.retryAllFailed()

    suspend fun setPriority(id: Long, priority: DownloadPriority) = queue.setPriority(id, priority)
    suspend fun setSpeedLimit(id: Long, bytesPerSecond: Long) = queue.setSpeedLimit(id, bytesPerSecond)

    /** Pauses or resumes the queue gate without touching in-flight transfers. */
    suspend fun setQueuePaused(paused: Boolean) = queue.setQueuePaused(paused)
    fun observeQueuePaused() = queue.queuePaused

    suspend fun rename(id: Long, newName: String): RenameResult {
        val task = repository.get(id) ?: return RenameResult.Failed("That download no longer exists")
        if (task.status.isRunning || task.status == TaskStatus.MERGING || task.status == TaskStatus.VERIFYING) {
            return RenameResult.Failed("Pause or cancel the download first")
        }
        val clean = FilenameResolver.sanitize(newName.trim())
        if (!FilenameResolver.isSafeName(clean)) return RenameResult.Failed("That name is not valid")
        repository.update(task.copy(filename = clean))
        return RenameResult.Renamed(clean)
    }

    suspend fun updateChecksum(id: Long, checksum: String) {
        repository.get(id)?.let { repository.update(it.copy(checksum = checksum.trim())) }
    }

    // --- File actions -------------------------------------------------------

    /**
     * A URI other apps can open or share.
     *
     * Rebuilt from the task's own destination rather than from the current
     * settings, so a download finished yesterday still opens correctly after the
     * user changes the download folder today.  Runs on [Dispatchers.IO] because
     * resolving it can hit MediaStore or the SAF provider.
     */
    suspend fun contentUriFor(task: DownloadTask): android.net.Uri? = withContext(Dispatchers.IO) {
        val destination = destinationFor(task)
        runCatching { destination.contentUri(task.filename) }.getOrNull()
    }

    suspend fun filePathFor(task: DownloadTask): String? = withContext(Dispatchers.IO) {
        destinationFor(task).filePath(task.filename)
    }

    /** Deletes the finished file from its destination. Never touches the task row. */
    suspend fun deleteFile(task: DownloadTask): Boolean = withContext(Dispatchers.IO) {
        runCatching { destinationFor(task).delete(task.filename) }.getOrDefault(false)
    }

    suspend fun destinationLabelFor(task: DownloadTask): String = withContext(Dispatchers.IO) {
        destinationFor(task).displayPath
    }

    private suspend fun destinationFor(task: DownloadTask): DownloadDestination {
        val settings = settingsProvider.current().copy(
            destinationKind = task.destinationKind,
            destinationUri = task.destinationUri,
        )
        return destinations.create(settings)
    }

    suspend fun updateSettings(settings: DownloadSettings) = settingsProvider.update(settings)

    /**
     * Recovers tasks that were mid-transfer when the process died.
     *
     * Always fixes the stored state.  Whether the recovered work actually restarts
     * follows the `resumeOnStartup` setting, matching the Windows product: with it
     * off, restored downloads wait in the queue until the user resumes them.
     */
    suspend fun recoverAfterRestart(): Int {
        val recovered = repository.recoverInterrupted()

        if (!settingsProvider.current().resumeOnStartup) {
            // Park recovered work so the queue does not restart it behind the
            // user's back. A task with bytes on disk or a start time is a
            // restored transfer, not something the user just queued.
            repository.observeAll().first()
                .filter { it.status == TaskStatus.QUEUED && it.autostart }
                .filter { it.downloadedSize > 0L || it.startedAt != null }
                .forEach { repository.update(it.copy(autostart = false)) }
        }

        queue.enqueueAllPending()
        return recovered
    }

    fun shutdown() = engine.shutdown()

    private fun DuplicatePolicy.autoChoice(): DuplicateChoice? = when (this) {
        DuplicatePolicy.ASK -> null
        DuplicatePolicy.ALLOW -> DuplicateChoice.DOWNLOAD_ANYWAY
        DuplicatePolicy.RENAME -> DuplicateChoice.RENAME
        DuplicatePolicy.REPLACE -> DuplicateChoice.REPLACE
    }
}

sealed interface UrlCheck {
    data class Valid(val url: String) : UrlCheck
    data class Invalid(val reason: String) : UrlCheck
}

/** A request from the Add Download dialog. */
data class AddDownloadRequest(
    val url: String,
    val filename: String? = null,
    val category: String? = null,
    val label: String = "",
    val priority: DownloadPriority = DownloadPriority.DEFAULT,
    val checksum: String = "",
    val startNow: Boolean = true,
    val speedLimitBps: Long = 0L,
    val analysis: DownloadAnalysis? = null,
    val duplicateChoice: DuplicateChoice? = null,
)

enum class DuplicateChoice {
    DOWNLOAD_ANYWAY,
    RENAME,
    REPLACE,
    OPEN_EXISTING,
}

enum class DuplicateKind {
    /** The same URL is already transferring. */
    SAME_URL_ACTIVE,

    /** A file with that name is already in the destination. */
    FILE_EXISTS,
}

sealed interface AddDownloadOutcome {
    data class Queued(val taskId: Long, val filename: String) : AddDownloadOutcome
    data class Rejected(val reason: String) : AddDownloadOutcome
    data class Duplicate(
        val existingTaskId: Long?,
        val filename: String,
        val kind: DuplicateKind,
    ) : AddDownloadOutcome

    data class OpenedExisting(val taskId: Long) : AddDownloadOutcome
}

sealed interface RenameResult {
    data class Renamed(val filename: String) : RenameResult
    data class Failed(val reason: String) : RenameResult
}
