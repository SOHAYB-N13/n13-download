package com.sohayb.n13download.core

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build

/**
 * The installed build's identity.
 *
 * Read from the package manager rather than `BuildConfig`, because this project
 * does not enable the generated `BuildConfig` class.
 */
data class AppVersionInfo(
    /** Shown to the user, e.g. `1.1.1`. */
    val versionName: String,
    /** The authoritative comparison value, e.g. `3`. */
    val versionCode: Int,
)

/** Reads the installed version, falling back to zeroes if the package is unreadable. */
fun Context.appVersionInfo(): AppVersionInfo {
    val info = runCatching {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(0L))
        } else {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, 0)
        }
    }.getOrNull()

    val code = when {
        info == null -> 0
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.P -> info.longVersionCode.toInt()
        else -> @Suppress("DEPRECATION") info.versionCode
    }

    return AppVersionInfo(
        versionName = info?.versionName.orEmpty(),
        versionCode = code,
    )
}
