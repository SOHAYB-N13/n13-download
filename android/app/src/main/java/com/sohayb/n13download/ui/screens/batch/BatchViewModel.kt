package com.sohayb.n13download.ui.screens.batch

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sohayb.n13download.domain.batch.BatchAddResult
import com.sohayb.n13download.domain.batch.BatchImport
import com.sohayb.n13download.domain.batch.BatchItem
import com.sohayb.n13download.domain.batch.BatchParseResult
import com.sohayb.n13download.domain.batch.BatchPattern
import com.sohayb.n13download.domain.batch.BatchPatternCheck
import com.sohayb.n13download.domain.batch.BatchPatternMode
import com.sohayb.n13download.domain.batch.BatchPatternProblem
import com.sohayb.n13download.domain.batch.BatchPatternSpec
import com.sohayb.n13download.domain.batch.BatchProbe
import com.sohayb.n13download.domain.batch.BatchProbeOutcome
import com.sohayb.n13download.domain.batch.BatchScanProgress
import com.sohayb.n13download.domain.batch.BatchScanResult
import com.sohayb.n13download.domain.batch.BatchScanner
import com.sohayb.n13download.domain.batch.BatchUrls
import com.sohayb.n13download.domain.download.DownloadManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** The two batch sources, matching the Windows Batch page's tabs. */
enum class BatchMode { URL_LIST, PATTERN }

/** Something the screen has to tell the user exactly once. */
sealed interface BatchEvent {
    data class Queued(val result: BatchAddResult) : BatchEvent

    /** Nothing could be queued; the reason is on the result. */
    data class NothingQueued(val result: BatchAddResult) : BatchEvent

    /** The pattern is not valid yet. */
    data class PatternInvalid(val problem: BatchPatternProblem) : BatchEvent

    /** The auto scan has not been run, so there is nothing to queue. */
    data object ScanFirst : BatchEvent

    /** The picked file could not be read. */
    data object ImportFailed : BatchEvent
}

data class BatchUiState(
    val mode: BatchMode = BatchMode.URL_LIST,

    // ---- URL list ----
    val urlText: String = "",
    val parse: BatchParseResult = BatchParseResult(),
    val showRejects: Boolean = false,

    // ---- Pattern ----
    val patternMode: BatchPatternMode = BatchPatternMode.AUTO,
    val patternText: String = "",
    val startText: String = "1",
    val endText: String = "100",
    val stepText: String = "1",
    val paddingText: String = "2",
    val patternCheck: BatchPatternCheck? = null,
    /** The deterministic expansion (RANGE) or the scan result (AUTO). */
    val preview: List<String> = emptyList(),
    val previewTotal: Int = 0,
    val scanning: Boolean = false,
    val scanProgress: BatchScanProgress? = null,
    val scanResult: BatchScanResult? = null,

    // ---- Shared ----
    val startNow: Boolean = true,
    val busy: Boolean = false,
    val event: BatchEvent? = null,
) {
    /** The pattern inputs form a usable spec. */
    val patternValid: Boolean get() = patternCheck is BatchPatternCheck.Valid

    /** What the primary button on the pattern panel should say/do. */
    val canQueuePattern: Boolean
        get() = when (patternMode) {
            BatchPatternMode.RANGE -> patternValid && previewTotal > 0
            BatchPatternMode.AUTO -> scanResult?.urls?.isNotEmpty() == true
        }
}

/**
 * State holder for the Batch screen.
 *
 * All the real work — parsing, expansion, the scan rules — lives in
 * `domain/batch`, which is plain Kotlin and unit-tested.  This class only owns the
 * text the user is typing, the debounce that keeps a 2000-line paste off the main
 * thread, and the one-shot events the screen turns into messages.
 */
class BatchViewModel(
    private val manager: DownloadManager,
    startNowByDefault: Boolean,
) : ViewModel() {

    private val _state = MutableStateFlow(BatchUiState(startNow = startNowByDefault))
    val state: StateFlow<BatchUiState> = _state.asStateFlow()

    private var parseJob: Job? = null
    private var patternJob: Job? = null
    private var scanJob: Job? = null

    /** Probing adapter: the batch scanner only needs "does this URL exist?". */
    private val probe = BatchProbe { url ->
        val analysis = manager.inspect(url)
        BatchProbeOutcome(
            reachable = analysis.ok,
            statusCode = analysis.statusCode,
            size = analysis.totalSize,
            error = analysis.error,
        )
    }

    // ------------------------------------------------------------------ //
    // URL list
    // ------------------------------------------------------------------ //

    fun setMode(mode: BatchMode) {
        if (_state.value.mode == mode) return
        _state.update { it.copy(mode = mode, event = null) }
        // Switching to Pattern for the first time has never built a preview.
        if (mode == BatchMode.PATTERN && _state.value.patternCheck == null) refreshPattern()
    }

    fun onUrlTextChange(value: String) {
        _state.update { it.copy(urlText = value) }
        parseJob?.cancel()
        parseJob = viewModelScope.launch {
            // Debounced and off the main thread: the field is also where a user
            // pastes a saved list, and parsing must never block a keystroke.
            delay(PARSE_DEBOUNCE_MILLIS)
            val parsed = withContext(Dispatchers.Default) { BatchUrls.parse(value) }
            _state.update { if (it.urlText == value) it.copy(parse = parsed) else it }
        }
    }

    fun onImported(text: String, fileName: String) {
        _state.update { it.copy(urlText = text, mode = BatchMode.URL_LIST) }
        parseJob?.cancel()
        parseJob = viewModelScope.launch {
            val parsed = withContext(Dispatchers.Default) { BatchImport.parse(text, fileName) }
            _state.update { it.copy(parse = parsed) }
        }
    }

    fun toggleRejects() {
        _state.update { it.copy(showRejects = !it.showRejects) }
    }

    fun notifyImportFailed() {
        emitEvent(BatchEvent.ImportFailed)
    }

    fun clearUrlList() {
        parseJob?.cancel()
        _state.update { it.copy(urlText = "", parse = BatchParseResult(), showRejects = false) }
    }

    fun queueUrlList() {
        val urls = _state.value.parse.urls
        if (urls.isEmpty() || _state.value.busy) {
            if (urls.isEmpty()) emitEvent(BatchEvent.NothingQueued(BatchAddResult()))
            return
        }
        queue(urls)
    }

    // ------------------------------------------------------------------ //
    // Pattern
    // ------------------------------------------------------------------ //

    fun setPatternMode(mode: BatchPatternMode) {
        if (_state.value.patternMode == mode) return
        _state.update { it.copy(patternMode = mode) }
        refreshPattern()
    }

    fun onPatternTextChange(value: String) {
        _state.update { it.copy(patternText = value) }
        refreshPattern()
    }

    fun onStartChange(value: String) {
        _state.update { it.copy(startText = value.filter { ch -> ch.isDigit() }) }
        refreshPattern()
    }

    fun onEndChange(value: String) {
        _state.update { it.copy(endText = value.filter { ch -> ch.isDigit() }) }
        refreshPattern()
    }

    fun onStepChange(value: String) {
        _state.update { it.copy(stepText = value.filter { ch -> ch.isDigit() }) }
        refreshPattern()
    }

    fun onPaddingChange(value: String) {
        _state.update { it.copy(paddingText = value.filter { ch -> ch.isDigit() }) }
        refreshPattern()
    }

    /** Re-validates and rebuilds the preview; a new input invalidates any scan. */
    private fun refreshPattern() {
        patternJob?.cancel()
        scanJob?.cancel()
        val snapshot = _state.value
        patternJob = viewModelScope.launch {
            delay(PATTERN_DEBOUNCE_MILLIS)
            val spec = snapshot.toSpec()
            val built = withContext(Dispatchers.Default) { buildPreview(spec) }
            _state.update { current ->
                if (current.patternSignature() != snapshot.patternSignature()) {
                    current
                } else {
                    current.copy(
                        patternCheck = built.check,
                        preview = built.preview,
                        previewTotal = built.total,
                        scanResult = null,
                        scanProgress = null,
                        scanning = false,
                    )
                }
            }
        }
    }

    private data class PreviewBuild(
        val check: BatchPatternCheck,
        val preview: List<String>,
        val total: Int,
    )

    private fun buildPreview(spec: BatchPatternSpec): PreviewBuild {
        val check = BatchPattern.check(spec)
        if (check !is BatchPatternCheck.Valid) {
            return PreviewBuild(check, emptyList(), 0)
        }
        return when (spec.mode) {
            BatchPatternMode.RANGE -> {
                val all = BatchPattern.expand(check.spec)
                PreviewBuild(check, all.take(PREVIEW_LIMIT), all.size)
            }

            // Auto mode cannot know the answer without probing, so the preview is
            // the first few URLs the scan will try — never a fake "total".
            BatchPatternMode.AUTO -> {
                val candidates = BatchPattern.autoCandidates(check.spec)
                PreviewBuild(check, candidates.take(PREVIEW_LIMIT), candidates.size)
            }
        }
    }

    /** Runs the probe pass for the current pattern. */
    fun scan() {
        if (_state.value.scanning) return
        val spec = _state.value.toSpec()
        val check = BatchPattern.check(spec)
        if (check !is BatchPatternCheck.Valid) {
            emitEvent(BatchEvent.PatternInvalid((check as BatchPatternCheck.Invalid).problem))
            return
        }

        scanJob?.cancel()
        _state.update { it.copy(scanning = true, scanProgress = null, scanResult = null) }

        scanJob = viewModelScope.launch {
            val scanner = BatchScanner(probe) { progress ->
                _state.update { it.copy(scanProgress = progress) }
            }
            val result = try {
                when (spec.mode) {
                    BatchPatternMode.AUTO -> scanner.scanAuto(check.spec)
                    BatchPatternMode.RANGE -> scanner.scanRange(check.spec)
                }
            } catch (_: kotlinx.coroutines.CancellationException) {
                _state.update { it.copy(scanning = false) }
                return@launch
            }
            _state.update {
                it.copy(
                    scanning = false,
                    scanResult = result,
                    preview = result.urls.take(PREVIEW_LIMIT),
                    previewTotal = result.urls.size,
                )
            }
        }
    }

    fun cancelScan() {
        scanJob?.cancel()
        _state.update { it.copy(scanning = false) }
    }

    fun queuePattern() {
        val snapshot = _state.value
        val spec = snapshot.toSpec()
        val check = BatchPattern.check(spec)
        if (check !is BatchPatternCheck.Valid) {
            emitEvent(BatchEvent.PatternInvalid((check as BatchPatternCheck.Invalid).problem))
            return
        }
        val urls = when (spec.mode) {
            BatchPatternMode.RANGE -> snapshot.scanResult?.urls ?: BatchPattern.expand(check.spec)
            BatchPatternMode.AUTO -> snapshot.scanResult?.urls.orEmpty()
        }
        if (urls.isEmpty()) {
            emitEvent(BatchEvent.ScanFirst)
            return
        }
        queue(urls)
    }

    // ------------------------------------------------------------------ //
    // Shared
    // ------------------------------------------------------------------ //

    fun setStartNow(value: Boolean) {
        _state.update { it.copy(startNow = value) }
    }

    fun consumeEvent() {
        _state.update { it.copy(event = null) }
    }

    private fun queue(urls: List<String>) {
        if (_state.value.busy) return
        val startNow = _state.value.startNow
        viewModelScope.launch {
            _state.update { it.copy(busy = true) }
            val items = urls.map { BatchItem(url = it) }
            val result = manager.addBatch(items, startNow = startNow)
            _state.update { current ->
                val cleared = if (current.mode == BatchMode.URL_LIST) {
                    current.copy(urlText = "", parse = BatchParseResult(), showRejects = false)
                } else {
                    current
                }
                cleared.copy(
                    busy = false,
                    event = if (result.queued > 0) {
                        BatchEvent.Queued(result)
                    } else {
                        BatchEvent.NothingQueued(result)
                    },
                )
            }
        }
    }

    private fun emitEvent(event: BatchEvent) {
        _state.update { it.copy(event = event) }
    }

    private fun BatchUiState.toSpec(): BatchPatternSpec = BatchPatternSpec(
        pattern = patternText,
        mode = patternMode,
        start = startText.toIntOrNull() ?: -1,
        end = endText.toIntOrNull() ?: -1,
        step = stepText.toIntOrNull() ?: -1,
        padding = paddingText.toIntOrNull() ?: -1,
    )

    private fun BatchUiState.patternSignature(): String =
        "$patternMode|$patternText|$startText|$endText|$stepText|$paddingText"

    companion object {
        private const val PARSE_DEBOUNCE_MILLIS = 220L
        private const val PATTERN_DEBOUNCE_MILLIS = 220L

        /** How many generated URLs the preview lists before summarising. */
        const val PREVIEW_LIMIT = 8

        fun factory(manager: DownloadManager, startNowByDefault: Boolean): ViewModelProvider.Factory =
            viewModelFactory {
                initializer { BatchViewModel(manager, startNowByDefault) }
            }
    }
}
