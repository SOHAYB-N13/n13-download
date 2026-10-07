package com.sohayb.n13download.ui.util

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.model.DownloadPriority

/**
 * The translated name of a priority level.
 *
 * [DownloadPriority] is a domain value class whose level names are English
 * identifiers (`DownloadPriority.HIGH` is the string `"High"`), so the
 * translation lives here rather than in the model. An unrecognised key is passed
 * through untouched.
 */
@Composable
fun priorityLabel(key: String): String = when (key) {
    DownloadPriority.HIGH -> stringResource(R.string.priority_high)
    DownloadPriority.NORMAL -> stringResource(R.string.priority_normal)
    DownloadPriority.LOW -> stringResource(R.string.priority_low)
    else -> key
}

/** The translated name for a priority value. */
@Composable
fun DownloadPriority.localizedLabel(): String = priorityLabel(label)

/** The "… priority" chip text. */
@Composable
fun priorityChipLabel(key: String): String =
    stringResource(R.string.priority_chip, priorityLabel(key))
