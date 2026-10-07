package com.sohayb.n13download.ui.screens.details

import androidx.compose.animation.AnimatedVisibility
import android.content.Context
import androidx.annotation.StringRes
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.R
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.DownloadPriority
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.ui.components.N13Card
import com.sohayb.n13download.ui.components.N13Chip
import com.sohayb.n13download.ui.components.N13ConfirmDialog
import com.sohayb.n13download.ui.components.N13EmptyState
import com.sohayb.n13download.ui.components.N13IconButton
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13InfoRow
import com.sohayb.n13download.ui.components.N13LtrText
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13ProgressBar
import com.sohayb.n13download.ui.components.N13RowDivider
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.components.N13SectionLabel
import com.sohayb.n13download.ui.components.N13StatusBadge
import com.sohayb.n13download.ui.components.N13TextAction
import com.sohayb.n13download.ui.components.visual
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes
import com.sohayb.n13download.ui.util.FileActions
import com.sohayb.n13download.ui.util.absoluteTime
import com.sohayb.n13download.ui.util.categoryLabel
import com.sohayb.n13download.ui.util.errorLabel
import com.sohayb.n13download.ui.util.localizedLabel
import com.sohayb.n13download.ui.util.priorityChipLabel
import kotlinx.coroutines.launch

/**
 * Properties — everything N13 knows about one download.
 *
 * Laid out as a stack of labelled sections.  Each field is a two-line
 * `LABEL / VALUE` row, so an arbitrarily long filename, URL or path wraps inside
 * its own row and can never run into a neighbouring field.  Values that should
 * stay on one line (paths, URLs, ETags) are ellipsised and can be expanded or
 * copied on demand, which keeps the screen readable without hiding the data.
 *
 * No dead buttons: each action only appears when it can actually do something.
 */
@Composable
fun DetailsScreen(
    manager: DownloadManager,
    taskId: Long,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager, taskId) { DetailsViewModel.factory(manager, taskId) }
    val viewModel: DetailsViewModel = viewModel(factory = factory)
    val task by viewModel.task.collectAsStateWithLifecycle()
    val destinationLabel by viewModel.destinationLabel.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var confirmDelete by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }

    val current = task
    if (current == null) {
        Column(modifier = modifier.fillMaxSize()) {
            N13PageHeader(
                titleRes = R.string.details_title,
                trailing = {
                    N13IconButton(
                        icon = N13Icons.ChevronLeft,
                        contentDescription = stringResource(R.string.action_back),
                        onClick = onBack,
                    )
                },
            )
            Box(modifier = Modifier.weight(1f)) {
                N13EmptyState(
                    icon = N13Icons.Info,
                    title = stringResource(R.string.details_not_found),
                    message = stringResource(R.string.details_not_found_message),
                    primaryLabel = stringResource(R.string.action_back),
                    onPrimary = onBack,
                )
            }
        }
        return
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        N13PageHeader(
            titleRes = R.string.details_title,
            trailing = {
                if (current.isOpenable) {
                    N13IconButton(
                        icon = N13Icons.Link,
                        contentDescription = stringResource(R.string.action_share),
                        onClick = { scope.launch { FileActions.share(context, manager, current) } },
                    )
                }
                N13IconButton(
                    icon = N13Icons.ChevronLeft,
                    contentDescription = stringResource(R.string.action_back),
                    onClick = onBack,
                )
            },
        )

        Column(modifier = Modifier.padding(horizontal = 16.dp)) {
            HeaderCard(task = current)

            Spacer(Modifier.height(14.dp))
            ActionButtons(
                task = current,
                onPause = viewModel::pause,
                onResume = viewModel::resume,
                onCancel = viewModel::cancel,
                onRetry = viewModel::retry,
                onOpen = { scope.launch { FileActions.open(context, manager, current) } },
                onShare = { scope.launch { FileActions.share(context, manager, current) } },
            )

            // ---- Where the file is ------------------------------------------
            Spacer(Modifier.height(18.dp))
            DestinationCard(
                task = current,
                destinationLabel = destinationLabel.ifBlank { current.directory },
                onOpen = { scope.launch { FileActions.open(context, manager, current) } },
                onOpenFolder = { scope.launch { FileActions.openFolder(context, manager, current) } },
                onCopyPath = { scope.launch { FileActions.copyPath(context, manager, current) } },
                onShare = { scope.launch { FileActions.share(context, manager, current) } },
            )

            // ---- File --------------------------------------------------------
            SectionStart(R.string.section_file)
            N13Card {
                N13InfoRow(
                    label = stringResource(R.string.field_name),
                    value = current.filename,
                    icon = N13Icons.Download,
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_category),
                    value = categoryLabel(current.category),
                )
                if (current.resolvedPath.isNotBlank()) {
                    N13RowDivider()
                    ExpandableRow(
                        label = stringResource(R.string.field_path),
                        value = current.resolvedPath,
                    )
                }
            }

            // ---- Transfer ----------------------------------------------------
            SectionStart(R.string.section_transfer)
            N13Card {
                N13InfoRow(
                    label = stringResource(R.string.field_size),
                    value = N13Format.progressSize(
                        current.downloadedSize,
                        current.totalSize.takeIf { it > 0 },
                    ),
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_speed),
                    value = if (current.status == TaskStatus.DOWNLOADING) {
                        N13Format.formatSpeed(current.currentSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_average_speed),
                    value = if (current.averageSpeed > 1.0) {
                        N13Format.formatSpeed(current.averageSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_time_left),
                    value = N13Format.formatEta(current.etaSeconds),
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_elapsed),
                    value = N13Format.formatDuration(current.elapsedSeconds),
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_connections),
                    value = if (current.segmentCount > 0) {
                        stringResource(
                            R.string.details_connections_of,
                            current.connections,
                            current.segmentCount,
                        )
                    } else {
                        current.connections.toString()
                    },
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_priority),
                    value = current.priority.localizedLabel(),
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_speed_limit),
                    value = if (current.speedLimitBps > 0L) {
                        N13Format.formatSpeed(current.speedLimitBps.toDouble())
                    } else {
                        stringResource(R.string.value_unlimited)
                    },
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_retries),
                    value = current.retryCount.toString(),
                    ltr = true,
                )
            }

            // ---- Server ------------------------------------------------------
            SectionStart(R.string.section_server)
            N13Card {
                N13InfoRow(
                    label = stringResource(R.string.field_server),
                    value = current.server.ifBlank { N13Format.UNKNOWN },
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_content_type),
                    value = current.contentType.ifBlank { N13Format.UNKNOWN },
                    mono = true,
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_resumable),
                    value = stringResource(
                        if (current.supportsRange) R.string.value_yes else R.string.value_no,
                    ),
                )
                N13RowDivider()
                ExpandableRow(
                    label = stringResource(R.string.field_source_url),
                    value = current.url,
                    copyValue = current.url,
                )
                if (current.etag.isNotBlank()) {
                    N13RowDivider()
                    ExpandableRow(label = "ETag", value = current.etag)
                }
                if (current.lastModified.isNotBlank()) {
                    N13RowDivider()
                    N13InfoRow(
                        label = stringResource(R.string.field_last_modified),
                        value = current.lastModified,
                        ltr = true,
                    )
                }
            }

            // ---- Timeline ----------------------------------------------------
            SectionStart(R.string.section_timeline)
            N13Card {
                // Timestamps are values, not prose: they keep the LTR layout the
                // screen was designed around.
                N13InfoRow(
                    label = stringResource(R.string.field_added),
                    value = absoluteTime(current.createdAt),
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_started),
                    value = absoluteTime(current.startedAt),
                    ltr = true,
                )
                N13RowDivider()
                N13InfoRow(
                    label = stringResource(R.string.field_finished),
                    value = absoluteTime(current.completedAt),
                    ltr = true,
                )
            }

            // ---- Error -------------------------------------------------------
            if (current.error.isNotBlank()) {
                SectionStart(R.string.section_last_error)
                N13Card(borderColor = LocalN13Colors.current.danger.copy(alpha = 0.35f)) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        verticalAlignment = Alignment.Top,
                    ) {
                        Icon(
                            imageVector = N13Icons.Alert,
                            contentDescription = null,
                            tint = LocalN13Colors.current.danger,
                            modifier = Modifier
                                .padding(top = 2.dp)
                                .size(16.dp),
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(
                            text = errorLabel(current.error),
                            style = MaterialTheme.typography.bodyMedium,
                            color = LocalN13Colors.current.danger,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }

            // ---- Reliable hit target for the quick priority change -----------
            SectionStart(R.string.priority_title)
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf(
                    DownloadPriority.HIGH to DownloadPriority.HIGH_PRESET,
                    DownloadPriority.NORMAL to DownloadPriority.NORMAL_PRESET,
                    DownloadPriority.LOW to DownloadPriority.LOW_PRESET,
                ).forEach { (priority, preset) ->
                    N13Chip(
                        label = priorityChipLabel(priority),
                        selected = current.priority.value == preset,
                        onClick = { viewModel.setPriority(DownloadPriority.of(preset)) },
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
            }

            // ---- Destructive -------------------------------------------------
            Spacer(Modifier.height(18.dp))
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                N13SecondaryButton(
                    label = stringResource(R.string.action_remove_from_list),
                    onClick = { confirmRemove = true },
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (current.isOpenable) {
                    N13SecondaryButton(
                        label = stringResource(R.string.action_delete_file),
                        onClick = { confirmDelete = true },
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
            }

            Spacer(Modifier.height(40.dp))
        }
    }

    if (confirmDelete) {
        N13ConfirmDialog(
            title = stringResource(R.string.confirm_delete_file_title),
            message = stringResource(R.string.confirm_delete_file_message, current.filename),
            confirmLabel = stringResource(R.string.action_delete),
            onConfirm = {
                viewModel.remove(deleteFile = true)
                confirmDelete = false
                onBack()
            },
            onDismiss = { confirmDelete = false },
        )
    }

    if (confirmRemove) {
        N13ConfirmDialog(
            title = stringResource(R.string.confirm_remove_title),
            message = stringResource(R.string.confirm_remove_message),
            confirmLabel = stringResource(R.string.action_remove),
            onConfirm = {
                viewModel.remove(deleteFile = false)
                confirmRemove = false
                onBack()
            },
            onDismiss = { confirmRemove = false },
        )
    }
}

/** Section label with consistent spacing above the card that follows it. */
@Composable
private fun SectionStart(@StringRes textRes: Int) {
    Spacer(Modifier.height(18.dp))
    N13SectionLabel(
        text = stringResource(textRes),
        modifier = Modifier.padding(start = 4.dp, bottom = 8.dp),
    )
}

/**
 * The identity card: filename on its own line above the badge, so a long name
 * wraps instead of pushing the status pill off the edge.
 */
@Composable
private fun HeaderCard(task: DownloadTask) {
    val n13 = LocalN13Colors.current
    N13Card {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(modifier = Modifier.weight(1f)) {
                    // The filename is data and keeps its own reading order.
                    N13LtrText(
                        text = task.filename,
                        style = MaterialTheme.typography.titleMedium,
                        color = n13.text1,
                    )
                }
                Spacer(Modifier.width(10.dp))
                N13StatusBadge(visual = task.status.visual())
            }
            if (task.hasKnownSize) {
                Spacer(Modifier.height(12.dp))
                N13ProgressBar(
                    fraction = (task.percent / 100.0).toFloat(),
                    label = "${task.percent.toInt()}%",
                )
                Spacer(Modifier.height(6.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    N13LtrText(
                        text = N13Format.progressSize(task.downloadedSize, task.totalSize),
                        style = MaterialTheme.typography.labelSmall,
                        color = n13.text2,
                    )
                    if (task.status == TaskStatus.DOWNLOADING) {
                        N13LtrText(
                            text = N13Format.formatSpeed(task.currentSpeed),
                            style = MaterialTheme.typography.labelSmall,
                            color = n13.accent,
                        )
                    }
                }
            }
        }
    }
}

/**
 * "Where did my file go?" answered in one card.
 *
 * The destination is the headline, with the finish-line actions right beside it,
 * so the answer and the way to use it never need a second screen.
 */
@Composable
private fun DestinationCard(
    task: DownloadTask,
    destinationLabel: String,
    onOpen: () -> Unit,
    onOpenFolder: () -> Unit,
    onCopyPath: () -> Unit,
    onShare: () -> Unit,
) {
    val n13 = LocalN13Colors.current
    SectionStart(R.string.section_location)
    N13Card {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = N13Icons.Folder,
                    contentDescription = null,
                    tint = n13.accent,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(10.dp))
                // The destination is a location, so it reads left-to-right.
                N13LtrText(
                    text = destinationLabel.ifBlank { N13Format.UNKNOWN },
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text1,
                    modifier = Modifier.weight(1f),
                )
            }

            if (task.resolvedPath.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                N13LtrText(
                    text = task.resolvedPath,
                    style = MaterialTheme.typography.labelSmall,
                    color = n13.text3,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(start = 26.dp),
                )
            }

            if (!task.isOpenable) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = stringResource(R.string.details_awaiting_file),
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.text3,
                    modifier = Modifier.padding(start = 26.dp),
                )
                return@Column
            }

            Spacer(Modifier.height(10.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(N13Shapes.small)
                    .background(n13.hover)
                    .padding(horizontal = 4.dp, vertical = 2.dp),
            ) {
                N13TextAction(
                    label = stringResource(R.string.action_open),
                    onClick = onOpen,
                    modifier = Modifier.weight(1f),
                )
                N13TextAction(
                    label = stringResource(R.string.action_share),
                    onClick = onShare,
                    modifier = Modifier.weight(1f),
                )
                N13TextAction(
                    label = stringResource(R.string.details_folder),
                    onClick = onOpenFolder,
                    modifier = Modifier.weight(1f),
                )
                N13TextAction(
                    label = stringResource(R.string.action_copy_path),
                    onClick = onCopyPath,
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

/**
 * A key/value row whose value is a long, single-token string.
 *
 * Ellipsised by default so it cannot distort the card, expandable to its full
 * wrapped form on tap so nothing is actually hidden, and copyable from the
 * expanded state.
 */
@Composable
private fun ExpandableRow(
    label: String,
    value: String,
    copyValue: String? = null,
) {
    val n13 = LocalN13Colors.current
    var expanded by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { expanded = !expanded }
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelMedium,
                color = n13.text3,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = stringResource(
                    if (expanded) R.string.action_collapse else R.string.action_expand,
                ),
                style = MaterialTheme.typography.labelSmall,
                color = n13.accent,
            )
        }
        Spacer(Modifier.height(3.dp))
        // A path, URL or ETag: a single technical token that must not be
        // reordered by the surrounding right-to-left context.
        N13LtrText(
            text = value,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                fontSize = 12.sp,
                lineHeight = 17.sp,
            ),
            color = n13.text1,
            maxLines = if (expanded) Int.MAX_VALUE else 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.heightIn(min = 16.dp),
        )
        AnimatedVisibility(visible = expanded && copyValue != null) {
            val context = LocalContext.current
            N13TextAction(
                label = stringResource(R.string.action_copy),
                onClick = {
                    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE)
                        as? android.content.ClipboardManager
                    clipboard?.setPrimaryClip(
                        android.content.ClipData.newPlainText("N13 value", copyValue.orEmpty()),
                    )
                },
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

@Composable
private fun ActionButtons(
    task: DownloadTask,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
    onRetry: () -> Unit,
    onOpen: () -> Unit,
    onShare: () -> Unit,
) {
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        when (task.status) {
            TaskStatus.DOWNLOADING,
            TaskStatus.STARTING,
            TaskStatus.ANALYZING,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
            -> {
                N13PrimaryButton(
                    label = stringResource(R.string.action_pause),
                    onClick = onPause,
                    icon = N13Icons.Pause,
                )
                N13SecondaryButton(
                    label = stringResource(R.string.action_cancel),
                    onClick = onCancel,
                    icon = N13Icons.XCircle,
                )
            }

            TaskStatus.PAUSED -> {
                N13PrimaryButton(
                    label = stringResource(R.string.action_resume),
                    onClick = onResume,
                    icon = N13Icons.Play,
                )
                N13SecondaryButton(
                    label = stringResource(R.string.action_cancel),
                    onClick = onCancel,
                    icon = N13Icons.XCircle,
                )
            }

            TaskStatus.QUEUED -> {
                N13PrimaryButton(
                    label = stringResource(R.string.action_start_now),
                    onClick = onResume,
                    icon = N13Icons.Bolt,
                )
                N13SecondaryButton(
                    label = stringResource(R.string.action_cancel),
                    onClick = onCancel,
                    icon = N13Icons.XCircle,
                )
            }

            TaskStatus.FAILED, TaskStatus.CANCELLED -> {
                N13PrimaryButton(
                    label = stringResource(R.string.action_retry),
                    onClick = onRetry,
                    icon = N13Icons.Retry,
                )
            }

            TaskStatus.COMPLETED -> {
                N13PrimaryButton(
                    label = stringResource(R.string.action_open),
                    onClick = onOpen,
                    icon = N13Icons.External,
                )
                N13SecondaryButton(
                    label = stringResource(R.string.action_share),
                    onClick = onShare,
                    icon = N13Icons.Link,
                )
            }

            TaskStatus.REMOVED -> Unit
        }
    }
}
