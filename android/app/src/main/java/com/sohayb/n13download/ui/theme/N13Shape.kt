package com.sohayb.n13download.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * Corner radii from the Windows N13 tokens (`--r-xs` .. `--r-pill`).
 * The UI is noticeably squarer than stock Material, which is a large part of why
 * it reads as N13 rather than as a generic Android app.
 */
val N13Shapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

/** `--r-pill`: used by badges, chips and toggles. */
val N13PillShape = RoundedCornerShape(percent = 50)
