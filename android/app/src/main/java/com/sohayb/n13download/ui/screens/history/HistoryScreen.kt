package com.sohayb.n13download.ui.screens.history

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadTask
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
import com.sohayb.n13download.ui.components.N13Sheet
import com.sohayb.n13download.ui.components.N13SheetItem
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.util.FileActions
import kotlinx.coroutines.launch

/**
 * History — completed, failed and cancelled downloads.
 *
 * Same analytics summary and row actions as the Windows history page, including
 * a working Retry on failed entries (the action that would otherwise be dead).
 */
@Composable
fun HistoryScreen(
    manager: DownloadManager,
    onOpenTask: (Long) -> Unit,
    onOpenDownloads: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager) { HistoryViewModel.factory(manager) }
    val viewModel: HistoryViewModel = viewModel(factory = factory)
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var moreTask by remember { mutableStateOf<DownloadTask?>(null) }
    var confirmClear by remember { mutableStateOf(false) }
    var removeTarget by remember { mutableStateOf<DownloadTask?>(null) }
    var deleteFileOnRemove by remember { mutableStateOf(false) }

    Column(modifier = modifier.fillMaxSize()) {
        N13PageHeader(
            title = "History",
            trailing = {
                if (state.entries.isNotEmpty()) {
                    N13IconButton(
                        icon = N13Icons.ClearAll,
                        contentDescription = "Clear history",
                        onClick = { confirmClear = true },
                    )
                }
            },
        )

        if (state.entries.isNotEmpty()) {
            HistorySummary(state = state)
            FilterRow(state = state, onFilter = viewModel::setFilter)
        }

        Box(modifier = Modifier.weight(1f)) {
            when {
                state.entries.isEmpty() -> N13EmptyState(
                    icon = N13Icons.History,
                    title = "No history yet",
                    message = "Completed and failed downloads are listed here.",
                    primaryLabel = "Back to downloads",
                    onPrimary = onOpenDownloads,
                )

                state.visible.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Filter,
                    title = "Nothing matches",
                    message = "Try a different filter.",
                    primaryLabel = "Show all",
                    onPrimary = { viewModel.setFilter(HistoryFilter.ALL) },
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

    moreTask?.let { task ->
        N13Sheet(onDismiss = { moreTask = null }) {
            Column(modifier = Modifier.padding(bottom = 12.dp)) {
                Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 10.dp)) {
                    Text(
                        text = task.filename,
                        style = MaterialTheme.typography.titleMedium,
                        color = LocalN13Colors.current.text1,
                    )
                    Text(
                        text = task.url,
                        style = MaterialTheme.typography.bodySmall,
                        color = LocalN13Colors.current.text3,
                        maxLines = 1,
                    )
                }
                if (task.isOpenable) {
                    N13SheetItem(N13Icons.External, "Open file", {
                        scope.launch { FileActions.open(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.Link, "Share", {
                        scope.launch { FileActions.share(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.FolderOpen, "Open folder", {
                        scope.launch { FileActions.openFolder(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.Copy, "Copy path", {
                        scope.launch { FileActions.copyPath(context, manager, task) }
                        moreTask = null
                    })
                }
                N13SheetItem(N13Icons.Copy, "Copy URL", {
                    FileActions.copyUrl(context, task)
                    moreTask = null
                })
                N13SheetItem(N13Icons.Retry, "Redownload", {
                    viewModel.retry(task.id)
                    moreTask = null
                })
                N13SheetItem(N13Icons.Info, "Properties", {
                    moreTask = null
                    onOpenTask(task.id)
                })
                N13SheetItem(N13Icons.Trash, "Remove from list", {
                    deleteFileOnRemove = false
                    removeTarget = task
                    moreTask = null
                })
                if (task.isOpenable) {
                    N13SheetItem(N13Icons.Alert, "Delete file", {
                        deleteFileOnRemove = true
                        removeTarget = task
                        moreTask = null
                    }, danger = true)
                }
            }
        }
    }

    if (confirmClear) {
        N13ConfirmDialog(
            title = "Clear history",
            message = "Remove all history entries? Downloaded files are not affected.",
            confirmLabel = "Clear",
            onConfirm = {
                viewModel.clearHistory()
                confirmClear = false
            },
            onDismiss = { confirmClear = false },
        )
    }

    removeTarget?.let { task ->
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
                removeTarget = null
            },
            onDismiss = { removeTarget = null },
        )
    }
}

/** Analytics block shown above the table when there is history. */
@Composable
private fun HistorySummary(state: HistoryUiState) {
    val n13 = LocalN13Colors.current

    N13Card(modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(modifier = Modifier.fillMaxWidth()) {
                SummaryStat("Downloads", state.entries.size.toString(), Modifier.weight(1f))
                SummaryStat("Completed", state.completedCount.toString(), Modifier.weight(1f))
                SummaryStat("Failed", state.failedCount.toString(), Modifier.weight(1f))
                SummaryStat("Cancelled", state.cancelledCount.toString(), Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(modifier = Modifier.fillMaxWidth()) {
                SummaryStat("Data", N13Format.humanSize(state.totalBytes), Modifier.weight(1f))
                SummaryStat(
                    "Avg speed",
                    if (state.averageSpeed > 1.0) N13Format.formatSpeed(state.averageSpeed) else N13Format.UNKNOWN,
                    Modifier.weight(1f),
                )
                SummaryStat(
                    "Peak",
                    if (state.peakSpeed > 1.0) N13Format.formatSpeed(state.peakSpeed) else N13Format.UNKNOWN,
                    Modifier.weight(1f),
                )
                SummaryStat(
                    "Time",
                    if (state.totalTimeSeconds > 0) N13Format.formatDuration(state.totalTimeSeconds) else N13Format.UNKNOWN,
                    Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun SummaryStat(label: String, value: String, modifier: Modifier = Modifier) {
    val n13 = LocalN13Colors.current
    Column(modifier = modifier) {
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            color = n13.text1,
        )
        Text(
            text = label.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = n13.text3,
        )
    }
}

@Composable
private fun FilterRow(state: HistoryUiState, onFilter: (HistoryFilter) -> Unit) {
    FlowRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HistoryFilter.entries.forEach { filter ->
            N13Chip(
                label = filter.label,
                count = state.countFor(filter),
                selected = state.filter == filter,
                onClick = { onFilter(filter) },
            )
        }
    }
}
