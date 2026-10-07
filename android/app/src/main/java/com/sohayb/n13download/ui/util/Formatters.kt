package com.sohayb.n13download.ui.util

import android.text.format.DateUtils

/** "3 minutes ago" / "Today · 14:02" style stamps, localised by the platform. */
fun relativeTime(epochMillis: Long): String =
    if (epochMillis <= 0L) {
        "—"
    } else {
        DateUtils.getRelativeTimeSpanString(
            epochMillis,
            System.currentTimeMillis(),
            DateUtils.MINUTE_IN_MILLIS,
            DateUtils.FORMAT_ABBREV_RELATIVE,
        ).toString()
    }

/** Absolute stamp for the Properties screen: "6 Oct 2026, 22:14". */
fun absoluteTime(epochMillis: Long?): String {
    if (epochMillis == null || epochMillis <= 0L) return "—"
    return java.text.SimpleDateFormat("d MMM yyyy, HH:mm", java.util.Locale.getDefault())
        .format(java.util.Date(epochMillis))
}
