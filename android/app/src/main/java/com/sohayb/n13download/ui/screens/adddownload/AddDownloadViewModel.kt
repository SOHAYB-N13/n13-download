package com.sohayb.n13download.ui.screens.adddownload

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.core.CategoryDetector
import com.sohayb.n13download.domain.download.AddDownloadOutcome
import com.sohayb.n13download.domain.download.AddDownloadRequest
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DuplicateChoice
import com.sohayb.n13download.domain.download.DuplicateKind
import com.sohayb.n13download.domain.download.UrlCheck
import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.domain.model.DownloadSettings
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** A duplicate the user has to decide about. */
data class ConflictPrompt(
    val kind: DuplicateKind,
    val filename: String,
    val existingTaskId: Long?,
)

data class AddDownloadUiState(
    val url: String = "",
    val filename: String = "",
    val filenameEdited: Boolean = false,
    val category: String? = null,
    val checksum: String = "",
    val startNow: Boolean = true,
    val advancedExpanded: Boolean = false,
    val probing: Boolean = false,
    val analysis: DownloadAnalysis? = null,
    val error: String? = null,
    val conflict: ConflictPrompt? = null,
    val settings: DownloadSettings = DownloadSettings(),
    val adding: Boolean = false,
    val done: Boolean = false,
) {
    val canSubmit: Boolean get() = url.isNotBlank() && !adding && conflict == null
}

/**
 * State holder for the Add Download flow.
 *
 * Mirrors the Windows behaviour exactly: the link is debounced, then probed, and
 * the result is shown as a detection card the user confirms before anything is
 * queued.  No download starts from this screen without an explicit confirmation.
 */
@OptIn(FlowPreview::class)
class AddDownloadViewModel(
    private val manager: DownloadManager,
    prefillUrl: String?,
) : ViewModel() {

    private val _state = MutableStateFlow(
        AddDownloadUiState(url = prefillUrl.orEmpty()),
    )
    val state: StateFlow<AddDownloadUiState> = _state.asStateFlow()

    private val urlChanges = MutableStateFlow(prefillUrl.orEmpty())

    @OptIn(FlowPreview::class)
    private fun startUrlWatcher() {
        viewModelScope.launch {
            urlChanges
                .debounce(PROBE_DEBOUNCE_MILLIS)
                .distinctUntilChanged()
                .collectLatest { raw -> probe(raw) }
        }
    }

    init {
        viewModelScope.launch {
            val settings = manager.settings()
            _state.update { it.copy(settings = settings) }
        }
        startUrlWatcher()
    }

    fun onUrlChange(value: String) {
        _state.update { it.copy(url = value, error = null) }
        urlChanges.value = value
    }

    fun onFilenameChange(value: String) {
        _state.update { it.copy(filename = value, filenameEdited = true) }
    }

    fun onCategoryChange(category: String?) {
        _state.update { it.copy(category = category) }
    }

    fun onChecksumChange(value: String) {
        _state.update { it.copy(checksum = value) }
    }

    fun onStartNowChange(value: Boolean) {
        _state.update { it.copy(startNow = value) }
    }

    fun toggleAdvanced() {
        _state.update { it.copy(advancedExpanded = !it.advancedExpanded) }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    fun dismissConflict() {
        _state.update { it.copy(conflict = null) }
    }

    /** Validates and inspects the link; a failure is shown inline, never queued. */
    private suspend fun probe(raw: String) {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) {
            _state.update { it.copy(analysis = null, probing = false, error = null) }
            return
        }

        when (val check = manager.validate(trimmed)) {
            is UrlCheck.Invalid -> {
                _state.update { it.copy(analysis = null, probing = false, error = check.reason) }
                return
            }

            is UrlCheck.Valid -> Unit
        }

        _state.update { it.copy(probing = true, error = null) }
        val analysis = manager.inspect(trimmed)

        _state.update { current ->
            // Discard a stale result: the user may have typed on while this ran.
            if (current.url.trim() != trimmed) return@update current

            current.copy(
                probing = false,
                analysis = analysis,
                error = analysis.error.takeIf { !analysis.ok },
                filename = if (current.filenameEdited) current.filename else analysis.filename,
                category = current.category ?: analysis.takeIf { it.ok }?.let {
                    CategoryDetector.detect(it.filename, it.contentType)
                },
            )
        }
    }

    /** Queues the download, or surfaces the conflict the user has to resolve. */
    fun submit(duplicateChoice: DuplicateChoice? = null) {
        val snapshot = _state.value
        if (snapshot.url.isBlank() || snapshot.adding) return

        viewModelScope.launch {
            _state.update { it.copy(adding = true, error = null) }

            val outcome = manager.addDownload(
                AddDownloadRequest(
                    url = snapshot.url.trim(),
                    filename = snapshot.filename.takeIf { it.isNotBlank() },
                    category = snapshot.category,
                    checksum = snapshot.checksum,
                    startNow = snapshot.startNow,
                    analysis = snapshot.analysis,
                    duplicateChoice = duplicateChoice,
                ),
            )

            when (outcome) {
                is AddDownloadOutcome.Queued -> {
                    _state.update { it.copy(adding = false, done = true, conflict = null) }
                }

                is AddDownloadOutcome.Rejected -> {
                    _state.update { it.copy(adding = false, error = outcome.reason) }
                }

                is AddDownloadOutcome.Duplicate -> {
                    _state.update {
                        it.copy(
                            adding = false,
                            conflict = ConflictPrompt(
                                kind = outcome.kind,
                                filename = outcome.filename,
                                existingTaskId = outcome.existingTaskId,
                            ),
                        )
                    }
                }

                is AddDownloadOutcome.OpenedExisting -> {
                    _state.update { it.copy(adding = false, done = true, conflict = null) }
                }
            }
        }
    }

    companion object {
        private const val PROBE_DEBOUNCE_MILLIS = 550L

        fun factory(manager: DownloadManager, prefillUrl: String?): ViewModelProvider.Factory =
            viewModelFactory {
                initializer { AddDownloadViewModel(manager, prefillUrl) }
            }
    }
}
