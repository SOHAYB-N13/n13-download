package com.sohayb.n13download.ui.permission

import androidx.compose.foundation.background
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.sohayb.n13download.R
import com.sohayb.n13download.ui.components.N13Icons
import com.sohayb.n13download.ui.components.N13PrimaryButton
import com.sohayb.n13download.ui.components.N13SecondaryButton
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes

/**
 * The one-time explanation shown before the system notification prompt.
 *
 * Android only lets an app ask twice before treating a denial as permanent, and a
 * bare system dialog with no context is the main reason people decline it.  This
 * says what the permission is for — progress and completion, nothing else — and
 * makes "not now" a first-class answer rather than a trap.
 */
@Composable
fun NotificationPermissionDialog(
    onAllow: () -> Unit,
    onDismiss: () -> Unit,
) {
    val n13 = LocalN13Colors.current

    Dialog(onDismissRequest = onDismiss) {
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
                        imageVector = N13Icons.Bell,
                        contentDescription = null,
                        tint = n13.accent,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = stringResource(R.string.notif_permission_title),
                        style = MaterialTheme.typography.titleMedium,
                        color = n13.text1,
                    )
                }

                Spacer(Modifier.height(10.dp))

                Text(
                    text = stringResource(R.string.notif_permission_message),
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                )

                Spacer(Modifier.height(18.dp))

                N13PrimaryButton(
                    label = stringResource(R.string.notif_permission_allow),
                    onClick = onAllow,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                N13SecondaryButton(
                    label = stringResource(R.string.notif_permission_later),
                    onClick = onDismiss,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}
