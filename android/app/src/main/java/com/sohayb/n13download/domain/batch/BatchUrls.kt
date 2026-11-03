package com.sohayb.n13download.domain.batch

import com.sohayb.n13download.core.FilenameResolver

/**
 * Why a line of pasted text was not turned into a download.
 *
 * The Windows batch page filters silently (`line.startsWith("http")`), which
 * leaves a user staring at "0 queued" with no idea which line was wrong.  Android
 * keeps the same acceptance rule but reports the rejects so the UI can point at
 * them.
 */
enum class BatchLineProblem {
    /** The line does not look like a link at all. */
    NOT_A_LINK,

    /** It looks like a link but is not a usable http(s) URL. */
    INVALID_URL,

    /** The same link already appeared earlier in this batch. */
    DUPLICATE,

    /** The batch is already at [BatchUrls.MAX_URLS]; the rest was dropped. */
    TOO_MANY,
}

data class BatchRejectedLine(
    val line: String,
    val lineNumber: Int,
    val problem: BatchLineProblem,
)

/**
 * The outcome of reading a pasted or imported URL list.
 *
 * [urls] is normalised, de-duplicated and in input order — exactly the list that
 * will be queued.
 */
data class BatchParseResult(
    val urls: List<String> = emptyList(),
    val rejected: List<BatchRejectedLine> = emptyList(),
) {
    val total: Int get() = urls.size
    val duplicateCount: Int get() = rejected.count { it.problem == BatchLineProblem.DUPLICATE }
    val invalidCount: Int get() = rejected.count {
        it.problem == BatchLineProblem.NOT_A_LINK || it.problem == BatchLineProblem.INVALID_URL
    }
    val overflowCount: Int get() = rejected.count { it.problem == BatchLineProblem.TOO_MANY }
    val isEmpty: Boolean get() = urls.isEmpty()

    /** The first few rejects, for an inline explanation. */
    fun previewRejects(limit: Int = 4): List<BatchRejectedLine> = rejected.take(limit)
}

/**
 * URL-list parsing for the batch screen.
 *
 * Rules, taken from the Windows implementation (`batch/sources.py`
 * `load_url_list` plus the Batch page's own filter):
 *
 *  - one link per line;
 *  - blank lines are ignored, never reported;
 *  - a line is normalised the same way the single-download dialog normalises a
 *    pasted link (`FilenameResolver.normalizeUrl`), so `example.com/a.zip` is
 *    accepted as `https://example.com/a.zip` — the Android app already accepts
 *    that shape everywhere else, and refusing it only here would be inconsistent;
 *  - duplicates are removed preserving the first occurrence
 *    (`dict.fromkeys` in the Windows code);
 *  - the list is capped, because a 200k-line paste must not become 200k tasks.
 */
object BatchUrls {

    /**
     * Upper bound on one batch.
     *
     * The Windows product has no cap, but it queues straight into a desktop
     * queue; on a phone every entry becomes a database row plus a notification
     * candidate, so an unbounded paste is a real way to wedge the app.
     */
    const val MAX_URLS = 5_000

    /** Parses a multi-line URL list, one link per line. */
    fun parse(text: String): BatchParseResult =
        collect(text.lineSequence().mapIndexed { index, line -> index + 1 to line })

    /**
     * Parses a list of already-split candidates (CSV cells, JSON strings, regex
     * matches).  Line numbers are the candidate index so a message can still say
     * "entry 3".
     */
    fun parseCandidates(candidates: List<String>): BatchParseResult =
        collect(candidates.asSequence().mapIndexed { index, value -> index + 1 to value })

    private fun collect(candidates: Sequence<Pair<Int, String>>): BatchParseResult {
        val accepted = ArrayList<String>()
        val seen = HashSet<String>()
        val rejected = ArrayList<BatchRejectedLine>()

        for ((lineNumber, raw) in candidates) {
            val line = raw.trim()
            if (line.isEmpty()) continue

            if (accepted.size >= MAX_URLS) {
                rejected += BatchRejectedLine(line, lineNumber, BatchLineProblem.TOO_MANY)
                continue
            }

            val normalized = FilenameResolver.normalizeUrl(line)
            if (!looksLikeLink(normalized)) {
                rejected += BatchRejectedLine(line, lineNumber, BatchLineProblem.NOT_A_LINK)
                continue
            }
            if (!FilenameResolver.isValidHttpUrl(normalized)) {
                rejected += BatchRejectedLine(line, lineNumber, BatchLineProblem.INVALID_URL)
                continue
            }
            if (!seen.add(normalized)) {
                rejected += BatchRejectedLine(line, lineNumber, BatchLineProblem.DUPLICATE)
                continue
            }
            accepted += normalized
        }
        return BatchParseResult(urls = accepted, rejected = rejected)
    }

    /**
     * Whether the text could plausibly be a link.
     *
     * Kept deliberately loose: it only has to separate "this line is prose" from
     * "this line is a URL that is malformed", because the two get different
     * messages.
     */
    private fun looksLikeLink(value: String): Boolean {
        if (value.isEmpty()) return false
        val lower = value.lowercase()
        if (lower.startsWith("http://") || lower.startsWith("https://")) return true
        // Anything with a scheme or a space is not a bare host we can repair.
        if (value.contains(' ')) return false
        return value.contains('.')
    }
}
