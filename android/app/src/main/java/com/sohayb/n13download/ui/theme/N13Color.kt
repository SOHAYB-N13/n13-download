package com.sohayb.n13download.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * The N13 palette.
 *
 * Every value is taken verbatim from the Windows design tokens
 * (`ui/frontend/css/tokens.css`) so the Android build is the same product, not a
 * lookalike.  Border and fill tokens keep their alpha because that is how the
 * desktop UI composites them over the surface stack.
 */
@Immutable
data class N13Colors(
    // Surfaces
    val background: Color,
    val secondary: Color,
    val card: Color,
    val elevated: Color,
    val input: Color,
    val hover: Color,
    val active: Color,

    // Lines
    val border: Color,
    val borderStrong: Color,

    // Brand and state
    val accent: Color,
    val accentHi: Color,
    val accentSoft: Color,
    val accentRing: Color,
    val onAccent: Color,
    val success: Color,
    val successSoft: Color,
    val warning: Color,
    val warningSoft: Color,
    val danger: Color,
    val dangerSoft: Color,
    val info: Color,
    val infoSoft: Color,
    val violet: Color,
    val violetSoft: Color,

    // Text
    val text1: Color,
    val text2: Color,
    val text3: Color,

    val isDark: Boolean,
)

/** Dark theme — the N13 default (`data-theme="dark"`). */
val N13DarkColors = N13Colors(
    background = Color(0xFF0B0F14),
    secondary = Color(0xFF121821),
    card = Color(0xFF171F2B),
    elevated = Color(0xFF1C2634),
    input = Color(0x09FFFFFF),
    hover = Color(0x0BFFFFFF),
    active = Color(0x14FFFFFF),

    border = Color(0x0FFFFFFF),
    borderStrong = Color(0x1FFFFFFF),

    accent = Color(0xFFEF4444),
    accentHi = Color(0xFFF37878),
    accentSoft = Color(0x24EF4444),
    accentRing = Color(0x59EF4444),
    onAccent = Color(0xFFFFFFFF),
    success = Color(0xFF22C55E),
    successSoft = Color(0x2122C55E),
    warning = Color(0xFFF59E0B),
    warningSoft = Color(0x21F59E0B),
    danger = Color(0xFFEF4444),
    dangerSoft = Color(0x21EF4444),
    info = Color(0xFF38BDF8),
    infoSoft = Color(0x2138BDF8),
    violet = Color(0xFF8B5CF6),
    violetSoft = Color(0x218B5CF6),

    text1 = Color(0xFFFFFFFF),
    text2 = Color(0xFFAAB2C0),
    text3 = Color(0xFF6B7686),

    isDark = true,
)

/** Light theme — `[data-theme="light"]` in the Windows tokens. */
val N13LightColors = N13Colors(
    background = Color(0xFFEDF0F6),
    secondary = Color(0xFFF8FAFD),
    card = Color(0xFFFFFFFF),
    elevated = Color(0xFFFFFFFF),
    input = Color(0x0A0F172A),
    hover = Color(0x0D0F172A),
    active = Color(0x170F172A),

    border = Color(0x170F172A),
    borderStrong = Color(0x290F172A),

    accent = Color(0xFFEF4444),
    accentHi = Color(0xFFF37878),
    accentSoft = Color(0x1FEF4444),
    accentRing = Color(0x59EF4444),
    onAccent = Color(0xFFFFFFFF),
    success = Color(0xFF22C55E),
    successSoft = Color(0x1F22C55E),
    warning = Color(0xFFF59E0B),
    warningSoft = Color(0x21F59E0B),
    danger = Color(0xFFEF4444),
    dangerSoft = Color(0x1CEF4444),
    info = Color(0xFF38BDF8),
    infoSoft = Color(0x2138BDF8),
    violet = Color(0xFF8B5CF6),
    violetSoft = Color(0x1C8B5CF6),

    text1 = Color(0xFF0E1522),
    text2 = Color(0xFF54606F),
    text3 = Color(0xFF8A94A2),

    isDark = false,
)

/** The accent swatches the Windows settings page offers. */
val N13AccentSwatches = listOf(
    Color(0xFF3B82F6),
    Color(0xFF8B5CF6),
    Color(0xFF14B8A6),
    Color(0xFF22C55E),
    Color(0xFFF59E0B),
    Color(0xFFEC4899),
    Color(0xFFEF4444),
)

val LocalN13Colors = staticCompositionLocalOf { N13DarkColors }

/** Lightens [accent] the way the Windows UI derives `--accent-hi` (28% toward white). */
fun N13Colors.withAccent(accent: Color): N13Colors = copy(
    accent = accent,
    accentHi = lerpColor(accent, Color.White, 0.28f),
    accentSoft = accent.copy(alpha = if (isDark) 0.14f else 0.12f),
    accentRing = accent.copy(alpha = 0.35f),
)

private fun lerpColor(start: Color, stop: Color, fraction: Float): Color = Color(
    red = start.red + (stop.red - start.red) * fraction,
    green = start.green + (stop.green - start.green) * fraction,
    blue = start.blue + (stop.blue - start.blue) * fraction,
    alpha = start.alpha + (stop.alpha - start.alpha) * fraction,
)
