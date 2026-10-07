package com.sohayb.n13download.ui.screens.downloads

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * The N13 filter chips that make sense for the live queue.
 *
 * [labelRes] is resolved in the composable, not here: a ViewModel must not hold
 * translated text, or it would freeze the language it was created in.
 */
enum class QueueFilter(@param:StringRes val labelRes: Int) {
    ALL(R.string.filter_all),
    ACTIVE(R.string.filter_active),
    QUEUED(R.string.filter_queued),
    PAUSED(R.string.filter_paused),
}

/** Sort orders, matching the Windows sort dropdown where they apply. */
enum class SortOrder(@param:StringRes val labelRes: Int) {
    NEWEST(R.string.sort_newest),
    OLDEST(R.string.sort_oldest),
    QUEUE(R.string.sort_queue),
    NAME(R.string.sort_name),
    LARGEST(R.string.sort_largest),
    PROGRESS(R.string.sort_progress),
}

/**
 * The Downloads screen state.
 *
 * [allTasks] is the raw queue and [visible] is the filtered + sorted projection.
 * Both are computed once when the state is built rather than in a getter, because
 * a getter would re-sort the list on every recomposition — the list is redrawn
 * several times a second while a transfer runs, and re-sorting it each time was
 * pure wasted work.
 */
data class DownloadsUiState(
    val allTasks: List<DownloadTask> = emptyList(),
    val settings: DownloadSettings = DownloadSettings(),
    val queuePaused: Boolean = false,
    val filter: QueueFilter = QueueFilter.ALL,
    val sort: SortOrder = SortOrder.QUEUE,
    val failedCount: Int = 0,
    val activeCount: Int = 0,
) {
    /** The list the LazyColumn shows, already filtered and ordered. */
    val visible: List<DownloadTask> = project(allTasks, filter, sort)

    /** How many tasks a filter chip should advertise. */
    private val counts: Map<QueueFilter, Int> = QueueFilter.entries.associateWith { candidate ->
        allTasks.count { candidate.matches(it) }
    }

    fun countFor(filter: QueueFilter): Int = counts[filter] ?: 0

    val totalSpeed: Double = allTasks.sumOf { it.currentSpeed }

    private companion object {
        fun project(
            tasks: List<DownloadTask>,
            filter: QueueFilter,
            sort: SortOrder,
        ): List<DownloadTask> {
            val filtered = tasks.filter { filter.matches(it) }
            return when (sort) {
                SortOrder.NEWEST -> filtered.sortedByDescending { it.createdAt }
                SortOrder.OLDEST -> filtered.sortedBy { it.createdAt }
                SortOrder.QUEUE -> filtered.sortedWith(QUEUE_ORDER)
                SortOrder.NAME -> filtered.sortedBy { it.filename.lowercase() }
                SortOrder.LARGEST -> filtered.sortedByDescending { it.totalSize }
                SortOrder.PROGRESS -> filtered.sortedByDescending { it.percent }
            }
        }

        val QUEUE_ORDER: Comparator<DownloadTask> =
            compareBy<DownloadTask> { it.priority.value }.thenBy { it.createdAt }
    }
}

/** Whether a task belongs under a given filter chip. */
private fun QueueFilter.matches(task: DownloadTask): Boolean = when (this) {
    QueueFilter.ALL -> true
    QueueFilter.ACTIVE -> task.status.isRunning ||
        task.status == TaskStatus.MERGING || task.status == TaskStatus.VERIFYING

    QueueFilter.QUEUED -> task.status == TaskStatus.QUEUED
    QueueFilter.PAUSED -> task.status == TaskStatus.PAUSED
}

/** State holder for the Downloads screen. */
class DownloadsViewModel(
    private val manager: DownloadManager,
) : ViewModel() {

    private data class Snapshot(
        val queue: List<DownloadTask>,
        val failedCount: Int,
        val settings: DownloadSettings,
        val paused: Boolean,
    )

    private val filter = MutableStateFlow(QueueFilter.ALL)
    private val sort = MutableStateFlow(SortOrder.QUEUE)

    /**
     * Everything the screen shows, minus the filter/sort chips.
     *
     * `failedCount` is folded in as a lightweight integer rather than a second
     * list, so a subscriber downstream never re-sorts or re-renders because the
     * history contents changed but the count did not.
     */
    private val source = combine(
        manager.observeQueue(),
        manager.observeHistory().map { history -> history.count { it.status == TaskStatus.FAILED } },
        manager.observeSettings(),
        manager.observeQueuePaused(),
    ) { queue, failed, settings, paused ->
        Snapshot(queue, failed, settings, paused)
    }

    val uiState: StateFlow<DownloadsUiState> = combine(
        source,
        filter,
        sort,
    ) { snapshot, currentFilter, currentSort ->
        DownloadsUiState(
            allTasks = snapshot.queue,
            settings = snapshot.settings,
            queuePaused = snapshot.paused,
            filter = currentFilter,
            sort = currentSort,
            failedCount = snapshot.failedCount,
            activeCount = snapshot.queue.count { it.status.isActive },
        )
    }
        .distinctUntilChanged()
        .stateIn(
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

    fun setPriority(id: Long, priority: DownloadPriority) =
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
