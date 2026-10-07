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

    suspend fun probe(
        url: String,
        settings: DownloadSettings,
        headOnly: Boolean = false,
    ): DownloadAnalysis = withContext(Dispatchers.IO) {
        val client = OkHttpClients.probe(settings)
        val userAgent = settings.userAgent

        // ---- Strategy A: HEAD ------------------------------------------
        when (val head = runHead(client, url, userAgent)) {
            is HeadResult.Done -> return@withContext head.analysis
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
                if (code >= 400 || code == 405) return HeadResult.Retry
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
                    return failure(url, "HTTP ${response.code}", finalUrl)
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
