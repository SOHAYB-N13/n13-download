package com.sohayb.n13download.core

/**
 * Browser-like request headers.
 *
 * Ported from `build_browser_headers` / `best_referer_for` in `core/utils.py`.
 * Many CDNs and file hosts reject requests that look like a bare client, so the
 * full header set is sent.
 *
 * `Accept-Encoding: identity` is deliberate and load-bearing: it stops the server
 * from gzip-ing a binary stream, which would break both `Content-Length` and
 * byte-range arithmetic.
 */
object BrowserHeaders {

    /** N13's default user agent, kept identical to the desktop product. */
    const val DEFAULT_USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    /**
     * Some hosts / CDNs require a specific Referer — usually the marketing site
     * rather than the CDN origin — or they return a small HTML landing page
     * instead of the real binary.
     */
    private val REFERER_OVERRIDES = mapOf(
        "drivers.amd.com" to "https://www.amd.com/",
        "download.amd.com" to "https://www.amd.com/",
        "us.download.nvidia.com" to "https://www.nvidia.com/",
        "download.nvidia.com" to "https://www.nvidia.com/",
        "international.download.nvidia.com" to "https://www.nvidia.com/",
        "sourceforge.net" to "https://sourceforge.net/",
        "downloads.sourceforge.net" to "https://sourceforge.net/",
        "github.com" to "https://github.com/",
        "objects.githubusercontent.com" to "https://github.com/",
        "release-assets.githubusercontent.com" to "https://github.com/",
        "download.softpedia.com" to "https://www.softpedia.com/",
    )

    fun bestRefererFor(url: String): String? {
        val host = FilenameResolver.hostOf(url).lowercase()
        REFERER_OVERRIDES[host]?.let { return it }
        return FilenameResolver.originOf(url)
    }

    /**
     * @param rangeHeader e.g. `bytes=1024-2047`, or null for a full request.
     * @param segmentRequest when true the `Sec-Fetch-*` headers are switched to
     *   the values N13 uses for range requests (a subresource, not a navigation).
     */
    fun build(
        url: String,
        userAgent: String = DEFAULT_USER_AGENT,
        rangeHeader: String? = null,
        accept: String = "*/*",
        segmentRequest: Boolean = false,
    ): Map<String, String> {
        val headers = linkedMapOf(
            "User-Agent" to userAgent,
            "Accept" to accept,
            "Accept-Language" to "en-US,en;q=0.9",
            // Load-bearing: keeps binary streams uncompressed.
            "Accept-Encoding" to "identity",
            "Cache-Control" to "no-cache",
            "Pragma" to "no-cache",
            "Connection" to "keep-alive",
        )

        if (segmentRequest) {
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "no-cors"
            headers["Sec-Fetch-Site"] = "same-origin"
        } else {
            headers["Upgrade-Insecure-Requests"] = "1"
            headers["Sec-Fetch-Dest"] = "document"
            headers["Sec-Fetch-Mode"] = "navigate"
            headers["Sec-Fetch-Site"] = "none"
            headers["Sec-Fetch-User"] = "?1"
        }

        bestRefererFor(url)?.let { referer ->
            headers["Referer"] = referer
            FilenameResolver.originOf(referer)?.let { origin ->
                headers["Origin"] = origin.trimEnd('/')
            }
        }

        if (rangeHeader != null) headers["Range"] = rangeHeader
        return headers
    }
}
