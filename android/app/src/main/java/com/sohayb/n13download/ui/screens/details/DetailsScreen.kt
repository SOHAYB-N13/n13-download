package com.sohayb.n13download.ui.screens.details

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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
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
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13ProgressBar
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.components.N13SectionLabel
import com.sohayb.n13download.ui.components.N13StatusBadge
import com.sohayb.n13download.ui.components.visual
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.util.FileActions
import com.sohayb.n13download.ui.util.absoluteTime
import kotlinx.coroutines.launch

/**
 * Properties — everything N13 knows about one download.
 *
 * Field set and actions follow the Windows Properties dialog: file, transfer,
 * server and error sections, with the state-appropriate controls at the bottom.
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
                title = "Properties",
                trailing = {
                N13IconButton(
                    icon = N13Icons.ChevronLeft,
                    contentDescription = "Back",
                    onClick = onBack,
                )
            },
        )
            Box(modifier = Modifier.weight(1f)) {
                N13EmptyState(
                    icon = N13Icons.Info,
                    title = "Not found",
                    message = "That download no longer exists.",
                    primaryLabel = "Back",
                    onPrimary = onBack,
                )
            }
        }
        return
    }

    val n13 = LocalN13Colors.current

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        N13PageHeader(
            title = "Properties",
            trailing = {
                if (current.isOpenable) {
                    N13IconButton(
                        icon = N13Icons.Link,
                        contentDescription = "Share",
                        onClick = { scope.launch { FileActions.share(context, manager, current) } },
                    )
                }
                N13IconButton(
                    icon = N13Icons.ChevronLeft,
                    contentDescription = "Back",
                    onClick = onBack,
                )
            },
        )

        Column(modifier = Modifier.padding(horizontal = 16.dp)) {
            // ---- Header card ------------------------------------------------
            N13Card {
                Column(modifier = Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = current.filename,
                            style = MaterialTheme.typography.titleMedium,
                            color = n13.text1,
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        N13StatusBadge(visual = current.status.visual())
                    }
                    if (current.hasKnownSize) {
                        Spacer(Modifier.height(12.dp))
                        N13ProgressBar(
                            fraction = (current.percent / 100.0).toFloat(),
                            label = "${current.percent.toInt()}%",
                        )
                        Spacer(Modifier.height(6.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(
                                text = N13Format.progressSize(current.downloadedSize, current.totalSize),
                                style = MaterialTheme.typography.labelSmall,
                                color = n13.text2,
                            )
                            if (current.status == TaskStatus.DOWNLOADING) {
                                Text(
                                    text = N13Format.formatSpeed(current.currentSpeed),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = n13.accent,
                                )
                            }
                        }
                    }
                }
            }

            // ---- Actions -----------------------------------------------------
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

            // ---- File ---------------------------------------------------------
            Spacer(Modifier.height(18.dp))
            N13SectionLabel(text = "File", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
            N13Card {
                N13InfoRow(label = "Name", value = current.filename)
                N13InfoRow(label = "Category", value = current.category)
                N13InfoRow(
                    label = "Destination",
                    value = destinationLabel.ifBlank { current.directory },
                    mono = true,
                )
                if (current.resolvedPath.isNotBlank()) {
                    N13InfoRow(label = "Path", value = current.resolvedPath, mono = true)
                }
                N13InfoRow(label = "Source URL", value = current.url, mono = true)
                N13InfoRow(
                    label = "Checksum",
                    value = current.checksum.ifBlank { N13Format.UNKNOWN },
                    mono = current.checksum.isNotBlank(),
                )
            }

            // ---- Transfer -----------------------------------------------------
            Spacer(Modifier.height(18.dp))
            N13SectionLabel(text = "Transfer", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
            N13Card {
                N13InfoRow(
                    label = "Size",
                    value = N13Format.progressSize(current.downloadedSize, current.totalSize.takeIf { it > 0 }),
                )
                N13InfoRow(
                    label = "Speed",
                    value = if (current.status == TaskStatus.DOWNLOADING) {
                        N13Format.formatSpeed(current.currentSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                )
                N13InfoRow(
                    label = "Average speed",
                    value = if (current.averageSpeed > 1.0) {
                        N13Format.formatSpeed(current.averageSpeed)
                    } else {
                        N13Format.UNKNOWN
                    },
                )
                N13InfoRow(label = "Time left", value = N13Format.formatEta(current.etaSeconds))
                N13InfoRow(label = "Elapsed", value = N13Format.formatDuration(current.elapsedSeconds))
                N13InfoRow(
                    label = "Connections",
                    value = if (current.segmentCount > 0) {
                        "${current.connections} of ${current.segmentCount}"
                    } else {
                        current.connections.toString()
                    },
                )
                N13InfoRow(label = "Priority", value = "${current.priority.label} (${current.priority.value})")
                N13InfoRow(
                    label = "Speed limit",
                    value = if (current.speedLimitBps > 0L) {
                        N13Format.formatSpeed(current.speedLimitBps.toDouble())
                    } else {
                        "Unlimited"
                    },
                )
                N13InfoRow(label = "Retries", value = current.retryCount.toString())
            }

            // ---- Server -------------------------------------------------------
            Spacer(Modifier.height(18.dp))
            N13SectionLabel(text = "Server", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
            N13Card {
                N13InfoRow(label = "Server", value = current.server.ifBlank { N13Format.UNKNOWN })
                N13InfoRow(
                    label = "Content type",
                    value = current.contentType.ifBlank { N13Format.UNKNOWN },
                    mono = true,
                )
                N13InfoRow(label = "Resumable", value = if (current.supportsRange) "Yes" else "No")
                N13InfoRow(label = "ETag", value = current.etag.ifBlank { N13Format.UNKNOWN }, mono = true)
                N13InfoRow(
                    label = "Last modified",
                    value = current.lastModified.ifBlank { N13Format.UNKNOWN },
                )
            }

            // ---- Timeline -----------------------------------------------------
            Spacer(Modifier.height(18.dp))
            N13SectionLabel(text = "Timeline", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
            N13Card {
                N13InfoRow(label = "Added", value = absoluteTime(current.createdAt))
                N13InfoRow(label = "Started", value = absoluteTime(current.startedAt))
                N13InfoRow(label = "Finished", value = absoluteTime(current.completedAt))
            }

            // ---- Error ---------------------------------------------------------
            if (current.error.isNotBlank()) {
                Spacer(Modifier.height(18.dp))
                N13SectionLabel(text = "Last error", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
                N13Card(borderColor = n13.danger.copy(alpha = 0.35f)) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        verticalAlignment = Alignment.Top,
                    ) {
                        Icon(
                            imageVector = N13Icons.Alert,
                            contentDescription = null,
                            tint = n13.danger,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(
                            text = current.error,
                            style = MaterialTheme.typography.bodyMedium,
                            color = n13.danger,
                        )
                    }
                }
            }

            // ---- Priority quick pick -------------------------------------------
            Spacer(Modifier.height(18.dp))
            N13SectionLabel(text = "Priority", modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf(
                    DownloadPriority.HIGH to DownloadPriority.HIGH_PRESET,
                    DownloadPriority.NORMAL to DownloadPriority.NORMAL_PRESET,
                    DownloadPriority.LOW to DownloadPriority.LOW_PRESET,
                ).forEach { (label, preset) ->
                    N13Chip(
                        label = "$label priority",
                        selected = current.priority.value == preset,
                        onClick = { viewModel.setPriority(DownloadPriority.of(preset)) },
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
            }

            // ---- Destructive ---------------------------------------------------
            Spacer(Modifier.height(18.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                N13SecondaryButton(
                    label = "Remove from list",
                    onClick = { confirmRemove = true },
                    modifier = Modifier.weight(1f),
                )
                if (current.isOpenable) {
                    N13SecondaryButton(
                        label = "Delete file",
                        onClick = { confirmDelete = true },
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            Spacer(Modifier.height(40.dp))
        }
    }

    if (confirmDelete) {
        N13ConfirmDialog(
            title = "Delete file",
            message = "Permanently delete \"${current.filename}\" from disk? This cannot be undone.",
            confirmLabel = "Delete",
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
            title = "Remove download",
            message = "Remove this entry from the list? The file on disk is kept.",
            confirmLabel = "Remove",
            onConfirm = {
                viewModel.remove(deleteFile = false)
                confirmRemove = false
                onBack()
            },
            onDismiss = { confirmRemove = false },
        )
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
    ) {
        when (task.status) {
            TaskStatus.DOWNLOADING,
            TaskStatus.STARTING,
            TaskStatus.ANALYZING,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
            -> {
                N13PrimaryButton("Pause", onPause, icon = N13Icons.Pause)
                N13SecondaryButton("Cancel", onCancel, icon = N13Icons.XCircle)
            }

            TaskStatus.PAUSED -> {
                N13PrimaryButton("Resume", onResume, icon = N13Icons.Play)
                N13SecondaryButton("Cancel", onCancel, icon = N13Icons.XCircle)
            }

            TaskStatus.QUEUED -> {
                N13PrimaryButton("Start now", onResume, icon = N13Icons.Bolt)
                N13SecondaryButton("Cancel", onCancel, icon = N13Icons.XCircle)
            }

            TaskStatus.FAILED, TaskStatus.CANCELLED -> {
                N13PrimaryButton("Retry", onRetry, icon = N13Icons.Retry)
            }

            TaskStatus.COMPLETED -> {
                N13PrimaryButton("Open", onOpen, icon = N13Icons.External)
                N13SecondaryButton("Share", onShare, icon = N13Icons.Link)
            }

            TaskStatus.REMOVED -> Unit
        }
    }
}
