package com.sohayb.n13download.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import com.sohayb.n13download.domain.model.ThemeMode

/**
 * The N13 theme.
 *
 * Dynamic colour is deliberately not used: the product has one identity, and it
 * must look the same on every device.  Everything Material draws is mapped onto
 * the N13 tokens, and the tokens themselves are exposed through
 * [LocalN13Colors] so components can use the semantic colours Material has no
 * slot for (info, violet, and the soft state fills).
 */
@Composable
fun N13Theme(
    themeMode: ThemeMode = ThemeMode.DARK,
    accentOverride: Color? = null,
    content: @Composable () -> Unit,
) {
    val systemDark = isSystemInDarkTheme()
    val dark = when (themeMode) {
        ThemeMode.DARK -> true
        ThemeMode.LIGHT -> false
        ThemeMode.SYSTEM -> systemDark
    }

    val n13Colors = remember(dark, accentOverride) {
        val base = if (dark) N13DarkColors else N13LightColors
        if (accentOverride != null) base.withAccent(accentOverride) else base
    }

    val materialScheme = remember(n13Colors) { n13Colors.toMaterialScheme() }

    CompositionLocalProvider(LocalN13Colors provides n13Colors) {
        MaterialTheme(
            colorScheme = materialScheme,
            typography = N13Typography,
            shapes = N13Shapes,
            content = content,
        )
    }
}

/**
 * Convenience accessor for the N13 tokens: `MaterialTheme.n13.accent`.
 * Material has no slot for info / violet / soft state fills, so those are read
 * from here.
 */
val MaterialTheme.n13: N13Colors
    @Composable get() = LocalN13Colors.current

private fun N13Colors.toMaterialScheme() = if (isDark) {
    darkColorScheme(
        primary = accent,
        onPrimary = onAccent,
        primaryContainer = accentSoft,
        onPrimaryContainer = text1,
        inversePrimary = accentHi,

        secondary = info,
        onSecondary = background,
        secondaryContainer = infoSoft,
        onSecondaryContainer = text1,

        tertiary = success,
        onTertiary = background,
        tertiaryContainer = successSoft,
        onTertiaryContainer = text1,

        background = background,
        onBackground = text1,

        surface = card,
        onSurface = text1,
        surfaceVariant = elevated,
        onSurfaceVariant = text2,
        surfaceTint = accent,

        surfaceContainerLowest = background,
        surfaceContainerLow = secondary,
        surfaceContainer = card,
        surfaceContainerHigh = elevated,
        surfaceContainerHighest = card,

        inverseSurface = text1,
        inverseOnSurface = background,

        outline = Color(0xFF2B3747),
        outlineVariant = Color(0xFF1E2833),

        error = danger,
        onError = onAccent,
        errorContainer = dangerSoft,
        onErrorContainer = text1,

        scrim = Color(0xE604070B),
    )
} else {
    lightColorScheme(
        primary = accent,
        onPrimary = onAccent,
        primaryContainer = accentSoft,
        onPrimaryContainer = text1,
        inversePrimary = accentHi,

        secondary = info,
        onSecondary = Color.White,
        secondaryContainer = infoSoft,
        onSecondaryContainer = text1,

        tertiary = success,
        onTertiary = Color.White,
        tertiaryContainer = successSoft,
        onTertiaryContainer = text1,

        background = background,
        onBackground = text1,

        surface = card,
        onSurface = text1,
        surfaceVariant = background,
        onSurfaceVariant = text2,
        surfaceTint = accent,

        surfaceContainerLowest = card,
        surfaceContainerLow = secondary,
        surfaceContainer = card,
        surfaceContainerHigh = card,
        surfaceContainerHighest = background,

        inverseSurface = text1,
        inverseOnSurface = card,

        outline = Color(0xFFC9CDD5),
        outlineVariant = Color(0xFFD9DCE4),

        error = danger,
        onError = Color.White,
        errorContainer = dangerSoft,
        onErrorContainer = text1,

        scrim = Color(0x4D505F78),
    )
}

/** Picks black or white text for a coloured surface. */
fun onColorFor(background: Color): Color =
    if (background.luminance() > 0.55f) Color(0xFF0E1522) else Color.White
