package com.sohayb.n13download.ui.components

import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection

/**
 * Keeps technical values reading left-to-right in a right-to-left UI.
 *
 * When Persian is selected the configuration is right-to-left, so Compose's
 * [LocalLayoutDirection] becomes [LayoutDirection.Rtl] and `TextAlign.Start`
 * resolves to the right edge.  That is correct for Persian prose but wrong for a
 * URL, a file path, a checksum or `1.5 MB/s`: those are Latin/technical tokens
 * whose own reading order never changes, and letting the surrounding RTL context
 * reorder them produces punctuation in the wrong place and values that appear to
 * jump.
 *
 * [N13Ltr] pins just that subtree back to LTR.  It is applied per value rather
 * than globally — the requirement is explicitly *not* to force the whole app into
 * LTR, only the values whose direction is a property of the data, not the UI.
 */
@Composable
fun N13Ltr(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
        content()
    }
}

/**
 * A [Text] for a technical value: always laid out and aligned left-to-right.
 *
 * The explicit [TextDirection.Ltr] matters as well as the layout direction —
 * it sets the paragraph's base bidi direction, so a value that happens to begin
 * with a neutral character still renders in the order it was written.
 */
@Composable
fun N13LtrText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = LocalTextStyle.current,
    color: Color = Color.Unspecified,
    maxLines: Int = Int.MAX_VALUE,
    overflow: TextOverflow = TextOverflow.Clip,
) {
    N13Ltr {
        Text(
            text = text,
            modifier = modifier,
            style = style.copy(
                textDirection = TextDirection.Ltr,
                textAlign = TextAlign.Start,
            ),
            color = color,
            maxLines = maxLines,
            overflow = overflow,
        )
    }
}
