package com.sohayb.n13download

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.ui.navigation.N13NavHost
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Theme

/**
 * Application root.
 *
 * The dependency graph lives on the Application so the foreground service and the
 * UI share exactly one queue; this composable only wires the theme and navigation.
 */
@Composable
fun N13App(
    sharedUrl: String? = null,
    onSharedUrlConsumed: () -> Unit = {},
    pendingTaskId: Long? = null,
    onPendingTaskConsumed: () -> Unit = {},
) {
    val application = LocalContext.current.applicationContext as N13Application
    val container = application.container

    val settings by container.settingsProvider.settings
        .collectAsStateWithLifecycle(initialValue = DownloadSettings())

    N13Theme(
        themeMode = settings.themeMode,
        accentOverride = Color(settings.accentColor.toInt()),
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = LocalN13Colors.current.background,
        ) {
            N13NavHost(
                container = container,
                sharedUrl = sharedUrl,
                onSharedUrlConsumed = onSharedUrlConsumed,
                pendingTaskId = pendingTaskId,
                onPendingTaskConsumed = onPendingTaskConsumed,
            )
        }
    }
}
