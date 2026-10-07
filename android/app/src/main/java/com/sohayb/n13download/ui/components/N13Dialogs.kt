package com.sohayb.n13download.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13Shapes

/**
 * Bottom sheet, built on [Dialog] rather than Material's `ModalBottomSheet`.
 *
 * The N13 sheet is a flat elevated panel with a scrim and no drag handle, so
 * building it directly keeps the visual language and avoids relying on an
 * experimental Material API.
 */
@Composable
fun N13Sheet(
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    val n13 = LocalN13Colors.current
    val interactionSource = remember { MutableInteractionSource() }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(
            usePlatformDefaultWidth = false,
            decorFitsSystemWindows = false,
        ),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(n13.background.copy(alpha = 0.72f))
                .clickable(
                    interactionSource = interactionSource,
                    indication = null,
                    onClick = onDismiss,
                ),
            contentAlignment = Alignment.BottomCenter,
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp))
                    .background(n13.elevated)
                    // Swallow taps so they never reach the scrim.
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = {},
                    )
                    .navigationBarsPadding()
                    .padding(bottom = 6.dp)
                    .then(modifier),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 10.dp, bottom = 6.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(
                            modifier = Modifier
                                .size(width = 36.dp, height = 4.dp)
                                .clip(N13Shapes.extraSmall)
                                .background(n13.borderStrong),
                        )
                    }
                    content()
                }
            }
        }
    }
}

/** One tappable row inside a sheet or a settings card. */
@Composable
fun N13SheetItem(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    trailingText: String? = null,
    danger: Boolean = false,
) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = if (danger) n13.danger else n13.text2,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(14.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = if (danger) n13.danger else n13.text1,
            modifier = Modifier.weight(1f),
        )
        if (trailingText != null) {
            Text(
                text = trailingText,
                style = MaterialTheme.typography.bodySmall,
                color = n13.text3,
            )
        }
    }
}

/**
 * Confirmation dialog.
 *
 * Wording and structure follow the Windows `confirm.*` strings, and the
 * destructive action is always the accented one so it cannot be mistaken for the
 * safe choice.
 */
@Composable
fun N13ConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    dismissLabel: String = "Cancel",
    danger: Boolean = true,
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
                        imageVector = if (danger) N13Icons.Alert else N13Icons.Info,
                        contentDescription = null,
                        tint = if (danger) n13.danger else n13.info,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleMedium,
                        color = n13.text1,
                    )
                }

                Spacer(Modifier.height(12.dp))

                Text(
                    text = message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                )

                Spacer(Modifier.height(20.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    N13SecondaryButton(label = dismissLabel, onClick = onDismiss)
                    Spacer(Modifier.width(8.dp))
                    N13PrimaryButton(
                        label = confirmLabel,
                        onClick = onConfirm,
                        icon = null,
                    )
                }
            }
        }
    }
}

/** Simple message dialog used for information the user only has to acknowledge. */
@Composable
fun N13MessageDialog(
    title: String,
    message: String,
    onDismiss: () -> Unit,
    confirmLabel: String = "OK",
    icon: ImageVector = N13Icons.Info,
    accent: androidx.compose.ui.graphics.Color? = null,
) {
    val n13 = LocalN13Colors.current
    val tint = accent ?: n13.info

    Dialog(onDismissRequest = onDismiss) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(N13Shapes.large)
                .background(n13.elevated)
                .padding(20.dp),
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(28.dp),
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleMedium,
                    color = n13.text1,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = n13.text2,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(18.dp))
                N13PrimaryButton(label = confirmLabel, onClick = onDismiss)
            }
        }
    }
}
