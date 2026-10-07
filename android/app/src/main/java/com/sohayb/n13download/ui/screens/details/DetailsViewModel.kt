package com.sohayb.n13download.ui.screens.details

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadTask
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** State holder for the download Properties screen. */
class DetailsViewModel(
    private val manager: DownloadManager,
    private val taskId: Long,
) : ViewModel() {

    val task: StateFlow<DownloadTask?> = manager.observeTask(taskId)
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
            initialValue = null,
        )

    val destinationLabel: StateFlow<String> = kotlinx.coroutines.flow.flow {
        emit(manager.task(taskId)?.let { manager.destinationLabelFor(it) }.orEmpty())
    }.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS),
        initialValue = "",
    )

    fun pause() = viewModelScope.launch { manager.pause(taskId) }
    fun resume() = viewModelScope.launch { manager.resume(taskId) }
    fun cancel() = viewModelScope.launch { manager.cancel(taskId) }
    fun retry() = viewModelScope.launch { manager.retry(taskId) }
    fun remove(deleteFile: Boolean) = viewModelScope.launch { manager.remove(taskId, deleteFile) }

    fun setPriority(priority: DownloadPriority) = viewModelScope.launch {
        manager.setPriority(taskId, priority)
    }

    fun setSpeedLimit(bytesPerSecond: Long) = viewModelScope.launch {
        manager.setSpeedLimit(taskId, bytesPerSecond)
    }

    fun setChecksum(checksum: String) = viewModelScope.launch {
        manager.updateChecksum(taskId, checksum)
    }

    companion object {
        private const val STOP_TIMEOUT_MILLIS = 5_000L

        fun factory(manager: DownloadManager, taskId: Long): ViewModelProvider.Factory =
            viewModelFactory {
                initializer { DetailsViewModel(manager, taskId) }
            }
    }
}
