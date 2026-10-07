package com.sohayb.n13download.ui.screens.downloads

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.R
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.ui.components.DownloadRow
import com.sohayb.n13download.ui.components.DownloadRowActions
import com.sohayb.n13download.ui.components.N13Card
import com.sohayb.n13download.ui.components.N13Chip
import com.sohayb.n13download.ui.components.N13ConfirmDialog
import com.sohayb.n13download.ui.components.N13EmptyState
import com.sohayb.n13download.ui.components.N13IconButton
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13ListPadding
import com.sohayb.n13download.ui.components.N13LtrText
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13Sheet
import com.sohayb.n13download.ui.components.N13SheetItem
import com.sohayb.n13download.ui.components.N13TextAction
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes
import com.sohayb.n13download.ui.util.FileActions
import com.sohayb.n13download.ui.util.localizedLabel
import com.sohayb.n13download.ui.util.priorityChipLabel
import kotlinx.coroutines.launch
import androidx.compose.runtime.rememberCoroutineScope

/**
 * Downloads — the start destination and the live queue.
 *
 * Shows the running and waiting work with the same fields as the Windows row,
 * plus the queue summary strip (slots, waiting, bandwidth, retry-failed) the
 * desktop product puts above the list.
 */
@Composable
fun DownloadsScreen(
    manager: DownloadManager,
    onAddDownload: () -> Unit,
    onOpenTask: (Long) -> Unit,
    onOpenHistory: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager) { DownloadsViewModel.factory(manager) }
    val viewModel: DownloadsViewModel = viewModel(factory = factory)
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var moreTask by remember { mutableStateOf<DownloadTask?>(null) }
    var confirmTask by remember { mutableStateOf<DownloadTask?>(null) }
    var deleteFileOnRemove by remember { mutableStateOf(false) }

    Column(modifier = modifier.fillMaxSize()) {
        N13PageHeader(
            titleRes = R.string.downloads_title,
            trailing = {
                if (state.queuePaused) {
                    N13IconButton(
                        icon = N13Icons.Play,
                        contentDescription = stringResource(R.string.downloads_resume_queue),
                        onClick = viewModel::resumeAll,
                        tint = LocalN13Colors.current.success,
                    )
                } else {
                    N13IconButton(
                        icon = N13Icons.Pause,
                        contentDescription = stringResource(R.string.downloads_pause_queue),
                        onClick = viewModel::pauseAll,
                    )
                }
                N13IconButton(
                    icon = N13Icons.Plus,
                    contentDescription = stringResource(R.string.downloads_new),
                    onClick = onAddDownload,
                    tint = LocalN13Colors.current.accent,
                )
            },
        )

        // ---- Queue summary strip ------------------------------------------
        if (state.allTasks.isNotEmpty()) {
            QueueSummaryStrip(
                state = state,
                onToggleQueue = {
                    if (state.queuePaused) viewModel.resumeAll() else viewModel.pauseAll()
                },
                onRetryFailed = {
                    viewModel.retryFailed()
                    onOpenHistory()
                },
            )
        }

        // ---- Filters -------------------------------------------------------
        if (state.allTasks.isNotEmpty()) {
            FilterRow(state = state, onFilter = viewModel::setFilter)
        }

        Box(modifier = Modifier.weight(1f)) {
            when {
                state.allTasks.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Download,
                    title = stringResource(R.string.downloads_empty_title),
                    message = stringResource(R.string.downloads_empty_message),
                    primaryLabel = stringResource(R.string.downloads_new),
                    onPrimary = onAddDownload,
                    secondaryLabel = stringResource(R.string.downloads_open_history),
                    onSecondary = onOpenHistory,
                )

                state.visible.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Filter,
                    title = stringResource(R.string.downloads_nothing_matches_title),
                    message = stringResource(R.string.downloads_nothing_matches_message),
                    primaryLabel = stringResource(R.string.downloads_show_all),
                    onPrimary = { viewModel.setFilter(QueueFilter.ALL) },
                )

                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = N13ListPadding,
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(items = state.visible, key = { it.id }) { task ->
                        DownloadRow(
                            task = task,
                            modifier = Modifier.animateItem(),
                            onClick = { onOpenTask(task.id) },
                            actions = DownloadRowActions(
                                onPause = { viewModel.pause(task.id) },
                                onResume = { viewModel.resume(task.id) },
                                onCancel = { viewModel.cancel(task.id) },
                                onRetry = { viewModel.retry(task.id) },
                                onOpen = { scope.launch { FileActions.open(context, manager, task) } },
                                onShare = { scope.launch { FileActions.share(context, manager, task) } },
                                onMore = { moreTask = task },
                            ),
                        )
                    }
                }
            }
        }
    }

    // ---- Row overflow menu -------------------------------------------------
    moreTask?.let { task ->
        DownloadActionsSheet(
            task = task,
            onDismiss = { moreTask = null },
            onOpen = { scope.launch { FileActions.open(context, manager, task) } },
            onShare = { scope.launch { FileActions.share(context, manager, task) } },
            onCopyLink = { FileActions.copyUrl(context, task) },
            onCopyPath = { scope.launch { FileActions.copyPath(context, manager, task) } },
            onOpenFolder = { scope.launch { FileActions.openFolder(context, manager, task) } },
            onProperties = {
                moreTask = null
                onOpenTask(task.id)
            },
            onPriority = { priority -> viewModel.setPriority(task.id, priority) },
            onSpeedLimit = { limit -> viewModel.setSpeedLimit(task.id, limit) },
            onRetry = { viewModel.retry(task.id) },
            onRemove = { deleteFileOnRemove = false; confirmTask = task },
            onDeleteFile = { deleteFileOnRemove = true; confirmTask = task },
        )
    }

    confirmTask?.let { task ->
        N13ConfirmDialog(
            title = stringResource(
                if (deleteFileOnRemove) R.string.confirm_delete_file_title
                else R.string.confirm_remove_title,
            ),
            message = if (deleteFileOnRemove) {
                stringResource(R.string.confirm_delete_file_message, task.filename)
            } else {
                stringResource(R.string.confirm_remove_message)
            },
            confirmLabel = stringResource(
                if (deleteFileOnRemove) R.string.action_delete else R.string.action_remove,
            ),
            onConfirm = {
                viewModel.remove(task.id, deleteFileOnRemove)
                confirmTask = null
            },
            onDismiss = { confirmTask = null },
        )
    }
}

/** The queue strip: slots, waiting, bandwidth and the retry-failed shortcut. */
@Composable
private fun QueueSummaryStrip(
    state: DownloadsUiState,
    onToggleQueue: () -> Unit,
    onRetryFailed: () -> Unit,
) {
    val n13 = LocalN13Colors.current
    val slots = state.settings.maxConcurrent
    val waiting = state.allTasks.count { it.status == com.sohayb.n13download.domain.model.TaskStatus.QUEUED }

    Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
        if (state.queuePaused) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(N13Shapes.small)
                    .background(n13.warningSoft)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    imageVector = N13Icons.Pause,
                    contentDescription = null,
                    tint = n13.warning,
                    modifier = Modifier.size(15.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = stringResource(R.string.downloads_queue_paused),
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.warning,
                    modifier = Modifier.weight(1f),
                )
                N13TextAction(
                    label = stringResource(R.string.action_resume),
                    onClick = onToggleQueue,
                    color = n13.warning,
                )
            }
            Spacer(Modifier.height(8.dp))
        }

        N13Card {
            Column(modifier = Modifier.padding(vertical = 10.dp)) {
                Row(
                    modifier = Modifier.padding(horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    StatBlock(
                        label = stringResource(R.string.downloads_stat_active),
                        value = stringResource(R.string.downloads_active_of, state.activeCount, slots),
                        modifier = Modifier.weight(1f),
                    )
                    StatBlock(
                        label = stringResource(R.string.downloads_stat_waiting),
                        value = if (waiting == 0) {
                            stringResource(R.string.downloads_waiting_none)
                        } else {
                            waiting.toString()
                        },
                        modifier = Modifier.weight(1f),
                    )
                    StatBlock(
                        label = stringResource(R.string.downloads_stat_bandwidth),
                        value = if (state.totalSpeed > 1.0) {
                            N13Format.formatSpeed(state.totalSpeed)
                        } else {
                            N13Format.UNKNOWN
                        },
                        accent = state.totalSpeed > 1.0,
                        modifier = Modifier.weight(1f),
                    )
                }

                if (state.failedCount > 0) {
                    Spacer(Modifier.height(6.dp))
                    N13TextAction(
                        label = stringResource(R.string.downloads_retry_failed, state.failedCount),
                        onClick = onRetryFailed,
                        modifier = Modifier.padding(start = 8.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun StatBlock(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    accent: Boolean = false,
) {
    val n13 = LocalN13Colors.current
    Column(modifier = modifier) {
        Text(
            text = label.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            color = if (accent) n13.accent else n13.text1,
        )
    }
}

@Composable
private fun FilterRow(state: DownloadsUiState, onFilter: (QueueFilter) -> Unit) {
    FlowRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        QueueFilter.entries.forEach { filter ->
            N13Chip(
                label = stringResource(filter.labelRes),
                count = state.countFor(filter),
                selected = state.filter == filter,
                onClick = { onFilter(filter) },
            )
        }
    }
}

/** The overflow sheet for one row: everything the Windows context menu offers. */
@Composable
private fun DownloadActionsSheet(
    task: DownloadTask,
    onDismiss: () -> Unit,
    onOpen: () -> Unit,
    onShare: () -> Unit,
    onCopyLink: () -> Unit,
    onCopyPath: () -> Unit,
    onOpenFolder: () -> Unit,
    onProperties: () -> Unit,
    onPriority: (DownloadPriority) -> Unit,
    onSpeedLimit: (Long) -> Unit,
    onRetry: () -> Unit,
    onRemove: () -> Unit,
    onDeleteFile: () -> Unit,
) {
    val n13 = LocalN13Colors.current
    var showPriority by remember { mutableStateOf(false) }
    var showSpeed by remember { mutableStateOf(false) }

    N13Sheet(onDismiss = onDismiss) {
        Column(modifier = Modifier.padding(bottom = 12.dp)) {
            Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 10.dp)) {
                // Filename and URL are data: they keep their own left-to-right
                // reading order whatever the UI language is.
                N13LtrText(
                    text = task.filename,
                    style = MaterialTheme.typography.titleMedium,
                    color = n13.text1,
                )
                N13LtrText(
                    text = task.url,
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.text3,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            if (showPriority) {
                PriorityPicker(current = task.priority, onPick = { onPriority(it); onDismiss() })
                return@Column
            }
            if (showSpeed) {
                SpeedLimitPicker(current = task.speedLimitBps, onPick = { onSpeedLimit(it); onDismiss() })
                return@Column
            }

            if (task.isOpenable) {
                N13SheetItem(N13Icons.External, stringResource(R.string.action_open_file), onOpen)
                N13SheetItem(N13Icons.Link, stringResource(R.string.action_share), onShare)
                N13SheetItem(N13Icons.FolderOpen, stringResource(R.string.action_open_folder), onOpenFolder)
                N13SheetItem(N13Icons.Copy, stringResource(R.string.action_copy_path), onCopyPath)
            }
            N13SheetItem(N13Icons.Copy, stringResource(R.string.action_copy_url), onCopyLink)
            if (task.isRetryable) {
                N13SheetItem(N13Icons.Retry, stringResource(R.string.action_redownload), onRetry)
            }
            N13SheetItem(
                N13Icons.Flag,
                stringResource(R.string.priority_title),
                { showPriority = true },
                trailingText = task.priority.localizedLabel(),
            )
            N13SheetItem(
                N13Icons.Gauge,
                stringResource(R.string.field_speed_limit),
                { showSpeed = true },
                trailingText = if (task.speedLimitBps > 0L) {
                    N13Format.formatSpeed(task.speedLimitBps.toDouble())
                } else {
                    stringResource(R.string.settings_unlimited)
                },
            )
            N13SheetItem(N13Icons.Info, stringResource(R.string.action_properties), onProperties)
            N13SheetItem(N13Icons.Trash, stringResource(R.string.action_remove_from_list), onRemove)
            if (task.isOpenable) {
                N13SheetItem(
                    N13Icons.Alert,
                    stringResource(R.string.action_delete_file),
                    onDeleteFile,
                    danger = true,
                )
            }
        }
    }
}

@Composable
private fun PriorityPicker(current: DownloadPriority, onPick: (DownloadPriority) -> Unit) {
    val n13 = LocalN13Colors.current
    Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp)) {
        Text(
            text = stringResource(R.string.priority_title),
            style = MaterialTheme.typography.titleMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = stringResource(R.string.priority_hint),
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(
                DownloadPriority.HIGH to DownloadPriority.HIGH_PRESET,
                DownloadPriority.NORMAL to DownloadPriority.NORMAL_PRESET,
                DownloadPriority.LOW to DownloadPriority.LOW_PRESET,
            ).forEach { (priority, preset) ->
                N13Chip(
                    label = priorityChipLabel(priority),
                    selected = current.value == preset,
                    onClick = { onPick(DownloadPriority.of(preset)) },
                )
            }
        }
    }
}

@Composable
private fun SpeedLimitPicker(current: Long, onPick: (Long) -> Unit) {
    val n13 = LocalN13Colors.current
    val unlimited = stringResource(R.string.settings_unlimited)
    val presets = listOf(
        unlimited to 0L,
        "256 KB/s" to 256L * 1024,
        "512 KB/s" to 512L * 1024,
        "1 MB/s" to 1024L * 1024,
        "2 MB/s" to 2L * 1024 * 1024,
        "5 MB/s" to 5L * 1024 * 1024,
    )

    Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp)) {
        Text(
            text = stringResource(R.string.field_speed_limit),
            style = MaterialTheme.typography.titleMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = stringResource(R.string.speed_limit_picker_hint),
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(14.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            presets.forEach { (label, value) ->
                N13Chip(
                    // The rate itself is a technical value and stays LTR.
                    label = label,
                    selected = current == value,
                    onClick = { onPick(value) },
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
        }
    }
}
