package com.sohayb.n13download.core

import java.util.Locale

/**
 * Byte/speed/time formatting.
 *
 * Ported from the Windows implementation (`core/utils.py`) so the Android build
 * reads exactly like the desktop app: `human_size` is the GUI formatter and
 * `format_size` the console one.  Both are kept because they disagree on
 * precision on purpose — the GUI never fabricates a "0 B" for an unknown size,
 * it prints an em dash, because an unknown size and an empty file are different
 * things.
 */
object N13Format {

    /** Em dash used everywhere the real value is not known yet. */
    const val UNKNOWN = "—"

    /** GUI byte size. Returns [UNKNOWN] when the value is absent or negative. */
    fun humanSize(bytes: Long?): String {
        if (bytes == null || bytes < 0L) return UNKNOWN
        var size = bytes.toDouble()
        val units = listOf("B", "KB", "MB", "GB", "TB", "PB")
        for ((index, unit) in units.withIndex()) {
            if (size < 1024.0 || index == units.lastIndex) {
                return if (index == 0) "${size.toLong()} B"
                else String.format(Locale.US, "%.1f %s", size, unit)
            }
            size /= 1024.0
        }
        return UNKNOWN
    }

    /** Console byte size — GB gets two decimals, MB/KB one. */
    fun formatSize(bytes: Long): String {
        val gb = 1024.0 * 1024.0 * 1024.0
        val mb = 1024.0 * 1024.0
        return when {
            bytes >= gb -> String.format(Locale.US, "%.2f GB", bytes / gb)
            bytes >= mb -> String.format(Locale.US, "%.1f MB", bytes / mb)
            bytes >= 1024 -> String.format(Locale.US, "%.1f KB", bytes / 1024.0)
            else -> "$bytes B"
        }
    }

    fun formatSpeed(bytesPerSecond: Double): String {
        val gb = 1024.0 * 1024.0 * 1024.0
        val mb = 1024.0 * 1024.0
        return when {
            bytesPerSecond >= gb -> String.format(Locale.US, "%.2f GB/s", bytesPerSecond / gb)
            bytesPerSecond >= mb -> String.format(Locale.US, "%.1f MB/s", bytesPerSecond / mb)
            bytesPerSecond >= 1024 -> String.format(Locale.US, "%.1f KB/s", bytesPerSecond / 1024.0)
            else -> String.format(Locale.US, "%.0f B/s", bytesPerSecond)
        }
    }

    /** `MM:SS` under an hour, `HH:MM:SS` above — matches the Windows elapsed cell. */
    fun formatDuration(seconds: Double): String {
        val total = seconds.toLong().coerceAtLeast(0L)
        val hours = total / 3600
        val minutes = (total % 3600) / 60
        val secs = total % 60
        return if (hours > 0) {
            String.format(Locale.US, "%d:%02d:%02d", hours, minutes, secs)
        } else {
            String.format(Locale.US, "%02d:%02d", minutes, secs)
        }
    }

    /** Remaining time. `null` means "not enough data yet" — never a fake 0:00. */
    fun formatEta(seconds: Double?): String {
        if (seconds == null || seconds <= 0.0 || seconds.isNaN() || seconds.isInfinite()) {
            return UNKNOWN
        }
        if (seconds < 3600.0) return formatDuration(seconds)
        val hours = (seconds / 3600.0).toLong()
        val minutes = ((seconds % 3600.0) / 60.0).toLong()
        return "${hours}h ${minutes}m"
    }

    fun percent(downloaded: Long, total: Long): Double {
        if (total <= 0L) return 0.0
        return (downloaded.toDouble() / total.toDouble() * 100.0).coerceIn(0.0, 100.0)
    }

    /** `4.5 MB / 1.2 GB`, or `4.5 MB / —` when the total is unknown. */
    fun progressSize(downloaded: Long, total: Long?): String =
        "${humanSize(downloaded)} / ${if (total == null || total <= 0L) UNKNOWN else humanSize(total)}"
}
