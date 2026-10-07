package com.sohayb.n13download.ui.screens.settings

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.ConnectionMode
import com.sohayb.n13download.domain.model.DestinationKind
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.domain.model.DuplicatePolicy
import com.sohayb.n13download.domain.model.ThemeMode
import com.sohayb.n13download.ui.components.N13Card
import com.sohayb.n13download.ui.components.N13Chip
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13InfoRow
import com.sohayb.n13download.ui.components.N13PageHeader
import com.sohayb.n13download.ui.components.N13SectionLabel
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13AccentSwatches
import com.sohayb.n13download.ui.theme.N13PillShape
import com.sohayb.n13download.ui.theme.N13Shapes

/**
 * Settings.
 *
 * Every control here changes real behaviour — connection counts, the bandwidth
 * cap, retry budget, duplicate handling, the storage destination, notifications
 * and the theme.  There are no placeholder toggles.
 */
@Composable
fun SettingsScreen(
    manager: DownloadManager,
    modifier: Modifier = Modifier,
) {
    val factory = remember(manager) { SettingsViewModel.factory(manager) }
    val viewModel: SettingsViewModel = viewModel(factory = factory)
    val settings by viewModel.settings.collectAsStateWithLifecycle()

    val context = LocalContext.current
    val appInfo = remember(context) { readAppInfo(context) }

    val folderPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree(),
    ) { uri: Uri? ->
        if (uri != null) {
            // Persist the grant so downloads keep working after a restart.
            runCatching {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            }
            viewModel.setTreeUri(uri.toString())
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        N13PageHeader(title = "Settings", brandLine = "Tune N13 to your workflow")

        // ---- Storage --------------------------------------------------------
        SettingsGroup(title = "Storage") {
            ChoiceRow(
                label = "Destination",
                hint = "Where finished files are written.",
                options = listOf(
                    DestinationKind.APP to "App storage",
                    DestinationKind.MEDIA_STORE to "Downloads",
                    DestinationKind.TREE to "Chosen folder",
                ),
                selected = settings.destinationKind,
                onSelect = viewModel::setDestinationKind,
            )

            if (settings.destinationKind == DestinationKind.TREE) {
                SettingsRowContainer {
                    N13SecondaryButton(
                        label = "Choose folder",
                        onClick = { folderPicker.launch(null) },
                        icon = N13Icons.FolderOpen,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (settings.destinationUri.isNotBlank()) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            text = settings.destinationUri,
                            style = MaterialTheme.typography.bodySmall,
                            color = LocalN13Colors.current.text3,
                            maxLines = 2,
                        )
                    }
                }
            }

            SubfolderField(
                value = settings.destinationFolder,
                onValueChange = viewModel::setDestinationFolder,
            )

            N13InfoRow(
                label = "App storage path",
                value = appStoragePath(context),
                icon = N13Icons.Folder,
                mono = true,
            )
        }

        // ---- Downloads ------------------------------------------------------
        SettingsGroup(title = "Downloads") {
            StepperRow(
                label = "Simultaneous downloads",
                hint = "How many downloads run at the same time.",
                value = settings.maxConcurrent,
                range = DownloadSettings.MIN_CONCURRENT..DownloadSettings.MAX_CONCURRENT_LIMIT,
                onChange = viewModel::setMaxConcurrent,
            )
            StepperRow(
                label = "Connections per download",
                hint = "Used when the connection mode is Manual.",
                value = settings.numThreads,
                range = 1..DownloadSettings.MAX_THREADS,
                onChange = viewModel::setNumThreads,
            )
            ChoiceRow(
                label = "Connection mode",
                hint = "Smart adapts the connection count to the file and the server.",
                options = listOf(
                    ConnectionMode.SMART to "Smart",
                    ConnectionMode.MANUAL to "Manual",
                ),
                selected = settings.connectionMode,
                onSelect = viewModel::setConnectionMode,
            )
            StepperRow(
                label = "Smart max connections",
                hint = "Ceiling Smart mode will not exceed.",
                value = settings.smartMaxConnections,
                range = 1..32,
                onChange = viewModel::setSmartMax,
            )
            ToggleRow(
                label = "Adaptive scaling",
                hint = "Increase connections while throughput keeps improving.",
                checked = settings.smartAdaptive,
                onCheckedChange = viewModel::setSmartAdaptive,
            )
            ChoiceRow(
                label = "Duplicate handling",
                hint = "What to do when the file name already exists.",
                options = listOf(
                    DuplicatePolicy.ASK to "Ask",
                    DuplicatePolicy.ALLOW to "Allow",
                    DuplicatePolicy.RENAME to "Rename",
                    DuplicatePolicy.REPLACE to "Replace",
                ),
                selected = settings.duplicatePolicy,
                onSelect = viewModel::setDuplicatePolicy,
            )
            ToggleRow(
                label = "Auto-detect category",
                hint = "Pick the category from the file extension.",
                checked = settings.autoCategorize,
                onCheckedChange = viewModel::setAutoCategorize,
            )
            ToggleRow(
                label = "Start immediately",
                hint = "New downloads start as soon as a slot is free.",
                checked = settings.startImmediately,
                onCheckedChange = viewModel::setStartImmediately,
            )
            ToggleRow(
                label = "Resume on startup",
                hint = "Continue unfinished downloads when the app starts.",
                checked = settings.resumeOnStartup,
                onCheckedChange = viewModel::setResumeOnStartup,
            )
        }

        // ---- Bandwidth ------------------------------------------------------
        SettingsGroup(title = "Bandwidth") {
            SpeedLimitRow(
                current = settings.maxSpeedBps,
                onChange = viewModel::setSpeedLimit,
            )
        }

        // ---- Reliability ----------------------------------------------------
        SettingsGroup(title = "Reliability") {
            StepperRow(
                label = "Retry attempts",
                hint = "Tries per connection before a download fails.",
                value = settings.maxRetries,
                range = 0..20,
                onChange = viewModel::setMaxRetries,
            )
            ToggleRow(
                label = "Verify SSL certificates",
                hint = "Turn off only for servers with a self-signed certificate.",
                checked = settings.verifySsl,
                onCheckedChange = viewModel::setVerifySsl,
            )
            ToggleRow(
                label = "Verify file size",
                hint = "Check the byte count against Content-Length.",
                checked = settings.verifySize,
                onCheckedChange = viewModel::setVerifySize,
            )
            ToggleRow(
                label = "Block private addresses",
                hint = "Refuse localhost and LAN targets (SSRF protection).",
                checked = settings.blockPrivateUrls,
                onCheckedChange = viewModel::setBlockPrivateUrls,
            )
        }

        // ---- Notifications --------------------------------------------------
        SettingsGroup(title = "Notifications") {
            ToggleRow(
                label = "Notifications",
                hint = "Show progress and event notifications.",
                checked = settings.notificationsEnabled,
                onCheckedChange = viewModel::setNotificationsEnabled,
            )
            ToggleRow(
                label = "Notify on completion",
                checked = settings.notifyCompleted,
                onCheckedChange = viewModel::setNotifyCompleted,
                enabled = settings.notificationsEnabled,
            )
            ToggleRow(
                label = "Notify on failure",
                checked = settings.notifyFailed,
                onCheckedChange = viewModel::setNotifyFailed,
                enabled = settings.notificationsEnabled,
            )
            ToggleRow(
                label = "Notify on start",
                checked = settings.notifyStarted,
                onCheckedChange = viewModel::setNotifyStarted,
                enabled = settings.notificationsEnabled,
            )
        }

        // ---- Network --------------------------------------------------------
        SettingsGroup(title = "Network") {
            UserAgentRow(
                value = settings.userAgent,
                onChange = viewModel::setUserAgent,
                onReset = viewModel::resetUserAgent,
            )
        }

        // ---- Appearance -----------------------------------------------------
        SettingsGroup(title = "Appearance") {
            ChoiceRow(
                label = "Theme",
                options = listOf(
                    ThemeMode.DARK to "Dark",
                    ThemeMode.LIGHT to "Light",
                    ThemeMode.SYSTEM to "System",
                ),
                selected = settings.themeMode,
                onSelect = viewModel::setThemeMode,
            )
            AccentRow(
                current = settings.accentColor,
                onChange = viewModel::setAccentColor,
            )
        }

        // ---- About ----------------------------------------------------------
        SettingsGroup(title = "About") {
            N13InfoRow(label = "Application", value = "N13 Download Manager", icon = N13Icons.Info)
            N13InfoRow(label = "Version", value = appInfo.versionName, icon = N13Icons.Info)
            N13InfoRow(label = "Package", value = appInfo.packageName, icon = N13Icons.Cpu, mono = true)
            N13InfoRow(label = "Platform", value = appInfo.platform, icon = N13Icons.Server)
            N13InfoRow(
                label = "Storage",
                value = appInfo.storage,
                icon = N13Icons.Disk,
            )
        }

        Spacer(Modifier.height(36.dp))
    }
}

// ---------------------------------------------------------------------- //
// Building blocks
// ---------------------------------------------------------------------- //

@Composable
private fun SettingsGroup(title: String, content: @Composable () -> Unit) {
    Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
        N13SectionLabel(text = title, modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
        N13Card {
            Column { content() }
        }
    }
}

@Composable
private fun SettingsRowContainer(content: @Composable () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 10.dp),
    ) { content() }
}

@Composable
private fun <T> ChoiceRow(
    label: String,
    options: List<Pair<T, String>>,
    selected: T,
    onSelect: (T) -> Unit,
    hint: String? = null,
) {
    val n13 = LocalN13Colors.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        if (hint != null) {
            Spacer(Modifier.height(2.dp))
            Text(
                text = hint,
                style = MaterialTheme.typography.bodySmall,
                color = n13.text3,
            )
        }
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEach { (value, text) ->
                N13Chip(
                    label = text,
                    selected = value == selected,
                    onClick = { onSelect(value) },
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun StepperRow(
    label: String,
    value: Int,
    range: IntRange,
    onChange: (Int) -> Unit,
    hint: String? = null,
) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium,
                color = n13.text1,
            )
            if (hint != null) {
                Spacer(Modifier.height(2.dp))
                Text(
                    text = hint,
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.text3,
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        StepperButton("−", enabled = value > range.first) {
            onChange((value - 1).coerceIn(range.first, range.last))
        }
        Box(
            modifier = Modifier.width(46.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = value.toString(),
                style = MaterialTheme.typography.titleMedium,
                color = n13.text1,
            )
        }
        StepperButton("+", enabled = value < range.last) {
            onChange((value + 1).coerceIn(range.first, range.last))
        }
    }
}

@Composable
private fun StepperButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    val n13 = LocalN13Colors.current
    Box(
        modifier = Modifier
            .size(32.dp)
            .clip(N13Shapes.small)
            .background(n13.hover)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.titleMedium,
            color = if (enabled) n13.text1 else n13.text3,
        )
    }
}

@Composable
private fun ToggleRow(
    label: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    hint: String? = null,
    enabled: Boolean = true,
) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled) { onCheckedChange(!checked) }
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium,
                color = if (enabled) n13.text1 else n13.text3,
            )
            if (hint != null) {
                Spacer(Modifier.height(2.dp))
                Text(
                    text = hint,
                    style = MaterialTheme.typography.bodySmall,
                    color = n13.text3,
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        // Track
        Box(
            modifier = Modifier
                .width(44.dp)
                .height(24.dp)
                .clip(N13PillShape)
                .background(
                    when {
                        !enabled -> n13.active
                        checked -> n13.accent
                        else -> n13.active
                    },
                )
                .border(1.dp, if (checked && enabled) n13.accent else n13.borderStrong, N13PillShape)
                .padding(2.dp),
            contentAlignment = if (checked) Alignment.CenterEnd else Alignment.CenterStart,
        ) {
            Box(
                modifier = Modifier
                    .size(18.dp)
                    .clip(N13PillShape)
                    .background(if (enabled) n13.text1 else n13.text3),
            )
        }
    }
}

@Composable
private fun SpeedLimitRow(current: Long, onChange: (Long) -> Unit) {
    val n13 = LocalN13Colors.current
    val presets = listOf(
        "Unlimited" to 0L,
        "256 KB/s" to 256L * 1024,
        "512 KB/s" to 512L * 1024,
        "1 MB/s" to 1024L * 1024,
        "2 MB/s" to 2L * 1024 * 1024,
        "5 MB/s" to 5L * 1024 * 1024,
        "10 MB/s" to 10L * 1024 * 1024,
    )

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            text = "Download speed limit",
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = if (current <= 0L) {
                "Unlimited — no throttling applied."
            } else {
                "Currently capped at ${N13Format.formatSpeed(current.toDouble())}."
            },
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            presets.forEach { (label, value) ->
                N13Chip(
                    label = label,
                    selected = current == value,
                    onClick = { onChange(value) },
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun UserAgentRow(value: String, onChange: (String) -> Unit, onReset: () -> Unit) {
    val n13 = LocalN13Colors.current
    var draft by remember(value) { mutableStateOf(value) }
    val isDefault = value == BrowserHeaders.DEFAULT_USER_AGENT

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            text = "User agent",
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "Sent with every request. Some hosts reject unknown clients.",
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = draft,
            onValueChange = {
                draft = it
                if (it.isNotBlank()) onChange(it)
            },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            shape = N13Shapes.small,
            colors = TextFieldDefaults.colors(
                focusedContainerColor = n13.secondary,
                unfocusedContainerColor = n13.secondary,
                focusedIndicatorColor = n13.accent,
                unfocusedIndicatorColor = n13.borderStrong,
                focusedTextColor = n13.text1,
                unfocusedTextColor = n13.text1,
                cursorColor = n13.accent,
            ),
        )
        if (!isDefault) {
            Spacer(Modifier.height(8.dp))
            N13SecondaryButton(
                label = "Reset to N13 default",
                onClick = onReset,
                icon = N13Icons.Refresh,
            )
        }
    }
}

@Composable
private fun AccentRow(current: Long, onChange: (Long) -> Unit) {
    val n13 = LocalN13Colors.current

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            text = "Accent colour",
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "The N13 default is red. The whole UI follows this choice.",
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            N13AccentSwatches.forEach { swatch ->
                val argb = swatch.toArgb().toLong() and 0xFFFFFFFFL
                val selected = argb == current
                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .clip(N13PillShape)
                        .background(swatch)
                        .border(
                            width = if (selected) 2.dp else 1.dp,
                            color = if (selected) n13.text1 else n13.borderStrong,
                            shape = N13PillShape,
                        )
                        .clickable { onChange(argb) },
                )
            }
        }
    }
}

@Composable
private fun SubfolderField(value: String, onValueChange: (String) -> Unit) {
    val n13 = LocalN13Colors.current
    var draft by remember(value) { mutableStateOf(value) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            text = "Sub-folder",
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "Optional folder inside the destination. Leave empty for the root.",
            style = MaterialTheme.typography.bodySmall,
            color = n13.text3,
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = draft,
            onValueChange = {
                draft = it
                onValueChange(it)
            },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("N13") },
            singleLine = true,
            shape = N13Shapes.small,
            colors = TextFieldDefaults.colors(
                focusedContainerColor = n13.secondary,
                unfocusedContainerColor = n13.secondary,
                focusedIndicatorColor = n13.accent,
                unfocusedIndicatorColor = n13.borderStrong,
                focusedTextColor = n13.text1,
                unfocusedTextColor = n13.text1,
                cursorColor = n13.accent,
            ),
        )
    }
}

// ---------------------------------------------------------------------- //

private data class AppInfo(
    val versionName: String,
    val packageName: String,
    val platform: String,
    val storage: String,
)

private fun readAppInfo(context: Context): AppInfo {
    val packageInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        context.packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(0L))
    } else {
        @Suppress("DEPRECATION")
        context.packageManager.getPackageInfo(context.packageName, 0)
    }

    val freeBytes = runCatching {
        val stat = android.os.StatFs(context.filesDir.absolutePath)
        stat.availableBytes
    }.getOrDefault(0L)

    return AppInfo(
        versionName = packageInfo.versionName.orEmpty().ifBlank { "unknown" },
        packageName = context.packageName,
        platform = "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
        storage = "${N13Format.humanSize(freeBytes)} free",
    )
}

private fun appStoragePath(context: Context): String =
    (context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)
        ?: context.filesDir).absolutePath
