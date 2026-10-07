package com.sohayb.n13download.ui.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.update.AppUpdate
import com.sohayb.n13download.ui.components.N13ConfirmDialog

/**
 * "A newer version is available."
 *
 * Reuses the app's standard confirmation dialog rather than introducing a new
 * one, so it inherits the N13 look and the localization for free. The primary
 * action opens the official release page in the user's browser — the app never
 * downloads or installs the APK itself, so it needs no extra permissions and no
 * install-source handling.
 */
@Composable
fun N13UpdateDialog(
    update: AppUpdate,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current

    N13ConfirmDialog(
        title = stringResource(R.string.update_available_title),
        message = stringResource(R.string.update_available_message, update.versionName),
        confirmLabel = stringResource(R.string.update_download),
        onConfirm = {
            openReleasePage(context, update.releaseUrl)
            onDismiss()
        },
        onDismiss = onDismiss,
        dismissLabel = stringResource(R.string.update_later),
        // An update is an offer, not a destructive action.
        danger = false,
    )
}

/** Hands the release page to the browser; a missing browser is simply ignored. */
private fun openReleasePage(context: Context, url: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
