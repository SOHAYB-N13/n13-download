package com.sohayb.n13download.data.engine

import android.util.Log
import com.sohayb.n13download.core.BrowserHeaders
import com.sohayb.n13download.core.FilenameResolver
import com.sohayb.n13download.core.N13Errors
import com.sohayb.n13download.core.RetryPolicy
import com.sohayb.n13download.domain.model.DownloadAnalysis
import com.sohayb.n13download.domain.model.DownloadSettings
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Link inspection with multi-strategy fallback.
 *
 * Ported from `core/probe.py`: **HEAD -> ranged GET (1 byte) -> plain GET**.
 * Many hosts block or mis-answer HEAD, so a failure there must never fail the
 * whole probe.  The probe only ever reads headers; it never downloads the body.
 */
object HttpProbe {

    private const val TAG = "N13Probe"

    /**
     * Below this size a segmented download is not worth its own handshake, so the
     * extra one-byte request that proves Range support is skipped.
     */
    private const val RANGE_PROBE_MIN_SIZE = 1L * 1024 * 1024

    /**
     * Status codes a HEAD may return *because it is a HEAD*.
     *
     * Servers reject the method, throttle it, or answer it differently from a GET;
     * for these the ranged GET is a genuinely better source.  Every other 4xx/5xx
     * (404, 410, 500, 502 …) is the server's real answer about the resource, and
     * spending the rest of the probe budget to hear it twice is what made a dead
     * link take three round trips to report.
     */
    private val HEAD_MAY_BE_BLOCKED = setOf(400, 401, 403, 405, 406, 429, 501)

    suspend fun probe(
        url: String,
        settings: DownloadSettings,
        headOnly: Boolean = false,
    ): DownloadAnalysis = withContext(Dispatchers.IO) {
        val client = OkHttpClients.probe(settings)
        val userAgent = settings.userAgent

        // ---- Strategy A: HEAD ------------------------------------------
        when (val head = runHead(client, url, userAgent)) {
            is HeadResult.Done -> return@withContext verifyRangeSupport(
                client = client,
                url = url,
                userAgent = userAgent,
                analysis = head.analysis,
                enabled = !headOnly,
            )
            // Timeout / connection error / 405 / HTML: fall through.
            HeadResult.Retry -> Unit
        }
        if (headOnly) return@withContext failure(url, "Could not inspect this link")

        // ---- Strategy B: ranged GET (1 byte) ---------------------------
        runRangedGet(client, url, userAgent)
    }

    private sealed interface HeadResult {
        data class Done(val analysis: DownloadAnalysis) : HeadResult
        data object Retry : HeadResult
    }

    /** `"HTTP 404 - Not Found"`, matching the wording the Windows build reports. */
    private fun describe(code: Int, reason: String): String =
        if (reason.isBlank()) "HTTP $code" else "HTTP $code - $reason"

    /**
     * Spends one byte to find out whether ranges really work.
     *
     * `Accept-Ranges` is advisory and is missing on plenty of hosts that *do*
     * honour a `Range` header, while some hosts advertise it and then answer a
     * ranged request with `200`.  Either way the decision is made from the
     * response, not the advertisement: only a `206` counts as support, and its
     * `Content-Range` total is authoritative enough to correct a wrong
     * `Content-Length` from the HEAD.
     *
     * Only worth a request when the file is big enough to be split at all.
     */
    private fun verifyRangeSupport(
        client: OkHttpClient,
        url: String,
        userAgent: String,
        analysis: DownloadAnalysis,
        enabled: Boolean,
    ): DownloadAnalysis {
        if (!enabled || !analysis.ok) return analysis
        if (analysis.supportsRange || analysis.totalSize < RANGE_PROBE_MIN_SIZE) return analysis

        val request = Request.Builder()
            .url(url)
            .headers(
                toOkHttpHeaders(
                    BrowserHeaders.build(url, userAgent, rangeHeader = "bytes=0-0", accept = "*/*"),
                ),
            )
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                // 200 means the Range header was ignored: no segmented download.
                if (response.code != 206) return analysis

                val contentRange = response.headers.toFlatMap().header("Content-Range").orEmpty()
                val provenSize = contentRange
                    .substringAfter('/', "")
                    .trim()
                    .toLongOrNull()
                    ?: 0L

                analysis.copy(
                    supportsRange = true,
                    totalSize = if (provenSize > 0L) provenSize else analysis.totalSize,
                )
            }
        } catch (io: IOException) {
            // The HEAD already succeeded; a failed verification only means we
            // keep the conservative single-connection plan.
            Log.d(TAG, "Range verification failed (${io.javaClass.simpleName}), assuming no ranges")
            analysis
        } catch (unexpected: Exception) {
            Log.d(TAG, "Range verification failed unexpectedly, assuming no ranges", unexpected)
            analysis
        }
    }

    private fun runHead(client: OkHttpClient, url: String, userAgent: String): HeadResult {
        val request = Request.Builder()
            .url(url)
            .headers(toOkHttpHeaders(BrowserHeaders.build(url, userAgent, accept = "*/*")))
            .head()
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                val code = response.code
                val headers = response.headers.toFlatMap()
                val finalUrl = response.request.url.toString()

                // A HEAD the server does not support tells us nothing about the file.
                if (code >= 400) {
                    return if (code in HEAD_MAY_BE_BLOCKED) {
                        HeadResult.Retry
                    } else {
                        // A definitive error — the server has said what it thinks.
                        HeadResult.Done(
                            failure(url, describe(code, response.message), finalUrl),
                        )
                    }
                }
                // An HTML answer to a HEAD may still be a real file on GET.
                if (RetryPolicy.isHtmlInterstitial(headers["Content-Type"])) return HeadResult.Retry

                HeadResult.Done(build(url, finalUrl, headers, code))
            }
        } catch (ssl: SSLException) {
            HeadResult.Done(failure(url, "SSL certificate verification failed"))
        } catch (_: SocketTimeoutException) {
            HeadResult.Retry
        } catch (io: IOException) {
            Log.d(TAG, "HEAD probe failed (${io.javaClass.simpleName}), trying a ranged GET")
            HeadResult.Retry
        } catch (unexpected: Exception) {
            Log.d(TAG, "HEAD probe failed unexpectedly, trying a ranged GET", unexpected)
            HeadResult.Retry
        }
    }

    private fun runRangedGet(
        client: OkHttpClient,
        url: String,
        userAgent: String,
    ): DownloadAnalysis {
        val rangedRequest = Request.Builder()
            .url(url)
            .headers(
                toOkHttpHeaders(
                    BrowserHeaders.build(url, userAgent, rangeHeader = "bytes=0-0", accept = "*/*"),
                ),
            )
            .build()

        try {
            client.newCall(rangedRequest).execute().use { response ->
                val headers = response.headers.toFlatMap()
                val finalUrl = response.request.url.toString()

                if (response.code >= 400) {
                    // The server rejected the Range request outright; try plainly.
                    return plainGet(client, url, userAgent, response.code)
                }
                if (RetryPolicy.isHtmlInterstitial(headers["Content-Type"])) {
                    return failure(url, N13Errors.HTML_INTERSTITIAL, finalUrl)
                }
                return build(url, finalUrl, headers, response.code)
            }
        } catch (ssl: SSLException) {
            return failure(url, "SSL certificate verification failed")
        } catch (_: SocketTimeoutException) {
            return failure(url, "Connection timed out - the server did not respond")
        } catch (_: UnknownHostException) {
            return failure(url, "Cannot resolve the server address")
        } catch (io: IOException) {
            return failure(url, N13Errors.friendly(io))
        } catch (unexpected: Exception) {
            return failure(url, N13Errors.friendly(unexpected))
        }
    }

    private fun plainGet(
        client: OkHttpClient,
        url: String,
        userAgent: String,
        firstCode: Int,
    ): DownloadAnalysis {
        val request = Request.Builder()
            .url(url)
            .headers(toOkHttpHeaders(BrowserHeaders.build(url, userAgent, accept = "*/*")))
            .build()
        return try {
            client.newCall(request).execute().use { response ->
                val headers = response.headers.toFlatMap()
                val finalUrl = response.request.url.toString()
                if (response.code >= 400) {
                    return failure(url, describe(response.code, response.message), finalUrl)
                }
                if (RetryPolicy.isHtmlInterstitial(headers["Content-Type"])) {
                    return failure(url, N13Errors.HTML_INTERSTITIAL, finalUrl)
                }
                build(url, finalUrl, headers, response.code)
            }
        } catch (io: IOException) {
            failure(url, N13Errors.friendly(io, firstCode))
        }
    }

    /** Turns response headers into a [DownloadAnalysis]. */
    fun build(
        url: String,
        finalUrl: String,
        headers: Map<String, String>,
        statusCode: Int,
    ): DownloadAnalysis {
        val (totalSize, supportsRange) = parseSizeAndRange(headers, statusCode)
        val contentType = headers.header("Content-Type").orEmpty()
        val wwwAuthenticate = headers.header("WWW-Authenticate")

        var analysis = DownloadAnalysis(
            ok = true,
            url = url,
            finalUrl = finalUrl,
            totalSize = totalSize,
            supportsRange = supportsRange,
            filename = FilenameResolver.fromResponse(headers, url, finalUrl),
            contentType = contentType,
            server = headers.header("Server").orEmpty(),
            etag = headers.header("ETag").orEmpty(),
            lastModified = headers.header("Last-Modified").orEmpty(),
            statusCode = statusCode,
            authRequired = wwwAuthenticate != null,
            checksumAvailable = headers.header("X-Checksum-MD5") != null ||
                headers.header("X-Checksum-Sha256") != null ||
                headers.header("X-File-MD5") != null,
            probedAt = System.currentTimeMillis(),
        )

        if (RetryPolicy.isHtmlInterstitial(contentType)) {
            analysis = analysis.copy(ok = false, error = N13Errors.HTML_INTERSTITIAL)
        }
        return analysis
    }

    /**
     * `(totalSize, supportsRange)`.
     *
     * A 206 proves Range support and carries the true total in `Content-Range`.
     * Otherwise `Accept-Ranges` is inspected token by token, because CDNs
     * concatenate it into values like `bytes, bytes`.
     */
    fun parseSizeAndRange(headers: Map<String, String>, statusCode: Int): Pair<Long, Boolean> {
        var supportsRange = statusCode == 206
        var totalSize = 0L

        if (supportsRange) {
            val contentRange = headers.header("Content-Range").orEmpty()
            if (contentRange.startsWith("bytes")) {
                totalSize = contentRange.substringAfter('/', "").trim().toLongOrNull() ?: 0L
            }
        }

        if (totalSize <= 0L) {
            totalSize = headers.header("Content-Length")?.trim()?.toLongOrNull() ?: 0L
        }

        if (!supportsRange) {
            val acceptRanges = headers.header("Accept-Ranges").orEmpty()
            supportsRange = acceptRanges.split(',').any { it.trim().equals("bytes", ignoreCase = true) }
        }

        return totalSize to supportsRange
    }

    private fun failure(url: String, reason: String, finalUrl: String = ""): DownloadAnalysis =
        DownloadAnalysis(
            ok = false,
            url = url,
            finalUrl = finalUrl,
            error = reason,
            probedAt = System.currentTimeMillis(),
        )

    private fun toOkHttpHeaders(map: Map<String, String>): Headers =
        Headers.Builder().apply {
            map.forEach { (name, value) -> add(name, value) }
        }.build()

    /** Case-insensitive header lookup; HTTP header names are not case-sensitive. */
    fun Map<String, String>.header(name: String): String? =
        entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value

    fun Headers.toFlatMap(): Map<String, String> {
        val result = LinkedHashMap<String, String>(size)
        for (index in 0 until size) {
            result[name(index)] = value(index)
        }
        return result
    }
}
