package com.sohayb.n13download.domain.model

/**
 * The result of inspecting a link before downloading it.
 *
 * Mirrors `core/analyzer.py`'s `Analysis`: everything the ANALYZING step learns
 * about a URL, cached per-URL so the queue does not probe the same link twice.
 */
data class DownloadAnalysis(
    val ok: Boolean = false,
    val url: String = "",
    val finalUrl: String = "",
    val totalSize: Long = 0L,
    val supportsRange: Boolean = false,
    val filename: String = "",
    val contentType: String = "",
    val server: String = "",
    val etag: String = "",
    val lastModified: String = "",
    val statusCode: Int = 0,
    val authRequired: Boolean = false,
    val checksumAvailable: Boolean = false,
    val error: String = "",
    val probedAt: Long = 0L,
) {
    val hasKnownSize: Boolean get() = totalSize > 0L

    /** The green "Resumable" pill in the N13 detection card. */
    val resumable: Boolean get() = ok && supportsRange && totalSize > 0L

    /** A link that cannot be resumed must be restartable from scratch. */
    val singleConnectionOnly: Boolean get() = !supportsRange || totalSize <= 0L
}
