package com.sohayb.n13download.ui.permission

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner

/**
 * Whether N13 may show download notifications right now.
 *
 * Four states, not a boolean, because the UI has to say something different in
 * each: never asked (offer the prompt), denied once (offer it again from
 * Settings), and permanently blocked (only the system settings screen can fix
 * it).  Collapsing them is how apps end up re-prompting a user who already said
 * no, or showing a button that does nothing.
 */
enum class NotificationAccess {
    /** Notifications are allowed. */
    GRANTED,

    /** Android 13+ and never asked — the one moment a system prompt is useful. */
    NOT_ASKED,

    /** Asked and declined, but the system will still show a prompt. */
    DENIED,

    /** Blocked by the user or the OS; only the app's settings screen can undo it. */
    BLOCKED,
}

/** What the UI needs in order to explain, ask, or route to settings. */
class NotificationPermissionState(
    val access: NotificationAccess,
    /** Launches the system prompt. No-op when the OS will not show one. */
    val request: () -> Unit,
    /** Opens this app's notification settings. */
    val openSettings: () -> Unit,
) {
    val granted: Boolean get() = access == NotificationAccess.GRANTED

    /** True when a system prompt is still worth showing. */
    val canRequest: Boolean
        get() = access == NotificationAccess.NOT_ASKED || access == NotificationAccess.DENIED
}

/**
 * Tracks the notification permission.
 *
 * `POST_NOTIFICATIONS` is a runtime permission from Android 13, and the app
 * declares it — but a declaration alone grants nothing, so before this existed the
 * manifest was correct and every progress notification was silently dropped.
 *
 * The state is re-evaluated on every resume, because the only way out of
 * [NotificationAccess.BLOCKED] is the system settings screen and the user comes
 * back through `ON_RESUME`.
 *
 * @param askedBefore whether N13 has already shown the prompt once. Persisted, so
 *   a user who declined is never asked again by surprise.
 * @param onAsked called when the prompt is dismissed (either answer), so the
 *   caller can record that N13 has asked.
 */
@Composable
fun rememberNotificationPermission(
    askedBefore: Boolean,
    onAsked: () -> Unit,
): NotificationPermissionState {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    var revision by remember { mutableIntStateOf(0) }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) revision++
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        onAsked()
        revision++
    }

    val access = remember(askedBefore, revision) {
        evaluateAccess(context, activity, askedBefore)
    }

    return remember(access, askedBefore) {
        NotificationPermissionState(
            access = access,
            request = {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    // Pre-13 there is no runtime permission: the only thing that
                    // can be off is the app-level notification switch.
                    onAsked()
                    context.openNotificationSettings()
                }
            },
            openSettings = { context.openNotificationSettings() },
        )
    }
}

private fun evaluateAccess(
    context: Context,
    activity: Activity?,
    askedBefore: Boolean,
): NotificationAccess {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
        // No runtime permission exists; the user can still block the app.
        return if (NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            NotificationAccess.GRANTED
        } else {
            NotificationAccess.BLOCKED
        }
    }

    val granted = ContextCompat.checkSelfPermission(
        context,
        Manifest.permission.POST_NOTIFICATIONS,
    ) == PackageManager.PERMISSION_GRANTED
    if (granted) return NotificationAccess.GRANTED
    if (!askedBefore) return NotificationAccess.NOT_ASKED

    // After a request, "should show rationale" is exactly the difference between
    // "declined once" and "don't ask again".
    val rationale = activity != null &&
        ActivityCompat.shouldShowRequestPermissionRationale(
            activity,
            Manifest.permission.POST_NOTIFICATIONS,
        )
    return if (rationale) NotificationAccess.DENIED else NotificationAccess.BLOCKED
}

private fun Context.findActivity(): Activity? {
    var current: Context? = this
    while (current is ContextWrapper) {
        if (current is Activity) return current
        current = current.baseContext
    }
    return current as? Activity
}

/** Opens this app's notification settings, falling back to the app details page. */
fun Context.openNotificationSettings() {
    val intents = listOf(
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, packageName),
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.fromParts("package", packageName, null)),
    )
    for (intent in intents) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { startActivity(intent) }.isSuccess) return
    }
}
