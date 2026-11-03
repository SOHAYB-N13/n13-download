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
import com.sohayb.n13download.ui.permission.NotificationPermissionDialog
import com.sohayb.n13download.ui.permission.NotificationPromptPolicy
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
 * launch of the app.  The user is told *why* notifications matter before the
 * system prompt is launched, and — because the explanation is shown once and the
 * answer recorded — N13 never asks again by itself.  Settings always keeps the
 * route back, which is where a user who declined goes when they change their
 * mind.
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

    var promptVisible by remember { mutableStateOf(false) }

    // Once the user has answered the explanation — either way — this launch is
    // done with it.  `notificationPermissionAsked` is the durable version of the
    // same fact; this latch only closes the few-millisecond window before the
    // write lands, during which a resume could otherwise re-open the dialog.
    var promptAnswered by remember { mutableStateOf(false) }

    // The update dialog is shown from the same composable, and two dialogs on one
    // launch is a bug, not a feature: the notification explanation waits its turn.
    val updateVisible = availableUpdate != null

    LaunchedEffect(
        notificationPermission.access,
        settings.notificationPermissionAsked,
        settings.notificationsEnabled,
        updateVisible,
    ) {
        if (!promptAnswered &&
            NotificationPromptPolicy.shouldPrompt(
                access = notificationPermission.access,
                askedBefore = settings.notificationPermissionAsked,
                notificationsEnabled = settings.notificationsEnabled,
                updateDialogVisible = updateVisible,
            )
        ) {
            // Let the first frame draw behind the dialog, so it opens over a
            // real screen instead of a blank one.
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
                    promptAnswered = true
                    markAsked()
                    // "Yes" must do what it says. When the OS will no longer show
                    // its own prompt — pre-13 with the app blocked at system level —
                    // the only thing that can actually enable notifications is the
                    // settings screen, so go there instead of tapping into a no-op.
                    if (NotificationPromptPolicy.canRequest(notificationPermission.access)) {
                        notificationPermission.request()
                    } else {
                        notificationPermission.openSettings()
                    }
                },
                onDismiss = {
                    promptVisible = false
                    promptAnswered = true
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
