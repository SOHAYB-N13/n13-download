package com.sohayb.n13download.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.model.DownloadTask
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes

/** The inline actions available on a row, already resolved for its state. */
data class DownloadRowActions(
    val onPause: () -> Unit = {},
    val onResume: () -> Unit = {},
    val onCancel: () -> Unit = {},
    val onRetry: () -> Unit = {},
    val onOpen: () -> Unit = {},
    val onShare: () -> Unit = {},
    val onMore: () -> Unit = {},
)

/**
 * One download in the list.
 *
 * The field set is the N13 desktop row (file type, name, host, progress with the
 * percentage inside the bar, downloaded/total, speed, ETA, status badge) packed
 * onto a phone-width card, and the inline actions are the same state-dependent
 * set the Windows row exposes.
 */
@Composable
fun DownloadRow(
    task: DownloadTask,
    modifier: Modifier = Modifier,
    onClick: () -> Unit = {},
    actions: DownloadRowActions = DownloadRowActions(),
) {
    val n13 = LocalN13Colors.current
    val fileType = fileTypeVisual(task.filename, task.contentType)
    val status = task.status.visual()

    N13Card(modifier = modifier, onClick = onClick) {
        Column(modifier = Modifier.padding(start = 14.dp, end = 14.dp, top = 13.dp, bottom = 8.dp)) {
            // ---- Header: type icon, name, host, status ----------------------
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(38.dp)
                        .clip(N13Shapes.small)
                        .background(fileType.color.copy(alpha = 0.13f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = fileType.icon,
                        contentDescription = null,
                        tint = fileType.color,
                        modifier = Modifier.size(19.dp),
                    )
                }

                Spacer(Modifier.width(12.dp))

                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = task.filename,
                        style = MaterialTheme.typography.titleSmall,
                        color = n13.text1,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = secondaryLine(task),
                        style = MaterialTheme.typography.bodySmall,
                        color = n13.text3,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Spacer(Modifier.width(8.dp))
                N13StatusBadge(visual = status)
            }

            // ---- Progress ---------------------------------------------------
            if (showProgress(task)) {
                Spacer(Modifier.height(11.dp))
                val barColor = progressColor(task)
                N13ProgressBar(
                    fraction = if (task.hasKnownSize) (task.percent / 100.0).toFloat() else null,
                    color = barColor,
                    colorHi = if (task.status == TaskStatus.DOWNLOADING) n13.accentHi else barColor,
                    label = if (task.hasKnownSize) "${task.percent.toInt()}%" else null,
                )
                Spacer(Modifier.height(7.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = N13Format.progressSize(task.downloadedSize, task.totalSize.takeIf { it > 0 }),
                        style = MaterialTheme.typography.labelSmall,
                        color = n13.text2,
                        maxLines = 1,
                    )
                    Text(
                        text = rightMeta(task),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (task.status == TaskStatus.DOWNLOADING) n13.accent else n13.text3,
                        maxLines = 1,
                    )
                }
            } else {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = plainMeta(task),
                    style = MaterialTheme.typography.labelSmall,
                    color = n13.text3,
                    maxLines = 1,
                )
            }

            // ---- Error -------------------------------------------------------
            if (task.error.isNotBlank() && task.status == TaskStatus.FAILED) {
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.Top) {
                    Icon(
                        imageVector = N13Icons.Alert,
                        contentDescription = null,
                        tint = n13.danger,
                        modifier = Modifier.size(13.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = task.error,
                        style = MaterialTheme.typography.bodySmall,
                        color = n13.danger,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            // ---- Inline actions ----------------------------------------------
            RowActions(task = task, actions = actions)
        }
    }
}

@Composable
private fun RowActions(task: DownloadTask, actions: DownloadRowActions) {
    val n13 = LocalN13Colors.current
    val items = buildList {
        when (task.status) {
            TaskStatus.DOWNLOADING,
            TaskStatus.STARTING,
            TaskStatus.ANALYZING,
            TaskStatus.MERGING,
            TaskStatus.VERIFYING,
            -> {
                add(Triple("Pause", N13Icons.Pause, actions.onPause))
                add(Triple("Cancel", N13Icons.XCircle, actions.onCancel))
            }

            TaskStatus.PAUSED -> {
                add(Triple("Resume", N13Icons.Play, actions.onResume))
                add(Triple("Cancel", N13Icons.XCircle, actions.onCancel))
            }

            TaskStatus.QUEUED -> {
                add(Triple("Start now", N13Icons.Bolt, actions.onResume))
                add(Triple("Cancel", N13Icons.XCircle, actions.onCancel))
            }

            TaskStatus.FAILED, TaskStatus.CANCELLED -> {
                add(Triple("Retry", N13Icons.Retry, actions.onRetry))
            }

            TaskStatus.COMPLETED -> {
                add(Triple("Open", N13Icons.External, actions.onOpen))
                add(Triple("Share", N13Icons.Link, actions.onShare))
            }

            TaskStatus.REMOVED -> Unit
        }
        add(Triple("More", N13Icons.More, actions.onMore))
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.End,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items.forEach { (label, icon, action) ->
            N13RowAction(
                label = label,
                icon = icon,
                onClick = action,
                danger = label == "Cancel",
                modifier = Modifier.padding(start = 2.dp),
            )
        }
    }
}

/** `cdn.example.com · Archives` — the N13 row subtitle. */
private fun secondaryLine(task: DownloadTask): String {
    val host = com.sohayb.n13download.core.FilenameResolver.hostOf(task.url)
    return listOf(host, task.category).filter { it.isNotBlank() }.joinToString(" · ")
}

private fun showProgress(task: DownloadTask): Boolean = when (task.status) {
    TaskStatus.QUEUED -> false
    TaskStatus.CANCELLED, TaskStatus.REMOVED -> false
    TaskStatus.COMPLETED -> task.hasKnownSize
    else -> true
}

@Composable
private fun progressColor(task: DownloadTask) = when (task.status) {
    TaskStatus.COMPLETED -> LocalN13Colors.current.success
    TaskStatus.FAILED -> LocalN13Colors.current.danger
    TaskStatus.PAUSED -> LocalN13Colors.current.warning
    else -> LocalN13Colors.current.accent
}

/** Speed / ETA / elapsed, depending on what the task is doing. */
private fun rightMeta(task: DownloadTask): String = when (task.status) {
    TaskStatus.DOWNLOADING, TaskStatus.STARTING ->
        listOf(
            N13Format.formatSpeed(task.currentSpeed),
            N13Format.formatEta(task.etaSeconds),
        ).filter { it != N13Format.UNKNOWN }.joinToString(" · ")

    TaskStatus.PAUSED -> "Paused · " + N13Format.formatDuration(task.elapsedSeconds)
    TaskStatus.COMPLETED -> N13Format.formatDuration(task.elapsedSeconds)
    TaskStatus.FAILED -> "Stopped at ${task.percent.toInt()}%"
    else -> N13Format.formatEta(task.etaSeconds)
}

private fun plainMeta(task: DownloadTask): String {
    val parts = mutableListOf<String>()
    if (task.hasKnownSize) parts += N13Format.humanSize(task.totalSize)
    parts += relativeTime(task.completedAt ?: task.createdAt)
    if (task.connections > 1) parts += "${task.connections} conn"
    return parts.filter { it.isNotBlank() }.joinToString(" · ")
}

private fun relativeTime(epochMillis: Long): String =
    if (epochMillis <= 0L) "" else com.sohayb.n13download.ui.util.relativeTime(epochMillis)
