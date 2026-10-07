package com.sohayb.n13download.domain.update

/**
 * One entry from the GitHub Releases API, reduced to what the update check needs.
 *
 * Deliberately a plain data class with no Android or JSON types, so the release
 * selection and version comparison stay pure and testable without a network.
 */
data class RemoteRelease(
    val tag: String,
    val title: String,
    /** The release notes, which carry the published `versionCode`. */
    val body: String,
    /** The official release page, opened when the user asks to download. */
    val htmlUrl: String,
    val isDraft: Boolean,
    val isPrerelease: Boolean,
    val assetNames: List<String>,
)
