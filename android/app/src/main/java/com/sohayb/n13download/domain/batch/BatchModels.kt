package com.sohayb.n13download.domain.batch

/** One generated entry, ready to become a download task. */
data class BatchItem(
    val url: String,
    /** Explicit override; null means "derive it from the URL". */
    val filename: String? = null,
)

/** A URL the batch refused, with the reason shown to the user. */
data class BatchRejection(val url: String, val reason: String)

/**
 * The outcome of queueing a batch.
 *
 * [skipped] counts links that were already waiting or downloading: re-adding them
 * would either download the same bytes twice or immediately fail the queue's own
 * duplicate check, so they are reported instead of silently dropped.
 */
data class BatchAddResult(
    val queued: Int = 0,
    val taskIds: List<Long> = emptyList(),
    val skipped: Int = 0,
    val rejected: List<BatchRejection> = emptyList(),
) {
    val nothingQueued: Boolean get() = queued == 0
}
