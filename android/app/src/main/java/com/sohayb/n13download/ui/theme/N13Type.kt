package com.sohayb.n13download.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * Typography sized from the Windows N13 scale.
 *
 * The desktop UI runs a 14px base with tight, information-dense rows.  The
 * platform default family is used on purpose: N13's Windows font (Segoe UI
 * Variable) does not exist on Android, and shipping a webfont would add weight
 * for no gain on a download list.
 */
private val Default = FontFamily.Default

val N13Typography = Typography(
    // Page H1 — 25px / 650 in the Windows UI.
    headlineMedium = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 25.sp,
        lineHeight = 30.sp,
        letterSpacing = (-0.2).sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 21.sp,
        lineHeight = 26.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 17.sp,
        lineHeight = 23.sp,
    ),
    // Card title — 15px / 620.
    titleMedium = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.1.sp,
    ),
    // Download row name — 13.5px / 580.
    titleSmall = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.Medium,
        fontSize = 13.5.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.1.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 21.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 19.sp,
        letterSpacing = 0.1.sp,
    ),
    // Row subtitle — 11.5px.
    bodySmall = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.Normal,
        fontSize = 11.5.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.1.sp,
    ),
    // Buttons — 13px / 600.
    labelLarge = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 13.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.2.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.2.sp,
    ),
    // Table headers and section labels — 11px / 650, uppercase, wide tracking.
    labelSmall = TextStyle(
        fontFamily = Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 11.sp,
        lineHeight = 14.sp,
        letterSpacing = 0.8.sp,
    ),
)

/** Monospaced style for paths, URLs and checksums. */
val N13MonoStyle = TextStyle(
    fontFamily = FontFamily.Monospace,
    fontWeight = FontWeight.Normal,
    fontSize = 12.sp,
    lineHeight = 17.sp,
)
