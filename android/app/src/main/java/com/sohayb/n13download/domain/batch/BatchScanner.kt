package com.sohayb.n13download.domain.batch

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.util.concurrent.atomic.AtomicInteger

/** What one probe of one generated URL found. */
data class BatchProbeOutcome(
    val reachable: Boolean,
    val statusCode: Int = 0,
    val size: Long = 0L,
    val error: String = "",
)

/** Probe abstraction, so the scan can be unit-tested without a network. */
fun interface BatchProbe {
    suspend fun probe(url: String): BatchProbeOutcome
}

/** The Windows `_classify` buckets, derived from a status code where possible. */
enum class BatchMissKind { NOT_FOUND, TIMEOUT, SERVER, OTHER }

enum class BatchScanStop {
    /** The range was walked, or the sequence ended on its own terms. */
    FINISHED,

    /** [BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES] misses in a row — the Windows stop rule. */
    CONSECUTIVE_MISSES,

    /** [BatchPattern.AUTO_MAX_ATTEMPTS] probes without finding the end. */
    ATTEMPT_LIMIT,

    /** [BatchPattern.AUTO_MAX_SERVER_ERRORS] server errors — the host is unhealthy. */
    SERVER_ERRORS,

    /** The user cancelled. */
    CANCELLED,
}

data class BatchScanProgress(
    val attempted: Int,
    val found: Int,
    val consecutiveMisses: Int,
    val url: String,
    val reachable: Boolean,
    val missKind: BatchMissKind? = null,
)

data class BatchScanResult(
    val urls: List<String> = emptyList(),
    val attempted: Int = 0,
    val stop: BatchScanStop = BatchScanStop.FINISHED,
)

/**
 * Probes generated URLs.
 *
 * [scanAuto] is a straight port of `batch/pattern.py::scan_pattern_urls`: the
 * same three stop rules, the same "a success resets both failure counters"
 * behaviour, and the same treatment of timeouts (they neither count as a miss
 * nor reset the counter).  Keeping it identical matters because the result is
 * what gets queued — a looser rule would scan a dead host for 200 requests.
 *
 * [scanRange] is the range counterpart (`scan_regex_urls`): every generated URL
 * is probed and only the reachable ones survive.  Unlike the Windows version it
 * runs a few probes at a time, because a phone round-trip is ~150 ms and a
 * 100-entry range would otherwise take 15 s of dead time.
 */
class BatchScanner(
    private val probe: BatchProbe,
    private val onProgress: suspend (BatchScanProgress) -> Unit = {},
) {

    suspend fun scanAuto(spec: BatchPatternSpec): BatchScanResult {
        val step = spec.step.coerceAtLeast(1)
        val found = ArrayList<String>()
        var value = spec.start
        var attempts = 0
        var consecutiveMisses = 0
        var serverErrors = 0
        var stop = BatchScanStop.ATTEMPT_LIMIT

        while (consecutiveMisses < BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES &&
            attempts < BatchPattern.AUTO_MAX_ATTEMPTS
        ) {
            currentCoroutineContextActive()
            attempts++

            val url = BatchPattern.urlFor(spec.pattern, value, spec.padding)
            val outcome = probe.probe(url)
            val kind = if (outcome.reachable) null else classify(outcome)

            if (outcome.reachable) {
                found += url
                consecutiveMisses = 0
                serverErrors = 0
            } else {
                when (kind) {
                    BatchMissKind.NOT_FOUND -> consecutiveMisses++
                    BatchMissKind.SERVER -> {
                        serverErrors++
                        if (serverErrors >= BatchPattern.AUTO_MAX_SERVER_ERRORS) {
                            stop = BatchScanStop.SERVER_ERRORS
                        }
                    }

                    // A timeout is inconclusive: it neither advances the "the
                    // sequence has ended" counter nor resets it.
                    BatchMissKind.TIMEOUT, BatchMissKind.OTHER, null -> Unit
                }
            }

            onProgress(
                BatchScanProgress(
                    attempted = attempts,
                    found = found.size,
                    consecutiveMisses = consecutiveMisses,
                    url = url,
                    reachable = outcome.reachable,
                    missKind = kind,
                ),
            )

            if (stop == BatchScanStop.SERVER_ERRORS) break

            value += step
        }

        if (stop != BatchScanStop.SERVER_ERRORS) {
            stop = when {
                consecutiveMisses >= BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES ->
                    BatchScanStop.CONSECUTIVE_MISSES

                attempts >= BatchPattern.AUTO_MAX_ATTEMPTS -> BatchScanStop.ATTEMPT_LIMIT
                else -> BatchScanStop.FINISHED
            }
        }
        return BatchScanResult(urls = found, attempted = attempts, stop = stop)
    }

    suspend fun scanRange(
        spec: BatchPatternSpec,
        concurrency: Int = DEFAULT_CONCURRENCY,
    ): BatchScanResult = coroutineScope {
        val candidates = BatchPattern.expand(spec)
        if (candidates.isEmpty()) return@coroutineScope BatchScanResult()

        val keep = arrayOfNulls<Boolean>(candidates.size)
        val gate = Semaphore(concurrency.coerceIn(1, MAX_CONCURRENCY))
        val attempted = AtomicInteger(0)
        val found = AtomicInteger(0)

        candidates.mapIndexed { index, url ->
            async(Dispatchers.IO) {
                gate.withPermit {
                    val outcome = probe.probe(url)
                    keep[index] = outcome.reachable
                    val tried = attempted.incrementAndGet()
                    val hits = if (outcome.reachable) found.incrementAndGet() else found.get()
                    onProgress(
                        BatchScanProgress(
                            attempted = tried,
                            found = hits,
                            consecutiveMisses = 0,
                            url = url,
                            reachable = outcome.reachable,
                            missKind = if (outcome.reachable) null else classify(outcome),
                        ),
                    )
                }
            }
        }.awaitAll()

        BatchScanResult(
            urls = candidates.filterIndexed { index, _ -> keep[index] == true },
            attempted = candidates.size,
            stop = BatchScanStop.FINISHED,
        )
    }

    private fun classify(outcome: BatchProbeOutcome): BatchMissKind {
        val code = outcome.statusCode
        if (code > 0) {
            return when {
                code == 404 || code == 410 -> BatchMissKind.NOT_FOUND
                code >= 500 -> BatchMissKind.SERVER
                code == 408 || code == 429 -> BatchMissKind.TIMEOUT
                else -> BatchMissKind.OTHER
            }
        }
        // No status code: fall back to the Windows message classifier, which is
        // what the probe's own error text looks like.
        val message = outcome.error.lowercase()
        return when {
            message.contains("404") || message.contains("not found") -> BatchMissKind.NOT_FOUND
            message.contains("timeout") || message.contains("timed out") -> BatchMissKind.TIMEOUT
            message.startsWith("http 5") || message.contains("server error") -> BatchMissKind.SERVER
            else -> BatchMissKind.OTHER
        }
    }

    private suspend fun currentCoroutineContextActive() {
        kotlinx.coroutines.currentCoroutineContext().ensureActive()
    }

    private companion object {
        /** Enough to hide the per-request latency without hammering a host. */
        const val DEFAULT_CONCURRENCY = 4
        const val MAX_CONCURRENCY = 16
    }
}
