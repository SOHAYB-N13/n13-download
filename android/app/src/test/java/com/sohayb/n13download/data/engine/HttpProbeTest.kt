package com.sohayb.n13download.data.engine

import com.sohayb.n13download.data.engine.HttpProbe.header
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Probe response parsing.
 *
 * This is the part of the probe that decides whether a download gets one
 * connection or several, and it is the part that used to get it wrong: a CDN
 * that answers `Accept-Ranges: bytes, bytes` or one that omits the header but
 * honours `Range` both ended up single-stream.  The rules are the Windows ones
 * (`core/probe.py::_metadata_from_headers` and `_verify_range_support`).
 */
class HttpProbeTest {

    // ---- Size and range discovery ----------------------------------------

    @Test
    fun `a 206 answer proves range support and carries the true total`() {
        val (size, range) = HttpProbe.parseSizeAndRange(
            mapOf(
                "Content-Range" to "bytes 0-0/1234567",
                // Deliberately wrong: Content-Range is authoritative.
                "Content-Length" to "1",
            ),
            statusCode = 206,
        )

        assertEquals(1_234_567L, size)
        assertTrue(range)
    }

    @Test
    fun `a 200 answer with accept-ranges advertises range support`() {
        val (size, range) = HttpProbe.parseSizeAndRange(
            mapOf("Content-Length" to "500", "Accept-Ranges" to "bytes"),
            statusCode = 200,
        )

        assertEquals(500L, size)
        assertTrue(range)
    }

    @Test
    fun `a concatenated accept-ranges value still counts`() {
        // CDNs really do send this.
        val (_, range) = HttpProbe.parseSizeAndRange(
            mapOf("Content-Length" to "10", "Accept-Ranges" to "bytes, bytes"),
            statusCode = 200,
        )

        assertTrue(range)
    }

    @Test
    fun `accept-ranges none means no range support`() {
        val (_, range) = HttpProbe.parseSizeAndRange(
            mapOf("Content-Length" to "10", "Accept-Ranges" to "none"),
            statusCode = 200,
        )

        assertFalse(range)
    }

    @Test
    fun `a missing accept-ranges is not range support on its own`() {
        // This is exactly the case the one-byte verification exists for: the
        // header is advisory, so the engine probes before trusting it either way.
        val (size, range) = HttpProbe.parseSizeAndRange(
            mapOf("Content-Length" to "2048"),
            statusCode = 200,
        )

        assertEquals(2048L, size)
        assertFalse(range)
    }

    @Test
    fun `a chunked response has no known size`() {
        val (size, range) = HttpProbe.parseSizeAndRange(
            mapOf("Transfer-Encoding" to "chunked"),
            statusCode = 200,
        )

        assertEquals(0L, size)
        assertFalse(range)
    }

    @Test
    fun `a malformed content-range does not crash the probe`() {
        val (size, range) = HttpProbe.parseSizeAndRange(
            mapOf("Content-Range" to "bytes 0-0/*", "Content-Length" to "77"),
            statusCode = 206,
        )

        // The 206 still proves ranges; only the total falls back to Content-Length.
        assertEquals(77L, size)
        assertTrue(range)
    }

    // ---- Analysis building ------------------------------------------------

    @Test
    fun `an analysis carries the filename, size and server`() {
        val analysis = HttpProbe.build(
            url = "https://example.com/files/report.pdf",
            finalUrl = "https://example.com/files/report.pdf",
            headers = mapOf(
                "Content-Type" to "application/pdf",
                "Content-Length" to "4096",
                "Server" to "nginx",
                "ETag" to "\"abc\"",
                "Accept-Ranges" to "bytes",
            ),
            statusCode = 200,
        )

        assertTrue(analysis.ok)
        assertEquals("report.pdf", analysis.filename)
        assertEquals(4096L, analysis.totalSize)
        assertEquals("application/pdf", analysis.contentType)
        assertEquals("nginx", analysis.server)
        assertTrue(analysis.supportsRange)
        assertTrue(analysis.resumable)
    }

    @Test
    fun `an html answer is not a download`() {
        val analysis = HttpProbe.build(
            url = "https://example.com/download",
            finalUrl = "https://example.com/download",
            headers = mapOf("Content-Type" to "text/html; charset=utf-8"),
            statusCode = 200,
        )

        assertFalse("an HTML interstitial must never be treated as the file", analysis.ok)
    }

    @Test
    fun `a 401 marks the analysis as needing authentication`() {
        val analysis = HttpProbe.build(
            url = "https://example.com/secret.zip",
            finalUrl = "https://example.com/secret.zip",
            headers = mapOf("WWW-Authenticate" to "Basic realm=\"x\""),
            statusCode = 401,
        )

        assertTrue(analysis.authRequired)
    }

    @Test
    fun `header lookup is case insensitive`() {
        // HTTP header names are not case-sensitive, and servers are inconsistent
        // about them; a case-sensitive lookup silently loses Content-Length.
        val headers = mapOf("content-length" to "123", "ACCEPT-RANGES" to "bytes")

        assertEquals("123", headers.header("Content-Length"))
        assertEquals("bytes", headers.header("Accept-Ranges"))
        assertEquals(null, headers.header("Content-Encoding"))
    }
}
