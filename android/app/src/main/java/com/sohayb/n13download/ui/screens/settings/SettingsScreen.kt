package com.sohayb.n13download.ui.screens.settings

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sohayb.n13download.R
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.core.N13Format
import com.sohayb.n13download.domain.download.DownloadManager
import com.sohayb.n13download.domain.model.AppLanguage
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
 * cap, retry budget, duplicate handling, the storage destination, notifications,
 * the theme and the interface language.  There are no placeholder toggles.
 *
 * All prose comes from string resources so the screen follows the chosen
 * language; the paths and byte counts shown alongside it do not, because they
 * are data.
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
        N13PageHeader(
            titleRes = R.string.settings_title,
            brandLineRes = R.string.settings_brand_line,
        )

        // ---- Storage --------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_storage)) {
            ChoiceRow(
                label = stringResource(R.string.settings_destination),
                hint = stringResource(R.string.settings_destination_hint),
                options = listOf(
                    DestinationKind.APP to stringResource(R.string.dest_app_storage),
                    DestinationKind.MEDIA_STORE to stringResource(R.string.dest_downloads),
                    DestinationKind.TREE to stringResource(R.string.dest_chosen_folder),
                ),
                selected = settings.destinationKind,
                onSelect = viewModel::setDestinationKind,
            )

            if (settings.destinationKind == DestinationKind.TREE) {
                SettingsRowContainer {
                    N13SecondaryButton(
                        label = stringResource(R.string.settings_choose_folder),
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
                label = stringResource(R.string.settings_app_storage_path),
                value = appStoragePath(context),
                icon = N13Icons.Folder,
                mono = true,
                ltr = true,
            )
        }

        // ---- Downloads ------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_downloads)) {
            StepperRow(
                label = stringResource(R.string.settings_simultaneous),
                hint = stringResource(R.string.settings_simultaneous_hint),
                value = settings.maxConcurrent,
                range = DownloadSettings.MIN_CONCURRENT..DownloadSettings.MAX_CONCURRENT_LIMIT,
                onChange = viewModel::setMaxConcurrent,
            )
            StepperRow(
                label = stringResource(R.string.settings_connections),
                hint = stringResource(R.string.settings_connections_hint),
                value = settings.numThreads,
                range = 1..DownloadSettings.MAX_THREADS,
                onChange = viewModel::setNumThreads,
            )
            ChoiceRow(
                label = stringResource(R.string.settings_connection_mode),
                hint = stringResource(R.string.settings_connection_mode_hint),
                options = listOf(
                    ConnectionMode.SMART to stringResource(R.string.connection_smart),
                    ConnectionMode.MANUAL to stringResource(R.string.connection_manual),
                ),
                selected = settings.connectionMode,
                onSelect = viewModel::setConnectionMode,
            )
            StepperRow(
                label = stringResource(R.string.settings_smart_max),
                hint = stringResource(R.string.settings_smart_max_hint),
                value = settings.smartMaxConnections,
                range = 1..32,
                onChange = viewModel::setSmartMax,
            )
            ToggleRow(
                label = stringResource(R.string.settings_adaptive),
                hint = stringResource(R.string.settings_adaptive_hint),
                checked = settings.smartAdaptive,
                onCheckedChange = viewModel::setSmartAdaptive,
            )
            ChoiceRow(
                label = stringResource(R.string.settings_duplicate_handling),
                hint = stringResource(R.string.settings_duplicate_hint),
                options = listOf(
                    DuplicatePolicy.ASK to stringResource(R.string.dup_ask),
                    DuplicatePolicy.ALLOW to stringResource(R.string.dup_allow),
                    DuplicatePolicy.RENAME to stringResource(R.string.dup_rename),
                    DuplicatePolicy.REPLACE to stringResource(R.string.dup_replace),
                ),
                selected = settings.duplicatePolicy,
                onSelect = viewModel::setDuplicatePolicy,
            )
            ToggleRow(
                label = stringResource(R.string.settings_auto_category),
                hint = stringResource(R.string.settings_auto_category_hint),
                checked = settings.autoCategorize,
                onCheckedChange = viewModel::setAutoCategorize,
            )
            ToggleRow(
                label = stringResource(R.string.settings_start_immediately),
                hint = stringResource(R.string.settings_start_immediately_hint),
                checked = settings.startImmediately,
                onCheckedChange = viewModel::setStartImmediately,
            )
            ToggleRow(
                label = stringResource(R.string.settings_resume_on_startup),
                hint = stringResource(R.string.settings_resume_on_startup_hint),
                checked = settings.resumeOnStartup,
                onCheckedChange = viewModel::setResumeOnStartup,
            )
        }

        // ---- Bandwidth ------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_bandwidth)) {
            SpeedLimitRow(
                current = settings.maxSpeedBps,
                onChange = viewModel::setSpeedLimit,
            )
        }

        // ---- Reliability ----------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_reliability)) {
            StepperRow(
                label = stringResource(R.string.settings_retry_attempts),
                hint = stringResource(R.string.settings_retry_attempts_hint),
                value = settings.maxRetries,
                range = 0..20,
                onChange = viewModel::setMaxRetries,
            )
            ToggleRow(
                label = stringResource(R.string.settings_verify_ssl),
                hint = stringResource(R.string.settings_verify_ssl_hint),
                checked = settings.verifySsl,
                onCheckedChange = viewModel::setVerifySsl,
            )
            ToggleRow(
                label = stringResource(R.string.settings_verify_size),
                hint = stringResource(R.string.settings_verify_size_hint),
                checked = settings.verifySize,
                onCheckedChange = viewModel::setVerifySize,
            )
            ToggleRow(
                label = stringResource(R.string.settings_block_private),
                hint = stringResource(R.string.settings_block_private_hint),
                checked = settings.blockPrivateUrls,
                onCheckedChange = viewModel::setBlockPrivateUrls,
            )
        }

        // ---- Notifications --------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_notifications)) {
            ToggleRow(
                label = stringResource(R.string.settings_notifications),
                hint = stringResource(R.string.settings_notifications_hint),
                checked = settings.notificationsEnabled,
                onCheckedChange = viewModel::setNotificationsEnabled,
            )
            ToggleRow(
                label = stringResource(R.string.settings_notify_completed),
                checked = settings.notifyCompleted,
                onCheckedChange = viewModel::setNotifyCompleted,
                enabled = settings.notificationsEnabled,
            )
            ToggleRow(
                label = stringResource(R.string.settings_notify_failed),
                checked = settings.notifyFailed,
                onCheckedChange = viewModel::setNotifyFailed,
                enabled = settings.notificationsEnabled,
            )
            ToggleRow(
                label = stringResource(R.string.settings_notify_started),
                checked = settings.notifyStarted,
                onCheckedChange = viewModel::setNotifyStarted,
                enabled = settings.notificationsEnabled,
            )
        }

        // ---- Network --------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_network)) {
            UserAgentRow(
                value = settings.userAgent,
                onChange = viewModel::setUserAgent,
                onReset = viewModel::resetUserAgent,
            )
        }

        // ---- Appearance -----------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_appearance)) {
            ChoiceRow(
                label = stringResource(R.string.settings_theme),
                options = listOf(
                    ThemeMode.DARK to stringResource(R.string.theme_dark),
                    ThemeMode.LIGHT to stringResource(R.string.theme_light),
                    ThemeMode.SYSTEM to stringResource(R.string.theme_system),
                ),
                selected = settings.themeMode,
                onSelect = viewModel::setThemeMode,
            )
            AccentRow(
                current = settings.accentColor,
                onChange = viewModel::setAccentColor,
            )
        }

        // ---- Language -------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_language)) {
            ChoiceRow(
                label = stringResource(R.string.settings_language),
                hint = stringResource(R.string.settings_language_hint),
                options = AppLanguage.entries.map { it to it.nativeName },
                selected = settings.language,
                onSelect = { language ->
                    // Persist first, then rebuild the UI so the new language is
                    // applied from the very next frame.
                    viewModel.setLanguage(language)
                    context.findActivity()?.let { AppLocale.switchTo(it, language) }
                },
            )
        }

        // ---- About ----------------------------------------------------------
        SettingsGroup(title = stringResource(R.string.settings_group_about)) {
            N13InfoRow(
                label = stringResource(R.string.about_application),
                value = stringResource(R.string.app_name),
                icon = N13Icons.Info,
            )
            N13InfoRow(
                label = stringResource(R.string.about_version),
                value = appInfo.versionName,
                icon = N13Icons.Info,
                ltr = true,
            )
            N13InfoRow(
                label = stringResource(R.string.about_package),
                value = appInfo.packageName,
                icon = N13Icons.Cpu,
                mono = true,
                ltr = true,
            )
            N13InfoRow(
                label = stringResource(R.string.about_platform),
                value = stringResource(
                    R.string.about_platform_value,
                    appInfo.release,
                    appInfo.apiLevel,
                ),
                icon = N13Icons.Server,
            )
            N13InfoRow(
                label = stringResource(R.string.about_storage),
                value = stringResource(
                    R.string.about_storage_free,
                    N13Format.humanSize(appInfo.freeBytes),
                ),
                icon = N13Icons.Disk,
                ltr = true,
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
            // A count is a technical value: always left-to-right.
            com.sohayb.n13download.ui.components.N13LtrText(
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
        stringResource(R.string.settings_unlimited) to 0L,
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
            text = stringResource(R.string.settings_speed_limit),
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = if (current <= 0L) {
                stringResource(R.string.settings_speed_unlimited_hint)
            } else {
                stringResource(
                    R.string.settings_speed_capped_hint,
                    N13Format.formatSpeed(current.toDouble()),
                )
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
            text = stringResource(R.string.settings_user_agent),
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = stringResource(R.string.settings_user_agent_hint),
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
                label = stringResource(R.string.settings_reset_agent),
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
            text = stringResource(R.string.settings_accent),
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = stringResource(R.string.settings_accent_hint),
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
            text = stringResource(R.string.settings_subfolder),
            style = MaterialTheme.typography.bodyMedium,
            color = n13.text1,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = stringResource(R.string.settings_subfolder_hint),
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
    val release: String,
    val apiLevel: Int,
    val freeBytes: Long,
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
        // A missing version is a value, not prose, so it stays untranslated here
        // and the row renders the em dash the rest of the app uses.
        versionName = packageInfo.versionName.orEmpty().ifBlank { N13Format.UNKNOWN },
        packageName = context.packageName,
        release = Build.VERSION.RELEASE,
        apiLevel = Build.VERSION.SDK_INT,
        freeBytes = freeBytes,
    )
}

private fun appStoragePath(context: Context): String =
    (context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)
        ?: context.filesDir).absolutePath

/**
 * The Activity hosting the current composition.
 *
 * Needed because switching language has to rebuild the Activity, and Compose's
 * context is often a wrapper around it rather than the Activity itself.
 */
private fun Context.findActivity(): Activity? {
    var current: Context? = this
    while (current is ContextWrapper) {
        if (current is Activity) return current
        current = current.baseContext
    }
    return null
}
