package com.sohayb.n13download.core

import kotlin.random.Random

/**
 * Retry classification and backoff.
 *
 * Ported from `core/download.py`.  Two distinct regimes, and the difference
 * matters: before the first byte arrives the retry schedule is short and
 * bounded so a dead-but-accepting server cannot stall startup for minutes; once
 * bytes are flowing the configured exponential backoff applies.
 */
object RetryPolicy {

    /** Transient / server-side statuses worth another attempt. */
    val RETRYABLE_STATUS = setOf(408, 425, 429, 500, 502, 503, 504)

    private const val STARTUP_RETRY_BASE = 0.5
    private const val STARTUP_RETRY_CAP = 2.0

    /** Backoff delay in seconds for `attempt` (1-based). */
    fun delaySeconds(
        attempt: Int,
        base: Double,
        backoff: Double,
        jitter: Double,
        maxDelay: Double,
        started: Boolean,
    ): Double {
        val (effectiveBase, effectiveBackoff, cap) = if (!started) {
            Triple(minOf(STARTUP_RETRY_BASE, base), 2.0, minOf(STARTUP_RETRY_CAP, maxDelay))
        } else {
            Triple(base, backoff, maxDelay)
        }

        var delay = effectiveBase * Math.pow(effectiveBackoff, (attempt - 1).toDouble())
        delay = minOf(delay, cap)
        // Decorrelated jitter in [delay*(1-j), delay*(1+j)].
        val spread = jitter.coerceIn(0.0, 0.5)
        delay *= 1.0 - spread + Random.nextDouble() * (2.0 * spread)
        return maxOf(0.0, delay)
    }

    /**
     * Whether an HTTP status is worth retrying.
     * Other 4xx codes fail fast instead of burning the whole retry budget.
     */
    fun isRetryableStatus(status: Int): Boolean = status in RETRYABLE_STATUS

    /** Connection-level failures (timeouts, resets, DNS) are always transient. */
    fun isRetryableException(throwable: Throwable): Boolean = when (throwable) {
        is java.net.SocketTimeoutException,
        is java.net.ConnectException,
        is java.net.NoRouteToHostException,
        is java.net.UnknownHostException,
        is java.io.InterruptedIOException,
        -> true

        is java.net.SocketException -> true
        is java.io.IOException -> true
        is okhttp3.internal.http2.StreamResetException -> true
        is javax.net.ssl.SSLException -> false
        else -> false
    }

    /**
     * The status to attach to a user-facing message, if the failure came from a
     * response rather than the network layer.
     */
    fun statusOf(throwable: Throwable): Int? =
        (throwable as? HttpStatusException)?.code

    /** Raised when a response carries an unacceptable status code. */
    class HttpStatusException(val code: Int, message: String) : java.io.IOException(message)

    /** Raised when the server answers with an HTML interstitial instead of a file. */
    class HtmlInterstitialException : java.io.IOException(N13Errors.HTML_INTERSTITIAL)

    /**
     * True when the response is an HTML page rather than the requested file.
     * A real binary almost never arrives as `text/html`.
     */
    fun isHtmlInterstitial(contentType: String?): Boolean {
        val type = contentType?.substringBefore(';')?.trim()?.lowercase().orEmpty()
        return type == "text/html" || type == "application/xhtml+xml"
    }
}
