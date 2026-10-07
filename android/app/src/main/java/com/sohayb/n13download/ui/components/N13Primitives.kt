package com.sohayb.n13download.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13PillShape
import com.sohayb.n13download.ui.theme.N13Shapes

/** The N13 panel: card surface, hairline border, 14dp radius. */
@Composable
fun N13Card(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    borderColor: Color? = null,
    containerColor: Color? = null,
    content: @Composable () -> Unit,
) {
    val n13 = LocalN13Colors.current
    val shape = N13Shapes.medium
    Box(
        modifier = modifier
            .clip(shape)
            .background(containerColor ?: n13.card)
            .border(BorderStroke(1.dp, borderColor ?: n13.border), shape)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
    ) {
        content()
    }
}

/** Uppercase section label, as used above every settings group in N13. */
@Composable
fun N13SectionLabel(text: String, modifier: Modifier = Modifier) {
    val n13 = LocalN13Colors.current
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelSmall,
        color = n13.text3,
        modifier = modifier,
    )
}

/** Status pill: coloured dot plus the N13 status label. */
@Composable
fun N13StatusBadge(
    visual: StatusVisual,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .clip(N13PillShape)
            .background(visual.color.copy(alpha = 0.13f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val dotAlpha = if (visual.pulsing) {
            val transition = rememberInfiniteTransition(label = "status-dot")
            val value by transition.animateFloat(
                initialValue = 0.35f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 900),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "status-dot-alpha",
            )
            value
        } else {
            1f
        }

        Box(
            modifier = Modifier
                .size(5.dp)
                .alpha(dotAlpha)
                .clip(N13PillShape)
                .background(visual.color),
        )
        Spacer(Modifier.width(6.dp))
        Text(
            text = visual.label,
            style = MaterialTheme.typography.labelSmall,
            color = visual.color,
            maxLines = 1,
        )
    }
}

/** Neutral chip, used for categories, priorities and filters. */
@Composable
fun N13Chip(
    label: String,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    count: Int? = null,
    selected: Boolean = false,
    accent: Color? = null,
    onClick: (() -> Unit)? = null,
) {
    val n13 = LocalN13Colors.current
    val tint = accent ?: n13.accent
    val background = if (selected) tint.copy(alpha = 0.16f) else n13.hover
    val contentColor = if (selected) tint else n13.text2

    Row(
        modifier = modifier
            .clip(N13PillShape)
            .background(background)
            .then(
                if (selected) Modifier.border(1.dp, tint.copy(alpha = 0.35f), N13PillShape)
                else Modifier,
            )
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = contentColor,
                modifier = Modifier.size(13.dp),
            )
            Spacer(Modifier.width(6.dp))
        }
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = contentColor,
            maxLines = 1,
        )
        if (count != null) {
            Spacer(Modifier.width(6.dp))
            Text(
                text = count.toString(),
                style = MaterialTheme.typography.labelSmall,
                color = if (selected) tint else n13.text3,
            )
        }
    }
}

/**
 * The N13 progress bar.
 *
 * The percentage is drawn inside the bar with a dark shadow, exactly as the
 * Windows row does, so it stays readable over both the filled and the empty part.
 * A null [fraction] renders an indeterminate shimmer rather than a fake 0%.
 */
@Composable
fun N13ProgressBar(
    fraction: Float?,
    modifier: Modifier = Modifier,
    color: Color? = null,
    colorHi: Color? = null,
    height: Dp = 14.dp,
    label: String? = null,
) {
    val n13 = LocalN13Colors.current
    val baseColor = color ?: n13.accent
    val topColor = colorHi ?: n13.accentHi

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(height)
            .clip(N13PillShape)
            .background(n13.active),
        contentAlignment = Alignment.Center,
    ) {
        if (fraction == null) {
            val transition = rememberInfiniteTransition(label = "indeterminate")
            val shift by transition.animateFloat(
                initialValue = -0.4f,
                targetValue = 1.4f,
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 1400),
                    repeatMode = RepeatMode.Restart,
                ),
                label = "indeterminate-shift",
            )
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .fillMaxHeight()
                    .background(
                        Brush.horizontalGradient(
                            colors = listOf(Color.Transparent, baseColor.copy(alpha = 0.55f), Color.Transparent),
                            startX = shift * 900f,
                            endX = shift * 900f + 360f,
                        ),
                    ),
            )
        } else if (fraction > 0f) {
            Box(
                modifier = Modifier
                    .fillMaxWidth(fraction.coerceIn(0f, 1f))
                    .fillMaxHeight()
                    .clip(N13PillShape)
                    .background(Brush.horizontalGradient(listOf(baseColor, topColor))),
            )
        }

        if (label != null) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontSize = 10.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.4.sp,
                    shadow = Shadow(color = Color(0xCC000000), blurRadius = 3f),
                ),
                color = Color.White,
                maxLines = 1,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 6.dp),
            )
        }
    }
}

/** Compact inline row action, mirroring the Windows row's inline buttons. */
@Composable
fun N13RowAction(
    label: String,
    icon: ImageVector,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    danger: Boolean = false,
) {
    val n13 = LocalN13Colors.current
    val tint = if (danger) n13.danger else n13.text2

    Row(
        modifier = modifier
            .clip(N13Shapes.small)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = tint,
            modifier = Modifier.size(14.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = tint,
            maxLines = 1,
        )
    }
}

/** Square icon-only button used in headers and toolbars. */
@Composable
fun N13IconButton(
    icon: ImageVector,
    contentDescription: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tint: Color? = null,
    enabled: Boolean = true,
    size: Dp = 40.dp,
) {
    val n13 = LocalN13Colors.current
    Box(
        modifier = modifier
            .size(size)
            .clip(N13Shapes.small)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = contentDescription,
            tint = (tint ?: n13.text2).let { if (enabled) it else it.copy(alpha = 0.4f) },
            modifier = Modifier.size(size * 0.5f),
        )
    }
}

/** A labelled key/value row, used by Settings and Properties. */
@Composable
fun N13InfoRow(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    valueColor: Color? = null,
    mono: Boolean = false,
) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        if (icon != null) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = n13.text3,
                modifier = Modifier.size(16.dp),
            )
            Spacer(Modifier.width(12.dp))
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = label,
                style = MaterialTheme.typography.bodySmall,
                color = n13.text3,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = value,
                style = if (mono) {
                    MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                        fontSize = 12.sp,
                    )
                } else {
                    MaterialTheme.typography.bodyMedium
                },
                color = valueColor ?: n13.text1,
            )
        }
    }
}

/** Full-width primary action, matching the N13 `.btn-primary` treatment. */
@Composable
fun N13PrimaryButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    enabled: Boolean = true,
) {
    val n13 = LocalN13Colors.current
    Surface(
        modifier = modifier
            .clip(N13Shapes.small)
            .clickable(enabled = enabled, onClick = onClick),
        color = if (enabled) n13.accent else n13.accent.copy(alpha = 0.35f),
        shape = N13Shapes.small,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            if (icon != null) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    tint = n13.onAccent,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(8.dp))
            }
            Text(
                text = label,
                style = MaterialTheme.typography.labelLarge,
                color = n13.onAccent,
            )
        }
    }
}

/** Quiet secondary action. */
@Composable
fun N13SecondaryButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
) {
    val n13 = LocalN13Colors.current
    Row(
        modifier = modifier
            .clip(N13Shapes.small)
            .background(n13.hover)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        if (icon != null) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = n13.text2,
                modifier = Modifier.size(16.dp),
            )
            Spacer(Modifier.width(8.dp))
        }
        Text(
            text = label,
            style = MaterialTheme.typography.labelLarge,
            color = n13.text2,
        )
    }
}

/** Small text link used in empty states and footers. */
@Composable
fun N13TextAction(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    color: Color? = null,
) {
    val n13 = LocalN13Colors.current
    Text(
        text = label,
        style = MaterialTheme.typography.labelLarge,
        color = color ?: n13.accent,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .clip(N13Shapes.small)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    )
}

/** Hairline divider inset to line up with row content. */
@Composable
fun N13Divider(modifier: Modifier = Modifier, startPadding: Dp = 0.dp) {
    val n13 = LocalN13Colors.current
    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = startPadding)
            .height(1.dp)
            .background(n13.border),
    )
}

/** Padding preset used by every list in the app. */
val N13ListPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 24.dp)
