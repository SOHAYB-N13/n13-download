package com.sohayb.n13download.ui.screens.history

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** History filters, using the N13 `history.*` labels. */
enum class HistoryFilter(val label: String) {
    ALL("All"),
    COMPLETED("Completed"),
    FAILED("Failed"),
    CANCELLED("Cancelled"),
}

data class HistoryUiState(
    val entries: List<DownloadTask> = emptyList(),
    val filter: HistoryFilter = HistoryFilter.ALL,
) {
    val visible: List<DownloadTask>
        get() = entries.filter { task ->
            when (filter) {
                HistoryFilter.ALL -> true
                HistoryFilter.COMPLETED -> task.status == TaskStatus.COMPLETED
                HistoryFilter.FAILED -> task.status == TaskStatus.FAILED
                HistoryFilter.CANCELLED -> task.status == TaskStatus.CANCELLED
            }
        }

    fun countFor(filter: HistoryFilter): Int = entries.count { task ->
        when (filter) {
            HistoryFilter.ALL -> true
            HistoryFilter.COMPLETED -> task.status == TaskStatus.COMPLETED
            HistoryFilter.FAILED -> task.status == TaskStatus.FAILED
            HistoryFilter.CANCELLED -> task.status == TaskStatus.CANCELLED
        }
    }

    // --- Analytics block, mirroring the Windows history summary -------------
    val totalBytes: Long get() = entries.filter { it.status == TaskStatus.COMPLETED }.sumOf { it.downloadedSize }
    val completedCount: Int get() = entries.count { it.status == TaskStatus.COMPLETED }
    val failedCount: Int get() = entries.count { it.status == TaskStatus.FAILED }
    val cancelledCount: Int get() = entries.count { it.status == TaskStatus.CANCELLED }

    val averageSpeed: Double
        get() {
            val finished = entries.filter { it.status == TaskStatus.COMPLETED && it.elapsedSeconds > 0 }
            if (finished.isEmpty()) return 0.0
            return finished.sumOf { it.downloadedSize / it.elapsedSeconds } / finished.size
        }

    val peakSpeed: Double get() = entries.maxOfOrNull { it.averageSpeed } ?: 0.0

    val totalTimeSeconds: Double get() = entries.sumOf { it.elapsedSeconds }
}

/** State holder for the History screen. */
class HistoryViewModel(
    private val manager: DownloadManager,
) : ViewModel() {

    private val filter = MutableStateFlow(HistoryFilter.ALL)

    val uiState: StateFlow<HistoryUiState> = combine(
        manager.observeHistory(),
        filter,
    ) { entries, currentFilter ->
        HistoryUiState(entries = entries, filter = currentFilter)
    }.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
        initialValue = HistoryUiState(),
    )

    fun setFilter(value: HistoryFilter) {
        filter.value = value
    }

    fun retry(id: Long) = viewModelScope.launch { manager.retry(id) }
    fun remove(id: Long, deleteFile: Boolean) = viewModelScope.launch { manager.remove(id, deleteFile) }
    fun clearHistory() = viewModelScope.launch { manager.clearHistory() }

    companion object {
        private const val STOP_TIMEOUT_MILLIS = 5_000L

        fun factory(manager: DownloadManager): ViewModelProvider.Factory = viewModelFactory {
            initializer { HistoryViewModel(manager) }
        }
    }
}
