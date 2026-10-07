package com.sohayb.n13download.domain.update

/**
 * A newer Android release the user should be told about.
 *
 * Carries only what the dialog needs: the version to show and the official page
 * to open. The app never downloads or installs anything itself.
 */
data class AppUpdate(
    val versionName: String,
    /** The official GitHub Release page for this version. */
    val releaseUrl: String,
)
