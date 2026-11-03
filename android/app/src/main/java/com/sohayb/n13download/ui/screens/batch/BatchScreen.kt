package com.sohayb.n13download.ui.screens.batch

import android.content.ClipboardManager
import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.batch.BatchPattern
import com.sohayb.n13download.domain.batch.BatchPatternCheck
import com.sohayb.n13download.domain.batch.BatchPatternMode
import com.sohayb.n13download.domain.batch.BatchPatternProblem
import com.sohayb.n13download.domain.batch.BatchScanStop
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.ui.components.N13Card
import com.sohayb.n13download.ui.components.N13Chip
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13LtrText
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13ProgressBar
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.components.N13SectionLabel
import com.sohayb.n13download.ui.components.N13TextAction
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes
import kotlinx.coroutines.launch

/**
 * Batch — queue many links at once.
 *
 * The two modes are the Windows Batch page's two tabs:
 *
 *  - **URL list** — paste or import a list, see how many are usable, queue them.
 *  - **Pattern** — one URL with a `*` placeholder, expanded either deterministically
 *    over a range or by probing the sequence for files that exist.
 *
 * Everything is scrollable and every number field is its own row on a narrow
 * screen, because this page is used with the keyboard open on a 5-inch phone.
 */
@Composable
fun BatchScreen(
    manager: DownloadManager,
    startNowByDefault: Boolean,
    onQueued: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager, startNowByDefault) {
        BatchViewModel.factory(manager, startNowByDefault)
    }
    val viewModel: BatchViewModel = viewModel(factory = factory)
    val state by viewModel.state.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val importLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val loaded = BatchFileReader.read(context, uri)
            if (loaded == null) {
                viewModel.notifyImportFailed()
            } else {
                viewModel.onImported(loaded.text, loaded.name)
            }
        }
    }

    LaunchedEffect(state.event) {
        if (state.event is BatchEvent.Queued) {
            onQueued()
            viewModel.consumeEvent()
        }
    }

    Column(modifier = modifier.fillMaxSize().imePadding()) {
        N13PageHeader(titleRes = R.string.batch_title)

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp),
        ) {
            ModeTabs(mode = state.mode, onSelect = viewModel::setMode)

            Spacer(Modifier.height(12.dp))

            when (state.mode) {
                BatchMode.URL_LIST -> UrlListPanel(
                    state = state,
                    onTextChange = viewModel::onUrlTextChange,
                    onClear = viewModel::clearUrlList,
                    onToggleRejects = viewModel::toggleRejects,
                    onImport = { importLauncher.launch(arrayOf("*/*")) },
                    onPaste = { viewModel.onUrlTextChange(state.urlText + clipboardText(context)) },
                    onStartNowChange = viewModel::setStartNow,
                )

                BatchMode.PATTERN -> PatternPanel(
                    state = state,
                    onPatternChange = viewModel::onPatternTextChange,
                    onStartChange = viewModel::onStartChange,
                    onEndChange = viewModel::onEndChange,
                    onStepChange = viewModel::onStepChange,
                    onPaddingChange = viewModel::onPaddingChange,
                    onModeChange = viewModel::setPatternMode,
                    onScan = viewModel::scan,
                    onCancelScan = viewModel::cancelScan,
                    onStartNowChange = viewModel::setStartNow,
                )
            }

            state.event?.let { event ->
                Spacer(Modifier.height(12.dp))
                MessageBanner(event = event, onDismiss = viewModel::consumeEvent)
            }

            Spacer(Modifier.height(24.dp))
        }

        // ---- Pinned action bar -------------------------------------------
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            when (state.mode) {
                BatchMode.URL_LIST -> {
                    N13PrimaryButton(
                        label = stringResource(
                            R.string.batch_queue_count,
                            state.parse.total,
                        ),
                        onClick = viewModel::queueUrlList,
                        icon = N13Icons.Download,
                        enabled = state.parse.total > 0 && !state.busy,
                        modifier = Modifier.weight(1f),
                    )
                }

                BatchMode.PATTERN -> {
                    val scanning = state.scanning
                    N13SecondaryButton(
                        label = stringResource(
                            if (scanning) R.string.batch_cancel_scan else R.string.batch_scan,
                        ),
                        onClick = { if (scanning) viewModel.cancelScan() else viewModel.scan() },
                        icon = if (scanning) N13Icons.X else N13Icons.Search,
                        modifier = Modifier.weight(1f),
                    )
                    N13PrimaryButton(
                        label = stringResource(
                            R.string.batch_queue_count,
                            if (state.scanResult != null) state.scanResult!!.urls.size
                            else if (state.patternMode == BatchPatternMode.RANGE) state.previewTotal
                            else 0,
                        ),
                        onClick = viewModel::queuePattern,
                        icon = N13Icons.Download,
                        enabled = state.canQueuePattern && !state.busy,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------- //
// Mode tabs
// ---------------------------------------------------------------------- //

@Composable
private fun ModeTabs(mode: BatchMode, onSelect: (BatchMode) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        BatchMode.entries.forEach { candidate ->
            N13Chip(
                label = stringResource(
                    when (candidate) {
                        BatchMode.URL_LIST -> R.string.batch_mode_url_list
                        BatchMode.PATTERN -> R.string.batch_mode_pattern
                    },
                ),
                icon = when (candidate) {
                    BatchMode.URL_LIST -> N13Icons.List
                    BatchMode.PATTERN -> N13Icons.Filter
                },
                selected = mode == candidate,
                onClick = { onSelect(candidate) },
            )
        }
    }
}

// ---------------------------------------------------------------------- //
// URL list
// ---------------------------------------------------------------------- //

@Composable
private fun UrlListPanel(
    state: BatchUiState,
    onTextChange: (String) -> Unit,
    onClear: () -> Unit,
    onToggleRejects: () -> Unit,
    onImport: () -> Unit,
    onPaste: () -> Unit,
    onStartNowChange: (Boolean) -> Unit,
) {
    val n13 = LocalN13Colors.current

    Text(
        text = stringResource(R.string.batch_url_hint),
        style = MaterialTheme.typography.bodySmall,
        color = n13.text3,
    )

    Spacer(Modifier.height(8.dp))

    OutlinedTextField(
        value = state.urlText,
        onValueChange = onTextChange,
        modifier = Modifier.fillMaxWidth(),
        placeholder = {
            Text(
                text = stringResource(R.string.batch_url_placeholder),
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
            )
        },
        minLines = 5,
        maxLines = 9,
        textStyle = MaterialTheme.typography.bodyMedium.copy(
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
            lineHeight = 17.sp,
        ),
        shape = N13Shapes.small,
        colors = batchFieldColors(),
    )

    Spacer(Modifier.height(8.dp))

    // ---- Live counters --------------------------------------------------
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        N13Chip(
            label = stringResource(R.string.batch_valid_count, state.parse.total),
            selected = state.parse.total > 0,
        )
        if (state.parse.duplicateCount > 0) {
            N13Chip(
                label = stringResource(R.string.batch_duplicate_count, state.parse.duplicateCount),
                accent = n13.warning,
            )
        }
        if (state.parse.invalidCount > 0) {
            N13Chip(
                label = stringResource(R.string.batch_invalid_count, state.parse.invalidCount),
                accent = n13.danger,
            )
        }
    }

    if (state.parse.rejected.isNotEmpty()) {
        Spacer(Modifier.height(4.dp))
        N13TextAction(
            label = stringResource(
                if (state.showRejects) R.string.batch_hide_rejects else R.string.batch_show_rejects,
            ),
            onClick = onToggleRejects,
            color = n13.text2,
            modifier = Modifier.padding(horizontal = 0.dp),
        )
        if (state.showRejects) {
            Spacer(Modifier.height(4.dp))
            N13Card {
                Column(modifier = Modifier.padding(vertical = 4.dp)) {
                    state.parse.previewRejects(limit = 8).forEach { rejected ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 12.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.Top,
                        ) {
                            Icon(
                                imageVector = N13Icons.Alert,
                                contentDescription = null,
                                tint = n13.danger,
                                modifier = Modifier.padding(top = 2.dp).size(13.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                            Column(modifier = Modifier.weight(1f)) {
                                N13LtrText(
                                    text = rejected.line,
                                    style = MaterialTheme.typography.bodySmall.copy(
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 11.sp,
                                    ),
                                    color = n13.text2,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    text = stringResource(
                                        when (rejected.problem) {
                                            com.sohayb.n13download.domain.batch.BatchLineProblem.NOT_A_LINK ->
                                                R.string.batch_reject_not_link

                                            com.sohayb.n13download.domain.batch.BatchLineProblem.INVALID_URL ->
                                                R.string.batch_reject_invalid

                                            com.sohayb.n13download.domain.batch.BatchLineProblem.DUPLICATE ->
                                                R.string.batch_reject_duplicate

                                            com.sohayb.n13download.domain.batch.BatchLineProblem.TOO_MANY ->
                                                R.string.batch_reject_overflow
                                        },
                                    ),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = n13.danger,
                                )
                            }
                        }
                    }
                    if (state.parse.rejected.size > 8) {
                        Text(
                            text = stringResource(
                                R.string.batch_rejects_more,
                                state.parse.rejected.size - 8,
                            ),
                            style = MaterialTheme.typography.labelSmall,
                            color = n13.text3,
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                        )
                    }
                }
            }
        }
    }

    Spacer(Modifier.height(10.dp))

    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        N13SecondaryButton(
            label = stringResource(R.string.batch_import),
            onClick = onImport,
            icon = N13Icons.Folder,
        )
        N13SecondaryButton(
            label = stringResource(R.string.batch_paste),
            onClick = onPaste,
            icon = N13Icons.Paste,
        )
        if (state.urlText.isNotEmpty()) {
            N13SecondaryButton(
                label = stringResource(R.string.action_clear),
                onClick = onClear,
                icon = N13Icons.X,
            )
        }
    }

    Spacer(Modifier.height(14.dp))
    StartNowToggle(checked = state.startNow, onChange = onStartNowChange)
}

// ---------------------------------------------------------------------- //
// Pattern
// ---------------------------------------------------------------------- //

@Composable
private fun PatternPanel(
    state: BatchUiState,
    onPatternChange: (String) -> Unit,
    onStartChange: (String) -> Unit,
    onEndChange: (String) -> Unit,
    onStepChange: (String) -> Unit,
    onPaddingChange: (String) -> Unit,
    onModeChange: (BatchPatternMode) -> Unit,
    onScan: () -> Unit,
    onCancelScan: () -> Unit,
    onStartNowChange: (Boolean) -> Unit,
) {
    val n13 = LocalN13Colors.current

    Text(
        text = stringResource(R.string.batch_pattern_hint),
        style = MaterialTheme.typography.bodySmall,
        color = n13.text3,
    )

    Spacer(Modifier.height(10.dp))

    N13SectionLabel(
        text = stringResource(R.string.batch_pattern_url),
        modifier = Modifier.padding(bottom = 6.dp),
    )
    OutlinedTextField(
        value = state.patternText,
        onValueChange = onPatternChange,
        modifier = Modifier.fillMaxWidth(),
        placeholder = {
            Text(
                text = stringResource(R.string.batch_pattern_placeholder),
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
            )
        },
        singleLine = true,
        isError = state.patternCheck is BatchPatternCheck.Invalid,
        textStyle = MaterialTheme.typography.bodyMedium.copy(
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
        ),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
        shape = N13Shapes.small,
        colors = batchFieldColors(),
    )

    // ---- Validation message --------------------------------------------
    val invalid = state.patternCheck as? BatchPatternCheck.Invalid
    if (invalid != null) {
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = N13Icons.Alert,
                contentDescription = null,
                tint = n13.danger,
                modifier = Modifier.size(13.dp),
            )
            Spacer(Modifier.width(6.dp))
            Text(
                text = stringResource(patternProblemMessage(invalid.problem), invalid.detail),
                style = MaterialTheme.typography.bodySmall,
                color = n13.danger,
            )
        }
    }

    Spacer(Modifier.height(14.dp))

    // ---- Mode -----------------------------------------------------------
    N13SectionLabel(
        text = stringResource(R.string.batch_pattern_mode),
        modifier = Modifier.padding(bottom = 8.dp),
    )
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        N13Chip(
            label = stringResource(R.string.batch_pattern_mode_auto),
            icon = N13Icons.Search,
            selected = state.patternMode == BatchPatternMode.AUTO,
            onClick = { onModeChange(BatchPatternMode.AUTO) },
        )
        N13Chip(
            label = stringResource(R.string.batch_pattern_mode_range),
            icon = N13Icons.List,
            selected = state.patternMode == BatchPatternMode.RANGE,
            onClick = { onModeChange(BatchPatternMode.RANGE) },
        )
    }

    Spacer(Modifier.height(14.dp))

    // ---- Numbers --------------------------------------------------------
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        NumberField(
            label = stringResource(R.string.batch_start_at),
            value = state.startText,
            onValueChange = onStartChange,
            modifier = Modifier.weight(1f),
        )
        NumberField(
            label = stringResource(R.string.batch_step),
            value = state.stepText,
            onValueChange = onStepChange,
            modifier = Modifier.weight(1f),
        )
    }
    Spacer(Modifier.height(8.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (state.patternMode == BatchPatternMode.RANGE) {
            NumberField(
                label = stringResource(R.string.batch_end_at),
                value = state.endText,
                onValueChange = onEndChange,
                modifier = Modifier.weight(1f),
            )
        } else {
            Box(modifier = Modifier.weight(1f))
        }
        NumberField(
            label = stringResource(R.string.batch_zero_padding),
            value = state.paddingText,
            onValueChange = onPaddingChange,
            modifier = Modifier.weight(1f),
        )
    }

    Spacer(Modifier.height(14.dp))

    // ---- Scan progress --------------------------------------------------
    if (state.scanning) {
        val progress = state.scanProgress
        N13Card(containerColor = n13.secondary) {
            Column(modifier = Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = N13Icons.Activity,
                        contentDescription = null,
                        tint = n13.accent,
                        modifier = Modifier.size(15.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = stringResource(
                            R.string.batch_scan_progress,
                            progress?.attempted ?: 0,
                            progress?.found ?: 0,
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = n13.text1,
                    )
                    Spacer(Modifier.weight(1f))
                    N13TextAction(
                        label = stringResource(R.string.batch_cancel_scan),
                        onClick = onCancelScan,
                        color = n13.text3,
                    )
                }
                Spacer(Modifier.height(8.dp))
                N13ProgressBar(fraction = null, height = 4.dp)
                if (progress != null) {
                    Spacer(Modifier.height(8.dp))
                    N13LtrText(
                        text = progress.url,
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = n13.text3,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        Spacer(Modifier.height(14.dp))
    }

    // ---- Preview --------------------------------------------------------
    if (state.preview.isNotEmpty()) {
        PreviewCard(state = state)
        Spacer(Modifier.height(14.dp))
    }

    StartNowToggle(checked = state.startNow, onChange = onStartNowChange)
}

@Composable
private fun PreviewCard(state: BatchUiState) {
    val n13 = LocalN13Colors.current
    val scanned = state.scanResult != null
    val title = when {
        scanned -> stringResource(R.string.batch_preview_found, state.scanResult!!.urls.size)
        state.patternMode == BatchPatternMode.RANGE ->
            stringResource(R.string.batch_preview_range, state.previewTotal)

        else -> stringResource(R.string.batch_preview_auto)
    }

    N13Card {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = if (scanned) N13Icons.Check else N13Icons.List,
                    contentDescription = null,
                    tint = if (scanned) n13.success else n13.text3,
                    modifier = Modifier.size(15.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = title,
                    style = MaterialTheme.typography.labelLarge,
                    color = if (scanned) n13.success else n13.text2,
                )
            }

            Spacer(Modifier.height(8.dp))

            state.preview.forEach { url ->
                N13LtrText(
                    text = url,
                    style = MaterialTheme.typography.labelSmall.copy(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                    ),
                    color = n13.text3,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(vertical = 1.dp),
                )
            }

            val remaining = state.previewTotal - state.preview.size
            if (remaining > 0) {
                Text(
                    text = stringResource(R.string.batch_preview_more, remaining),
                    style = MaterialTheme.typography.labelSmall,
                    color = n13.text3,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }

            // What the scan stopped on is the difference between "the sequence
            // ended" and "the host fell over" — worth one line.
            state.scanResult?.stop?.let { stop ->
                val stopText = when (stop) {
                    BatchScanStop.CONSECUTIVE_MISSES -> R.string.batch_stop_misses
                    BatchScanStop.ATTEMPT_LIMIT -> R.string.batch_stop_attempts
                    BatchScanStop.SERVER_ERRORS -> R.string.batch_stop_server
                    BatchScanStop.FINISHED, BatchScanStop.CANCELLED -> null
                }
                if (stopText != null) {
                    Text(
                        text = stringResource(
                            stopText,
                            if (stop == BatchScanStop.CONSECUTIVE_MISSES) {
                                BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES
                            } else {
                                BatchPattern.AUTO_MAX_ATTEMPTS
                            },
                        ),
                        style = MaterialTheme.typography.labelSmall,
                        color = n13.warning,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------- //
// Small pieces
// ---------------------------------------------------------------------- //

@Composable
private fun NumberField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier,
        label = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        shape = N13Shapes.small,
        colors = batchFieldColors(),
    )
}

@Composable
private fun StartNowToggle(checked: Boolean, onChange: (Boolean) -> Unit) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(N13Shapes.small)
            .background(n13.secondary)
            .clickable { onChange(!checked) }
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = if (checked) N13Icons.Bolt else N13Icons.Clock,
            contentDescription = null,
            tint = if (checked) n13.accent else n13.text3,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.add_start_immediately),
                style = MaterialTheme.typography.bodyMedium,
                color = n13.text1,
            )
            Text(
                text = stringResource(
                    if (checked) R.string.add_start_immediately_hint
                    else R.string.add_start_later_hint,
                ),
                style = MaterialTheme.typography.bodySmall,
                color = n13.text3,
            )
        }
        Box(
            modifier = Modifier
                .size(20.dp)
                .clip(androidx.compose.foundation.shape.RoundedCornerShape(50))
                .background(if (checked) n13.accent else n13.active)
                .border(
                    1.dp,
                    if (checked) n13.accent else n13.borderStrong,
                    androidx.compose.foundation.shape.RoundedCornerShape(50),
                ),
        )
    }
}

@Composable
private fun MessageBanner(event: BatchEvent, onDismiss: () -> Unit) {
    val n13 = LocalN13Colors.current

    // Every string is resolved here, at composable scope: `stringResource` cannot
    // be called from inside a plain lambda such as `buildString {}`.
    var icon = N13Icons.Info
    var tint = n13.info
    var text = ""

    when (event) {
        is BatchEvent.Queued -> {
            icon = N13Icons.Check
            tint = n13.success
            text = stringResource(R.string.batch_queued_count, event.result.queued)
            if (event.result.skipped > 0) {
                text += " · " + stringResource(R.string.batch_skipped_count, event.result.skipped)
            }
            if (event.result.rejected.isNotEmpty()) {
                text += " · " + stringResource(
                    R.string.batch_rejected_count,
                    event.result.rejected.size,
                )
            }
        }

        is BatchEvent.NothingQueued -> {
            icon = N13Icons.Info
            tint = n13.warning
            text = if (event.result.skipped > 0) {
                stringResource(R.string.batch_all_duplicates, event.result.skipped)
            } else {
                stringResource(R.string.batch_nothing_queued)
            }
        }

        is BatchEvent.PatternInvalid -> {
            icon = N13Icons.Alert
            tint = n13.danger
            text = stringResource(patternProblemMessage(event.problem), "")
        }

        BatchEvent.ScanFirst -> {
            icon = N13Icons.Search
            tint = n13.info
            text = stringResource(R.string.batch_scan_first)
        }

        BatchEvent.ImportFailed -> {
            icon = N13Icons.Alert
            tint = n13.danger
            text = stringResource(R.string.batch_import_failed)
        }
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(N13Shapes.small)
            .background(tint.copy(alpha = 0.12f))
            .border(1.dp, tint.copy(alpha = 0.3f), N13Shapes.small)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = tint,
            modifier = Modifier.size(15.dp),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = tint,
            modifier = Modifier.weight(1f),
        )
        N13TextAction(label = stringResource(R.string.action_ok), onClick = onDismiss, color = tint)
    }
}

@Composable
private fun patternProblemMessage(problem: BatchPatternProblem): Int = when (problem) {
    BatchPatternProblem.EMPTY -> R.string.batch_problem_empty
    BatchPatternProblem.NO_PLACEHOLDER -> R.string.batch_problem_placeholder
    BatchPatternProblem.INVALID_URL -> R.string.batch_problem_url
    BatchPatternProblem.START_OUT_OF_RANGE -> R.string.batch_problem_start
    BatchPatternProblem.PADDING_OUT_OF_RANGE -> R.string.batch_problem_padding
    BatchPatternProblem.END_BEFORE_START -> R.string.batch_problem_end
    BatchPatternProblem.STEP_OUT_OF_RANGE -> R.string.batch_problem_step
    BatchPatternProblem.TOO_MANY -> R.string.batch_problem_too_many
}

@Composable
private fun batchFieldColors() = TextFieldDefaults.colors(
    focusedContainerColor = LocalN13Colors.current.secondary,
    unfocusedContainerColor = LocalN13Colors.current.secondary,
    disabledContainerColor = LocalN13Colors.current.secondary,
    focusedIndicatorColor = LocalN13Colors.current.accent,
    unfocusedIndicatorColor = LocalN13Colors.current.borderStrong,
    focusedTextColor = LocalN13Colors.current.text1,
    unfocusedTextColor = LocalN13Colors.current.text1,
    focusedLabelColor = LocalN13Colors.current.accent,
    unfocusedLabelColor = LocalN13Colors.current.text3,
    cursorColor = LocalN13Colors.current.accent,
)

/** The clipboard's text, or empty when it holds nothing usable. */
private fun clipboardText(context: Context): String {
    val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        ?: return ""
    val clip = manager.primaryClip ?: return ""
    if (clip.itemCount == 0) return ""
    val text = clip.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
    return if (text.isBlank()) "" else "\n$text"
}
