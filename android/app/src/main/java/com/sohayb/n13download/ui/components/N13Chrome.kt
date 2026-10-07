package com.sohayb.n13download.ui.components

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.sohayb.n13download.R
import com.sohayb.n13download.ui.navigation.N13Destination
import com.sohayb.n13download.ui.theme.LocalN13Colors
import com.sohayb.n13download.ui.theme.N13PillShape
import com.sohayb.n13download.ui.theme.N13Shapes

/**
 * The N13 brand mark, taken from the same artwork the Windows icon uses.
 * Rendered from the launcher foreground so the in-app mark and the home-screen
 * icon can never drift apart.
 */
@Composable
fun N13BrandMark(size: Dp = 40.dp, modifier: Modifier = Modifier) {
    Image(
        painter = painterResource(id = R.mipmap.ic_launcher_foreground),
        contentDescription = null,
        modifier = modifier.size(size),
    )
}

/**
 * Page header: brand mark, page title and an action slot.
 *
 * This is the mobile equivalent of the Windows page chrome (the sidebar brand
 * plus the `h1`/subtitle block), collapsed onto one line so a phone does not lose
 * a third of its height to chrome.
 */
@Composable
fun N13PageHeader(
    title: String,
    modifier: Modifier = Modifier,
    brandLine: String = "N13 Download Manager",
    trailing: @Composable RowScope.() -> Unit = {},
) {
    val n13 = LocalN13Colors.current

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 6.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        N13BrandMark(size = 42.dp)

        Spacer(Modifier.width(12.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = brandLine.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = n13.text3,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = title,
                style = MaterialTheme.typography.headlineSmall,
                color = n13.text1,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }

        trailing()
    }
}

/**
 * Bottom navigation.
 *
 * The Windows app uses an eight-item sidebar; on a phone the same information
 * architecture is reduced to the three destinations the product treats as
 * primary.  Downloads is the start destination.
 */
@Composable
fun N13BottomBar(
    currentRoute: String?,
    onSelect: (N13Destination) -> Unit,
    modifier: Modifier = Modifier,
) {
    val n13 = LocalN13Colors.current

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(n13.secondary)
            .navigationBarsPadding()
            .padding(horizontal = 6.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        N13Destination.entries.forEach { destination ->
            val selected = currentRoute == destination.route
            Column(
                modifier = Modifier
                    .weight(1f)
                    .clip(N13Shapes.small)
                    .background(if (selected) n13.accentSoft else n13.secondary)
                    .clickable { onSelect(destination) }
                    .padding(vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    imageVector = destination.icon,
                    contentDescription = null,
                    tint = if (selected) n13.accent else n13.text3,
                    modifier = Modifier.size(21.dp),
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = destination.label,
                    style = MaterialTheme.typography.labelSmall,
                    color = if (selected) n13.accent else n13.text3,
                    maxLines = 1,
                )
            }
        }
    }
}

/**
 * Empty state.
 *
 * Mirrors the Windows `empty.*` copy: an icon, a title, an explanation, and up to
 * two real actions — never a dead button.
 */
@Composable
fun N13EmptyState(
    icon: ImageVector,
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    primaryLabel: String? = null,
    onPrimary: (() -> Unit)? = null,
    secondaryLabel: String? = null,
    onSecondary: (() -> Unit)? = null,
) {
    val n13 = LocalN13Colors.current

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            modifier = Modifier
                .size(84.dp)
                .clip(N13Shapes.extraLarge)
                .background(n13.accentSoft),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = n13.accent,
                modifier = Modifier.size(38.dp),
            )
        }

        Spacer(Modifier.height(20.dp))

        Text(
            text = title,
            style = MaterialTheme.typography.titleLarge,
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

        if (primaryLabel != null && onPrimary != null) {
            Spacer(Modifier.height(22.dp))
            N13PrimaryButton(
                label = primaryLabel,
                onClick = onPrimary,
                icon = N13Icons.Plus,
            )
        }

        if (secondaryLabel != null && onSecondary != null) {
            Spacer(Modifier.height(6.dp))
            N13SecondaryButton(label = secondaryLabel, onClick = onSecondary)
        }
    }
}

/** Small pill used for live counters in the header strip. */
@Composable
fun N13CountPill(text: String, color: androidx.compose.ui.graphics.Color, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .clip(N13PillShape)
            .background(color.copy(alpha = 0.14f))
            .padding(horizontal = 9.dp, vertical = 3.dp),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            maxLines = 1,
        )
    }
}
