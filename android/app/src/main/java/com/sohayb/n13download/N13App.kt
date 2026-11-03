package com.sohayb.n13download

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sohayb.n13download.domain.model.DownloadSettings
import com.sohayb.n13download.ui.navigation.N13Destination
import com.sohayb.n13download.ui.navigation.N13NavHost
import com.sohayb.n13download.ui.permission.NotificationAccess
import com.sohayb.n13download.ui.permission.NotificationPermissionDialog
import com.sohayb.n13download.ui.permission.rememberNotificationPermission
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Theme
import com.sohayb.n13download.ui.update.N13UpdateDialog
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Application root.
 *
 * The dependency graph lives on the Application so the foreground service and the
 * UI share exactly one queue; this composable only wires the theme and navigation.
 *
 * It also owns the one place N13 asks for the notification permission: the first
 * time a download actually starts.  That is the only moment the permission has a
 * concrete meaning ("this is the download whose progress you will see"), and it
 * means the prompt never appears on a cold start with nothing to explain.
 */
@Composable
fun N13App(
    sharedUrl: String? = null,
    onSharedUrlConsumed: () -> Unit = {},
    pendingTaskId: Long? = null,
    onPendingTaskConsumed: () -> Unit = {},
    initialRoute: String = N13Destination.START.route,
) {
    val application = LocalContext.current.applicationContext as N13Application
    val container = application.container

    val settings by container.settingsProvider.settings
        .collectAsStateWithLifecycle(initialValue = DownloadSettings())

    // Application-scoped, so "Later" survives recomposition and recreation.
    val availableUpdate by container.updateCenter.update.collectAsStateWithLifecycle()

    val scope = rememberCoroutineScope()

    /** Persists the fact that N13 has asked, whatever the answer was. */
    val markAsked = {
        scope.launch {
            val fresh = container.downloadManager.settings()
            if (!fresh.notificationPermissionAsked) {
                container.downloadManager.updateSettings(
                    fresh.copy(notificationPermissionAsked = true),
                )
            }
        }
        Unit
    }

    val notificationPermission = rememberNotificationPermission(
        askedBefore = settings.notificationPermissionAsked,
        onAsked = markAsked,
    )

    val activeDownloads by container.queue.activeCount.collectAsStateWithLifecycle()
    var promptVisible by remember { mutableStateOf(false) }

    LaunchedEffect(
        notificationPermission.access,
        activeDownloads,
        settings.notificationsEnabled,
    ) {
        val worthAsking = notificationPermission.access == NotificationAccess.NOT_ASKED &&
            settings.notificationsEnabled &&
            activeDownloads > 0
        if (worthAsking) {
            // Let the new download appear in the list first, so the dialog has
            // visible context behind it.
            delay(PROMPT_DELAY_MILLIS)
            promptVisible = true
        }
    }

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
                initialRoute = initialRoute,
            )
        }

        availableUpdate?.let { update ->
            N13UpdateDialog(update = update, onDismiss = container.updateCenter::dismiss)
        }

        if (promptVisible) {
            NotificationPermissionDialog(
                onAllow = {
                    promptVisible = false
                    notificationPermission.request()
                },
                onDismiss = {
                    promptVisible = false
                    // "Not now" is an answer too: record it so the prompt never
                    // returns on its own. Settings still offers the route back.
                    markAsked()
                },
            )
        }
    }
}

/** Short enough to feel immediate, long enough for the list to show the download. */
private const val PROMPT_DELAY_MILLIS = 700L
