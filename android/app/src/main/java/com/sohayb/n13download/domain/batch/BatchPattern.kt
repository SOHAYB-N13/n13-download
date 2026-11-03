package com.sohayb.n13download.domain.batch

import com.sohayb.n13download.core.FilenameResolver

/** How a pattern becomes a list of URLs. */
enum class BatchPatternMode {
    /**
     * Walk the sequence and keep what exists — the Windows "sequential" scan:
     * stop after 5 consecutive 404s, after 200 attempts, or after 10 server
     * errors.
     */
    AUTO,

    /** Generate `start..end` by `step` without touching the network. */
    RANGE,
}

/** Everything the pattern panel collects. */
data class BatchPatternSpec(
    val pattern: String = "",
    val mode: BatchPatternMode = BatchPatternMode.AUTO,
    val start: Int = 1,
    val end: Int = 100,
    val step: Int = 1,
    val padding: Int = 2,
)

enum class BatchPatternProblem {
    EMPTY,
    NO_PLACEHOLDER,
    INVALID_URL,
    START_OUT_OF_RANGE,
    PADDING_OUT_OF_RANGE,
    END_BEFORE_START,
    STEP_OUT_OF_RANGE,
    TOO_MANY,
}

sealed interface BatchPatternCheck {
    data class Valid(val spec: BatchPatternSpec) : BatchPatternCheck

    data class Invalid(
        val problem: BatchPatternProblem,
        /** The offending input, so the message can quote it. */
        val detail: String = "",
    ) : BatchPatternCheck
}

/**
 * Pattern expansion for the batch screen.
 *
 * The Windows reference is `batch/pattern.py`:
 *
 * ```
 * num_str = f"{num:0{padding}d}" if padding > 0 else str(num)
 * test_url = pattern.replace("*", num_str)
 * ```
 *
 * `str.replace` substitutes **every** occurrence, so a pattern with the
 * placeholder in several places numbers all of them with the same value.  This
 * does the same, which is why a pattern with more than one placeholder is allowed
 * here even though the Windows CLI insists on exactly one: the GUI path
 * (`ui/api.py::scan_pattern`) passes the pattern straight to that `replace`, so
 * multiple placeholders already work there.
 */
object BatchPattern {

    const val PLACEHOLDER = "*"

    /** Windows caps padding at 12 in the CLI (`0 <= padding <= 12`). */
    const val MAX_PADDING = 12

    /** Same ceiling as [BatchUrls.MAX_URLS]: one batch, one bound. */
    const val MAX_GENERATED = BatchUrls.MAX_URLS

    /** Windows: `max_attempts=200` for the sequential scan. */
    const val AUTO_MAX_ATTEMPTS = 200

    /** Windows: `max_consecutive_404=5`. */
    const val AUTO_MAX_CONSECUTIVE_MISSES = 5

    /** Windows: `max_server_errors=10`. */
    const val AUTO_MAX_SERVER_ERRORS = 10

    /** Keeps `start + step * n` from overflowing while expanding. */
    const val MAX_INDEX = 100_000_000

    fun check(spec: BatchPatternSpec): BatchPatternCheck {
        val pattern = spec.pattern.trim()
        if (pattern.isEmpty()) {
            return BatchPatternCheck.Invalid(BatchPatternProblem.EMPTY)
        }
        if (!pattern.contains(PLACEHOLDER)) {
            return BatchPatternCheck.Invalid(BatchPatternProblem.NO_PLACEHOLDER)
        }
        if (spec.padding !in 0..MAX_PADDING) {
            return BatchPatternCheck.Invalid(
                BatchPatternProblem.PADDING_OUT_OF_RANGE,
                spec.padding.toString(),
            )
        }
        if (spec.start !in 0..MAX_INDEX) {
            return BatchPatternCheck.Invalid(
                BatchPatternProblem.START_OUT_OF_RANGE,
                spec.start.toString(),
            )
        }
        // The sample URL is what the user will actually request, so validating a
        // substitution catches a broken scheme/host instead of 200 failed probes.
        val sample = urlFor(pattern, spec.start, spec.padding)
        if (!FilenameResolver.isValidHttpUrl(sample)) {
            return BatchPatternCheck.Invalid(BatchPatternProblem.INVALID_URL, sample)
        }

        return when (spec.mode) {
            BatchPatternMode.AUTO -> BatchPatternCheck.Valid(spec.copy(pattern = pattern))

            BatchPatternMode.RANGE -> {
                if (spec.step < 1) {
                    return BatchPatternCheck.Invalid(
                        BatchPatternProblem.STEP_OUT_OF_RANGE,
                        spec.step.toString(),
                    )
                }
                if (spec.end !in 0..MAX_INDEX) {
                    return BatchPatternCheck.Invalid(
                        BatchPatternProblem.END_BEFORE_START,
                        spec.end.toString(),
                    )
                }
                if (spec.end < spec.start) {
                    return BatchPatternCheck.Invalid(
                        BatchPatternProblem.END_BEFORE_START,
                        spec.end.toString(),
                    )
                }
                if (rangeCount(spec) > MAX_GENERATED) {
                    return BatchPatternCheck.Invalid(
                        BatchPatternProblem.TOO_MANY,
                        rangeCount(spec).toString(),
                    )
                }
                BatchPatternCheck.Valid(spec.copy(pattern = pattern))
            }
        }
    }

    /** `f"{value:0{padding}d}"` — leading zeros, or none when padding is 0. */
    fun numberText(value: Int, padding: Int): String =
        if (padding > 0) value.toString().padStart(padding, '0') else value.toString()

    /** Substitutes every `*` with the padded number. */
    fun urlFor(pattern: String, value: Int, padding: Int): String =
        pattern.trim().replace(PLACEHOLDER, numberText(value, padding))

    /** How many URLs [expand] produces. */
    fun rangeCount(spec: BatchPatternSpec): Long {
        if (spec.end < spec.start) return 0L
        val step = spec.step.coerceAtLeast(1)
        return ((spec.end.toLong() - spec.start.toLong()) / step) + 1L
    }

    /**
     * The deterministic `start..end` list.
     *
     * No network access and no guessing: the same inputs always produce the same
     * URLs, which is what the preview shows and what gets queued.
     */
    fun expand(spec: BatchPatternSpec): List<String> {
        if (spec.end < spec.start) return emptyList()
        val step = spec.step.coerceAtLeast(1)
        val count = rangeCount(spec).coerceAtMost(MAX_GENERATED.toLong()).toInt()
        val urls = ArrayList<String>(count)
        var value = spec.start
        repeat(count) {
            urls += urlFor(spec.pattern, value, spec.padding)
            value += step
        }
        return urls
    }

    /**
     * The candidates an [BatchPatternMode.AUTO] scan will try, in order.
     *
     * The scan may stop earlier (misses, server errors); this is only the
     * ceiling, and it is what the preview lists before any network work happens.
     */
    fun autoCandidates(spec: BatchPatternSpec): List<String> {
        val step = spec.step.coerceAtLeast(1)
        val urls = ArrayList<String>(AUTO_MAX_ATTEMPTS)
        var value = spec.start
        repeat(AUTO_MAX_ATTEMPTS) {
            urls += urlFor(spec.pattern, value, spec.padding)
            value += step
        }
        return urls
    }

    /** The list a mode can produce without probing, for the live counter. */
    fun plannedCount(spec: BatchPatternSpec): Int = when (spec.mode) {
        BatchPatternMode.RANGE -> rangeCount(spec).coerceAtMost(MAX_GENERATED.toLong()).toInt()
        BatchPatternMode.AUTO -> AUTO_MAX_ATTEMPTS
    }
}
