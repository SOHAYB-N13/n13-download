package com.sohayb.n13download.ui.screens.downloads

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The N13 filter chips that make sense for the live queue. */
enum class QueueFilter(val label: String) {
    ALL("All"),
    ACTIVE("Active"),
    QUEUED("Queued"),
    PAUSED("Paused"),
}

/** Sort orders, matching the Windows sort dropdown where they apply. */
enum class SortOrder(val label: String) {
    NEWEST("Newest first"),
    OLDEST("Oldest first"),
    QUEUE("Queue order"),
    NAME("Name A-Z"),
    LARGEST("Largest first"),
    PROGRESS("Most complete"),
}

data class DownloadsUiState(
    val tasks: List<DownloadTask> = emptyList(),
    val settings: DownloadSettings = DownloadSettings(),
    val queuePaused: Boolean = false,
    val filter: QueueFilter = QueueFilter.ALL,
    val sort: SortOrder = SortOrder.QUEUE,
    val failedCount: Int = 0,
    val activeCount: Int = 0,
) {
    val visible: List<DownloadTask>
        get() {
            val filtered = tasks.filter { task ->
                when (filter) {
                    QueueFilter.ALL -> true
                    QueueFilter.ACTIVE -> task.status.isRunning ||
                        task.status == TaskStatus.MERGING || task.status == TaskStatus.VERIFYING

                    QueueFilter.QUEUED -> task.status == TaskStatus.QUEUED
                    QueueFilter.PAUSED -> task.status == TaskStatus.PAUSED
                }
            }
            return when (sort) {
                SortOrder.NEWEST -> filtered.sortedByDescending { it.createdAt }
                SortOrder.OLDEST -> filtered.sortedBy { it.createdAt }
                SortOrder.QUEUE -> filtered.sortedWith(
                    compareBy<DownloadTask> { it.priority.value }.thenBy { it.createdAt },
                )

                SortOrder.NAME -> filtered.sortedBy { it.filename.lowercase() }
                SortOrder.LARGEST -> filtered.sortedByDescending { it.totalSize }
                SortOrder.PROGRESS -> filtered.sortedByDescending { it.percent }
            }
        }

    fun countFor(filter: QueueFilter): Int = tasks.count { task ->
        when (filter) {
            QueueFilter.ALL -> true
            QueueFilter.ACTIVE -> task.status.isRunning ||
                task.status == TaskStatus.MERGING || task.status == TaskStatus.VERIFYING

            QueueFilter.QUEUED -> task.status == TaskStatus.QUEUED
            QueueFilter.PAUSED -> task.status == TaskStatus.PAUSED
        }
    }

    val totalSpeed: Double get() = tasks.sumOf { it.currentSpeed }
}

/** State holder for the Downloads screen. */
class DownloadsViewModel(
    private val manager: DownloadManager,
) : ViewModel() {

    private data class Snapshot(
        val queue: List<DownloadTask>,
        val history: List<DownloadTask>,
        val settings: DownloadSettings,
        val paused: Boolean,
    )

    private val filter = MutableStateFlow(QueueFilter.ALL)
    private val sort = MutableStateFlow(SortOrder.QUEUE)

    /** Repository snapshot, combined first so the outer combine stays type-safe. */
    private val source = combine(
        manager.observeQueue(),
        manager.observeHistory(),
        manager.observeSettings(),
        manager.observeQueuePaused(),
    ) { queue, history, settings, paused ->
        Snapshot(queue, history, settings, paused)
    }

    val uiState: StateFlow<DownloadsUiState> = combine(
        source,
        filter,
        sort,
    ) { snapshot, currentFilter, currentSort ->
        DownloadsUiState(
            tasks = snapshot.queue,
            settings = snapshot.settings,
            queuePaused = snapshot.paused,
            filter = currentFilter,
            sort = currentSort,
            failedCount = snapshot.history.count { it.status == TaskStatus.FAILED },
            activeCount = snapshot.queue.count { it.status.isActive },
        )
    }.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
        initialValue = DownloadsUiState(),
    )

    fun setFilter(value: QueueFilter) {
        filter.value = value
    }

    fun setSort(value: SortOrder) {
        sort.value = value
    }

    fun pause(id: Long) = viewModelScope.launch { manager.pause(id) }
    fun resume(id: Long) = viewModelScope.launch { manager.resume(id) }
    fun cancel(id: Long) = viewModelScope.launch { manager.cancel(id) }
    fun retry(id: Long) = viewModelScope.launch { manager.retry(id) }
    fun remove(id: Long, deleteFile: Boolean) = viewModelScope.launch { manager.remove(id, deleteFile) }
    fun pauseAll() = viewModelScope.launch { manager.pauseAll() }
    fun resumeAll() = viewModelScope.launch { manager.resumeAll() }
    fun retryFailed() = viewModelScope.launch { manager.retryAllFailed() }
    fun clearFinished() = viewModelScope.launch { manager.clearHistory() }

    fun setPriority(id: Long, priority: com.sohayb.n13download.domain.model.DownloadPriority) =
        viewModelScope.launch { manager.setPriority(id, priority) }

    fun setSpeedLimit(id: Long, bytesPerSecond: Long) =
        viewModelScope.launch { manager.setSpeedLimit(id, bytesPerSecond) }

    fun rename(id: Long, newName: String) = viewModelScope.launch { manager.rename(id, newName) }

    companion object {
        private const val STOP_TIMEOUT_MILLIS = 5_000L

        fun factory(manager: DownloadManager): ViewModelProvider.Factory = viewModelFactory {
            initializer { DownloadsViewModel(manager) }
        }
    }
}
