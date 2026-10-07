package com.sohayb.n13download.ui.screens.adddownload

import android.content.ClipboardManager
import android.content.Context
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
import androidx.compose.foundation.text.KeyboardActions
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.core.CategoryDetector
import com.sohayb.n13download.core.FilenameResolver
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.download.DuplicateChoice
import com.sohayb.n13download.domain.download.DuplicateKind
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.ui.components.N13Card
import com.sohayb.n13download.ui.components.N13Chip
import com.sohayb.n13download.ui.components.N13ConfirmDialog
import com.sohayb.n13download.ui.components.N13IconButton
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13InfoRow
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13SectionLabel
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.components.N13TextAction
import com.sohayb.n13download.ui.components.categoryIcon
import com.sohayb.n13download.ui.components.fileTypeVisual
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13PillShape
import com.sohayb.n13download.ui.theme.N13Shapes

/**
 * Add Download.
 *
 * The full N13 workflow: paste a link, let N13 inspect it, show what it found,
 * let the user adjust the name, category and options, and only then queue it.
 * Nothing irreversible happens before the confirmation.
 */
@Composable
fun AddDownloadScreen(
    manager: DownloadManager,
    prefillUrl: String?,
    onDone: () -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager, prefillUrl) {
        AddDownloadViewModel.factory(manager, prefillUrl)
    }
    val viewModel: AddDownloadViewModel = viewModel(factory = factory)
    val state by viewModel.state.collectAsStateWithLifecycle()

    LaunchedEffect(state.done) {
        if (state.done) onDone()
    }

    val context = LocalContext.current
    val clipboardLink = remember { readClipboardLink(context) }
    val focusRequester = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        if (state.url.isBlank()) focusRequester.requestFocus()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .imePadding(),
    ) {
        // ---- Header -------------------------------------------------------
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 6.dp, end = 16.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            N13IconButton(
                icon = N13Icons.ChevronLeft,
                contentDescription = "Back",
                onClick = onCancel,
            )
            Spacer(Modifier.width(4.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "New download",
                    style = MaterialTheme.typography.headlineSmall,
                    color = LocalN13Colors.current.text1,
                )
                Text(
                    text = "Add a file by URL",
                    style = MaterialTheme.typography.bodySmall,
                    color = LocalN13Colors.current.text3,
                )
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp),
        ) {
            // ---- Link ------------------------------------------------------
            OutlinedTextField(
                value = state.url,
                onValueChange = viewModel::onUrlChange,
                modifier = Modifier
                    .fillMaxWidth()
                    .focusRequester(focusRequester),
                label = { Text("Link") },
                placeholder = { Text("https://example.com/archive.zip") },
                singleLine = true,
                isError = state.error != null,
                leadingIcon = {
                    Icon(
                        imageVector = N13Icons.Link,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                    )
                },
                trailingIcon = {
                    if (clipboardLink != null) {
                        N13IconButton(
                            icon = N13Icons.Paste,
                            contentDescription = "Paste link from clipboard",
                            onClick = { viewModel.onUrlChange(clipboardLink) },
                            size = 36.dp,
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { viewModel.submit() }),
                shape = N13Shapes.small,
                colors = n13TextFieldColors(),
            )

            if (state.error != null) {
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = N13Icons.Alert,
                        contentDescription = null,
                        tint = LocalN13Colors.current.danger,
                        modifier = Modifier.size(13.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = state.error.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = LocalN13Colors.current.danger,
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            // ---- Detection card --------------------------------------------
            DetectionCard(state = state)

            Spacer(Modifier.height(18.dp))

            // ---- File name -------------------------------------------------
            N13SectionLabel(text = "File name", modifier = Modifier.padding(bottom = 6.dp))
            OutlinedTextField(
                value = state.filename,
                onValueChange = viewModel::onFilenameChange,
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("Detected automatically") },
                singleLine = true,
                shape = N13Shapes.small,
                colors = n13TextFieldColors(),
            )

            Spacer(Modifier.height(18.dp))

            // ---- Category ---------------------------------------------------
            N13SectionLabel(text = "Category", modifier = Modifier.padding(bottom = 8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CategoryDetector.ORDER.forEach { category ->
                    N13Chip(
                        label = category,
                        icon = categoryIcon(category),
                        selected = state.category == category,
                        onClick = {
                            viewModel.onCategoryChange(
                                if (state.category == category) null else category,
                            )
                        },
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
            }

            Spacer(Modifier.height(10.dp))

            // ---- Destination ------------------------------------------------
            N13SectionLabel(text = "Save to", modifier = Modifier.padding(bottom = 8.dp))
            N13Card {
                N13InfoRow(
                    label = "Destination",
                    value = destinationLabel(state.settings.destinationKind, state.settings.destinationFolder),
                    icon = N13Icons.Folder,
                    mono = true,
                )
                Text(
                    text = "Change the download folder in Settings.",
                    style = MaterialTheme.typography.bodySmall,
                    color = LocalN13Colors.current.text3,
                    modifier = Modifier.padding(start = 42.dp, end = 14.dp, bottom = 12.dp),
                )
            }

            Spacer(Modifier.height(18.dp))

            // ---- Advanced ---------------------------------------------------
            N13TextAction(
                label = if (state.advancedExpanded) "Hide advanced options" else "Advanced options",
                onClick = viewModel::toggleAdvanced,
                color = LocalN13Colors.current.text2,
                modifier = Modifier.padding(horizontal = 0.dp),
            )

            if (state.advancedExpanded) {
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = state.checksum,
                    onValueChange = viewModel::onChecksumChange,
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Checksum") },
                    placeholder = { Text("MD5 or SHA-256, optional") },
                    singleLine = true,
                    isError = state.checksum.isNotBlank() && !isValidChecksum(state.checksum),
                    shape = N13Shapes.small,
                    colors = n13TextFieldColors(),
                )
                if (state.checksum.isNotBlank() && !isValidChecksum(state.checksum)) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = "A checksum must be 32 (MD5) or 64 (SHA-256) hex characters",
                        style = MaterialTheme.typography.bodySmall,
                        color = LocalN13Colors.current.danger,
                    )
                }

                Spacer(Modifier.height(14.dp))
                StartImmediatelyToggle(
                    checked = state.startNow,
                    onCheckedChange = viewModel::onStartNowChange,
                )
            }

            Spacer(Modifier.height(28.dp))
        }

        // ---- Action bar ----------------------------------------------------
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            N13SecondaryButton(label = "Cancel", onClick = onCancel)
            Spacer(Modifier.width(10.dp))
            N13PrimaryButton(
                label = if (state.adding) "Adding…" else if (state.startNow) "Download now" else "Add to queue",
                onClick = { viewModel.submit() },
                icon = N13Icons.Download,
                enabled = state.canSubmit,
            )
        }
    }

    // ---- Duplicate conflict ------------------------------------------------
    val conflict = state.conflict
    if (conflict != null) {
        DuplicateConflictDialog(
            conflict = conflict,
            onChoice = { choice -> viewModel.submit(choice) },
            onDismiss = viewModel::dismissConflict,
        )
    }
}

/**
 * The detection card, matching N13's `.nd-detect`: file icon, resolved filename,
 * size and host, and a green "Resumable" pill only when the server really
 * supports byte ranges.
 */
@Composable
private fun DetectionCard(state: AddDownloadUiState) {
    val n13 = LocalN13Colors.current

    if (state.url.isBlank()) return

    N13Card(
        containerColor = n13.secondary,
        borderColor = n13.border,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val analysis = state.analysis
            if (state.probing) {
                Icon(
                    imageVector = N13Icons.Search,
                    contentDescription = null,
                    tint = n13.text3,
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(12.dp))
                Text(
                    text = "Inspecting link…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                )
                return@Row
            }

            if (analysis == null || !analysis.ok) {
                Icon(
                    imageVector = N13Icons.Info,
                    contentDescription = null,
                    tint = n13.text3,
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(12.dp))
                Text(
                    text = "Could not inspect this link",
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                )
                return@Row
            }

            val fileType = fileTypeVisual(analysis.filename, analysis.contentType)
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(N13Shapes.small)
                    .background(fileType.color.copy(alpha = 0.13f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = fileType.icon,
                    contentDescription = null,
                    tint = fileType.color,
                    modifier = Modifier.size(18.dp),
                )
            }

            Spacer(Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = state.filename.ifBlank { analysis.filename },
                    style = MaterialTheme.typography.titleSmall,
                    color = n13.text1,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = analysisMeta(analysis),
                        style = MaterialTheme.typography.bodySmall,
                        color = n13.text3,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (analysis.resumable) {
                        Spacer(Modifier.width(8.dp))
                        Box(
                            modifier = Modifier
                                .clip(N13PillShape)
                                .background(n13.successSoft)
                                .padding(horizontal = 7.dp, vertical = 2.dp),
                        ) {
                            Text(
                                text = "Resumable",
                                style = MaterialTheme.typography.labelSmall,
                                color = n13.success,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StartImmediatelyToggle(checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(N13Shapes.small)
            .background(n13.secondary)
            .clickable { onCheckedChange(!checked) }
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
                text = "Start immediately",
                style = MaterialTheme.typography.bodyMedium,
                color = n13.text1,
            )
            Text(
                text = if (checked) {
                    "Begins as soon as a queue slot is free"
                } else {
                    "Off — added to the queue without starting"
                },
                style = MaterialTheme.typography.bodySmall,
                color = n13.text3,
            )
        }
        Box(
            modifier = Modifier
                .size(20.dp)
                .clip(N13PillShape)
                .background(if (checked) n13.accent else n13.active)
                .border(1.dp, if (checked) n13.accent else n13.borderStrong, N13PillShape),
        )
    }
}

/** The N13 duplicate dialog: same wording, same choices. */
@Composable
private fun DuplicateConflictDialog(
    conflict: ConflictPrompt,
    onChoice: (DuplicateChoice) -> Unit,
    onDismiss: () -> Unit,
) {
    val n13 = LocalN13Colors.current
    val isActive = conflict.kind == DuplicateKind.SAME_URL_ACTIVE

    androidx.compose.ui.window.Dialog(onDismissRequest = onDismiss) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(N13Shapes.large)
                .background(n13.elevated)
                .padding(20.dp),
        ) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = N13Icons.Alert,
                        contentDescription = null,
                        tint = n13.warning,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = if (isActive) "Already downloading" else "Duplicate detected",
                        style = MaterialTheme.typography.titleMedium,
                        color = n13.text1,
                    )
                }

                Spacer(Modifier.height(10.dp))

                Text(
                    text = if (isActive) {
                        "This URL is already in your download queue."
                    } else {
                        "A file named \"${conflict.filename}\" already exists in the destination."
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                )

                Spacer(Modifier.height(18.dp))

                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (isActive) {
                        N13PrimaryButton(
                            label = "Open existing",
                            onClick = { onChoice(DuplicateChoice.OPEN_EXISTING) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        N13SecondaryButton(
                            label = "Download anyway",
                            onClick = { onChoice(DuplicateChoice.DOWNLOAD_ANYWAY) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    } else {
                        N13PrimaryButton(
                            label = "Rename automatically",
                            onClick = { onChoice(DuplicateChoice.RENAME) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        N13SecondaryButton(
                            label = "Download anyway",
                            onClick = { onChoice(DuplicateChoice.DOWNLOAD_ANYWAY) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        N13SecondaryButton(
                            label = "Replace existing",
                            onClick = { onChoice(DuplicateChoice.REPLACE) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    N13SecondaryButton(
                        label = "Cancel",
                        onClick = onDismiss,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}

@Composable
private fun n13TextFieldColors() = TextFieldDefaults.colors(
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

private fun analysisMeta(analysis: DownloadAnalysis): String {
    val size = if (analysis.hasKnownSize) N13Format.humanSize(analysis.totalSize) else "Unknown size"
    val host = FilenameResolver.hostOf(analysis.finalUrl.ifBlank { analysis.url })
    return listOf(size, host).filter { it.isNotBlank() }.joinToString(" · ")
}

private fun destinationLabel(kind: DestinationKind, folder: String): String {
    val base = when (kind) {
        DestinationKind.APP -> "App storage"
        DestinationKind.MEDIA_STORE -> "Downloads"
        DestinationKind.TREE -> "Chosen folder"
    }
    return if (folder.isBlank()) base else "$base/$folder"
}

private fun isValidChecksum(value: String): Boolean {
    val clean = value.trim()
    val hex = clean.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' }
    return hex && (clean.length == 32 || clean.length == 64)
}

/** Only offers paste when the clipboard actually holds a link. */
private fun readClipboardLink(context: Context): String? {
    val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return null
    val clip = manager.primaryClip ?: return null
    if (clip.itemCount == 0) return null
    val text = clip.getItemAt(0)?.coerceToText(context)?.toString()?.trim().orEmpty()
    return text.takeIf {
        it.startsWith("http://", ignoreCase = true) || it.startsWith("https://", ignoreCase = true)
    }
}
