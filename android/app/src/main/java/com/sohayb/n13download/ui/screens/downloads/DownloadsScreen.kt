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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
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
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13Sheet
import com.sohayb.n13download.ui.components.N13SheetItem
import com.sohayb.n13download.ui.components.N13TextAction
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes
import com.sohayb.n13download.ui.util.FileActions
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
            title = "Downloads",
            trailing = {
                if (state.queuePaused) {
                    N13IconButton(
                        icon = N13Icons.Play,
                        contentDescription = "Resume the queue",
                        onClick = viewModel::resumeAll,
                        tint = LocalN13Colors.current.success,
                    )
                } else {
                    N13IconButton(
                        icon = N13Icons.Pause,
                        contentDescription = "Pause the queue",
                        onClick = viewModel::pauseAll,
                    )
                }
                N13IconButton(
                    icon = N13Icons.Plus,
                    contentDescription = "New download",
                    onClick = onAddDownload,
                    tint = LocalN13Colors.current.accent,
                )
            },
        )

        // ---- Queue summary strip ------------------------------------------
        if (state.tasks.isNotEmpty()) {
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
        if (state.tasks.isNotEmpty()) {
            FilterRow(state = state, onFilter = viewModel::setFilter)
        }

        Box(modifier = Modifier.weight(1f)) {
            when {
                state.tasks.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Download,
                    title = "No downloads yet",
                    message = "Paste a link or share one to N13 and it will show up here.",
                    primaryLabel = "New download",
                    onPrimary = onAddDownload,
                    secondaryLabel = "Open history",
                    onSecondary = onOpenHistory,
                )

                state.visible.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Filter,
                    title = "Nothing matches",
                    message = "Try a different filter.",
                    primaryLabel = "Show all downloads",
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
            title = if (deleteFileOnRemove) "Delete file" else "Remove download",
            message = if (deleteFileOnRemove) {
                "Permanently delete \"${task.filename}\" from disk? This cannot be undone."
            } else {
                "Remove this entry from the list? The file on disk is kept."
            },
            confirmLabel = if (deleteFileOnRemove) "Delete" else "Remove",
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
    val waiting = state.tasks.count { it.status == com.sohayb.n13download.domain.model.TaskStatus.QUEUED }

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
                    text = "Queue paused — waiting downloads will not start",
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.warning,
                    modifier = Modifier.weight(1f),
                )
                N13TextAction(label = "Resume", onClick = onToggleQueue, color = n13.warning)
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
                        label = "Active",
                        value = "${state.activeCount} of $slots",
                        modifier = Modifier.weight(1f),
                    )
                    StatBlock(
                        label = "Waiting",
                        value = if (waiting == 0) "none" else waiting.toString(),
                        modifier = Modifier.weight(1f),
                    )
                    StatBlock(
                        label = "Bandwidth",
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
                        label = "Retry failed (${state.failedCount})",
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
                label = filter.label,
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
                Text(
                    text = task.filename,
                    style = MaterialTheme.typography.titleMedium,
                    color = n13.text1,
                )
                Text(
                    text = task.url,
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.text3,
                    maxLines = 1,
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
                N13SheetItem(N13Icons.External, "Open file", onOpen)
                N13SheetItem(N13Icons.Link, "Share", onShare)
                N13SheetItem(N13Icons.FolderOpen, "Open folder", onOpenFolder)
                N13SheetItem(N13Icons.Copy, "Copy path", onCopyPath)
            }
            N13SheetItem(N13Icons.Copy, "Copy URL", onCopyLink)
            if (task.isRetryable) {
                N13SheetItem(N13Icons.Retry, "Redownload", onRetry)
            }
            N13SheetItem(
                N13Icons.Flag,
                "Priority",
                { showPriority = true },
                trailingText = task.priority.label,
            )
            N13SheetItem(
                N13Icons.Gauge,
                "Speed limit",
                { showSpeed = true },
                trailingText = if (task.speedLimitBps > 0L) {
                    N13Format.formatSpeed(task.speedLimitBps.toDouble())
                } else {
                    "Unlimited"
                },
            )
            N13SheetItem(N13Icons.Info, "Properties", onProperties)
            N13SheetItem(N13Icons.Trash, "Remove from list", onRemove)
            if (task.isOpenable) {
                N13SheetItem(N13Icons.Alert, "Delete file", onDeleteFile, danger = true)
            }
        }
    }
}

@Composable
private fun PriorityPicker(current: DownloadPriority, onPick: (DownloadPriority) -> Unit) {
    val n13 = LocalN13Colors.current
    Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp)) {
        Text(
            text = "Priority",
            style = MaterialTheme.typography.titleMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = "When a queue slot frees up, higher priority downloads start first.",
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(
                DownloadPriority.HIGH to DownloadPriority.HIGH_PRESET,
                DownloadPriority.NORMAL to DownloadPriority.NORMAL_PRESET,
                DownloadPriority.LOW to DownloadPriority.LOW_PRESET,
            ).forEach { (label, preset) ->
                N13Chip(
                    label = "$label priority",
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
    val presets = listOf(
        "Unlimited" to 0L,
        "256 KB/s" to 256L * 1024,
        "512 KB/s" to 512L * 1024,
        "1 MB/s" to 1024L * 1024,
        "2 MB/s" to 2L * 1024 * 1024,
        "5 MB/s" to 5L * 1024 * 1024,
    )

    Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp)) {
        Text(
            text = "Speed limit",
            style = MaterialTheme.typography.titleMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = "Caps the real transfer rate for this download only.",
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(14.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            presets.forEach { (label, value) ->
                N13Chip(
                    label = label,
                    selected = current == value,
                    onClick = { onPick(value) },
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
        }
    }
}
