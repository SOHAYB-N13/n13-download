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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.R
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
import com.sohayb.n13download.ui.components.N13LtrText
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
            titleRes = R.string.history_title,
            trailing = {
                if (state.entries.isNotEmpty()) {
                    N13IconButton(
                        icon = N13Icons.ClearAll,
                        contentDescription = stringResource(R.string.history_clear),
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
                    title = stringResource(R.string.history_empty_title),
                    message = stringResource(R.string.history_empty_message),
                    primaryLabel = stringResource(R.string.history_back_to_downloads),
                    onPrimary = onOpenDownloads,
                )

                state.visible.isEmpty() -> N13EmptyState(
                    icon = N13Icons.Filter,
                    title = stringResource(R.string.downloads_nothing_matches_title),
                    message = stringResource(R.string.downloads_nothing_matches_message),
                    primaryLabel = stringResource(R.string.action_show_all),
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
                    // Filename and URL are data and stay left-to-right.
                    N13LtrText(
                        text = task.filename,
                        style = MaterialTheme.typography.titleMedium,
                        color = LocalN13Colors.current.text1,
                    )
                    N13LtrText(
                        text = task.url,
                        style = MaterialTheme.typography.bodySmall,
                        color = LocalN13Colors.current.text3,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (task.isOpenable) {
                    N13SheetItem(N13Icons.External, stringResource(R.string.action_open_file), {
                        scope.launch { FileActions.open(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.Link, stringResource(R.string.action_share), {
                        scope.launch { FileActions.share(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.FolderOpen, stringResource(R.string.action_open_folder), {
                        scope.launch { FileActions.openFolder(context, manager, task) }
                        moreTask = null
                    })
                    N13SheetItem(N13Icons.Copy, stringResource(R.string.action_copy_path), {
                        scope.launch { FileActions.copyPath(context, manager, task) }
                        moreTask = null
                    })
                }
                N13SheetItem(N13Icons.Copy, stringResource(R.string.action_copy_url), {
                    FileActions.copyUrl(context, task)
                    moreTask = null
                })
                N13SheetItem(N13Icons.Retry, stringResource(R.string.action_redownload), {
                    viewModel.retry(task.id)
                    moreTask = null
                })
                N13SheetItem(N13Icons.Info, stringResource(R.string.action_properties), {
                    moreTask = null
                    onOpenTask(task.id)
                })
                N13SheetItem(N13Icons.Trash, stringResource(R.string.action_remove_from_list), {
                    deleteFileOnRemove = false
                    removeTarget = task
                    moreTask = null
                })
                if (task.isOpenable) {
                    N13SheetItem(N13Icons.Alert, stringResource(R.string.action_delete_file), {
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
            title = stringResource(R.string.history_clear_title),
            message = stringResource(R.string.history_clear_message),
            confirmLabel = stringResource(R.string.action_clear),
            onConfirm = {
                viewModel.clearHistory()
                confirmClear = false
            },
            onDismiss = { confirmClear = false },
        )
    }

    removeTarget?.let { task ->
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
                SummaryStat(
                    stringResource(R.string.history_stat_downloads),
                    state.entries.size.toString(),
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_completed),
                    state.completedCount.toString(),
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_failed),
                    state.failedCount.toString(),
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_cancelled),
                    state.cancelledCount.toString(),
                    Modifier.weight(1f),
                )
            }
            Spacer(Modifier.height(12.dp))
            Row(modifier = Modifier.fillMaxWidth()) {
                SummaryStat(
                    stringResource(R.string.history_stat_data),
                    N13Format.humanSize(state.totalBytes),
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_avg_speed),
                    if (state.averageSpeed > 1.0) {
                        N13Format.formatSpeed(state.averageSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_peak),
                    if (state.peakSpeed > 1.0) {
                        N13Format.formatSpeed(state.peakSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                    Modifier.weight(1f),
                )
                SummaryStat(
                    stringResource(R.string.history_stat_time),
                    if (state.totalTimeSeconds > 0) {
                        N13Format.formatDuration(state.totalTimeSeconds)
                    } else {
                        N13Format.UNKNOWN
                    },
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
        // The number is a technical value: always left-to-right.
        N13LtrText(
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
                label = stringResource(filter.labelRes),
                count = state.countFor(filter),
                selected = state.filter == filter,
                onClick = { onFilter(filter) },
            )
        }
    }
}
