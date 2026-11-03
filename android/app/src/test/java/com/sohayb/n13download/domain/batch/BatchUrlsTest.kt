package com.sohayb.n13download.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * URL-list parsing for the batch screen.
 *
 * The rules being pinned here are the ones a user notices when they are wrong:
 * which lines become downloads, which are reported, and in what order.  The
 * Windows reference is `batch/sources.py::load_url_list` plus the Batch page's
 * own filter, and the ordering rule (first occurrence wins) is the one that
 * decides which of two identical links a queued task belongs to.
 */
class BatchUrlsTest {

    @Test
    fun `one link per line is accepted in input order`() {
        val result = BatchUrls.parse(
            """
            https://example.com/a.zip
            https://example.com/b.zip
            https://example.com/c.zip
            """.trimIndent(),
        )

        assertEquals(
            listOf(
                "https://example.com/a.zip",
                "https://example.com/b.zip",
                "https://example.com/c.zip",
            ),
            result.urls,
        )
        assertEquals(3, result.total)
        assertTrue(result.rejected.isEmpty())
    }

    @Test
    fun `blank lines are ignored and never reported`() {
        val result = BatchUrls.parse("https://example.com/a.zip\n\n   \n\t\nhttps://example.com/b.zip")

        assertEquals(2, result.total)
        assertTrue("blank lines must not be reported as problems", result.rejected.isEmpty())
    }

    @Test
    fun `a bare domain is normalised to https like everywhere else in the app`() {
        val result = BatchUrls.parse("example.com/files/a.zip")

        assertEquals(listOf("https://example.com/files/a.zip"), result.urls)
    }

    @Test
    fun `surrounding quotes and whitespace are trimmed`() {
        val result = BatchUrls.parse("   \"https://example.com/a.zip\"   ")

        assertEquals(listOf("https://example.com/a.zip"), result.urls)
    }

    @Test
    fun `duplicates are removed preserving the first occurrence`() {
        val result = BatchUrls.parse(
            """
            https://example.com/a.zip
            https://example.com/b.zip
            https://example.com/a.zip
            """.trimIndent(),
        )

        assertEquals(
            "the first occurrence must win, in its original position",
            listOf("https://example.com/a.zip", "https://example.com/b.zip"),
            result.urls,
        )
        assertEquals(1, result.duplicateCount)
        val duplicate = result.rejected.single()
        assertEquals(BatchLineProblem.DUPLICATE, duplicate.problem)
        assertEquals(3, duplicate.lineNumber)
    }

    @Test
    fun `duplicates are detected after normalisation`() {
        // The same resource written three ways must collapse to one entry,
        // otherwise a user pasting from two places gets two downloads.
        val result = BatchUrls.parse(
            """
            https://example.com/a.zip
            example.com/a.zip
            "https://example.com/a.zip"
            """.trimIndent(),
        )

        assertEquals(1, result.total)
        assertEquals(2, result.duplicateCount)
    }

    @Test
    fun `prose is reported as not a link`() {
        val result = BatchUrls.parse("see the notes below\nhttps://example.com/a.zip")

        assertEquals(1, result.total)
        val problem = result.rejected.single()
        assertEquals(BatchLineProblem.NOT_A_LINK, problem.problem)
        assertEquals(1, problem.lineNumber)
        assertEquals("see the notes below", problem.line)
    }

    @Test
    fun `a link with no host is reported as an invalid url`() {
        val result = BatchUrls.parse("https://")

        assertEquals(0, result.total)
        assertEquals(BatchLineProblem.INVALID_URL, result.rejected.single().problem)
        assertEquals(1, result.invalidCount)
    }

    @Test
    fun `a non http scheme is reported rather than queued`() {
        val result = BatchUrls.parse("ftp://example.com/a.zip")

        assertEquals(0, result.total)
        assertEquals(BatchLineProblem.INVALID_URL, result.rejected.single().problem)
    }

    @Test
    fun `counters separate invalid from duplicate`() {
        val result = BatchUrls.parse(
            """
            https://example.com/a.zip
            https://example.com/a.zip
            not a link at all
            https://
            """.trimIndent(),
        )

        assertEquals(1, result.total)
        assertEquals(1, result.duplicateCount)
        assertEquals(2, result.invalidCount)
        assertEquals(3, result.rejected.size)
    }

    @Test
    fun `the list is capped and the overflow is reported`() {
        val lines = (1..BatchUrls.MAX_URLS + 5).joinToString("\n") { "https://example.com/$it.zip" }

        val result = BatchUrls.parse(lines)

        assertEquals(BatchUrls.MAX_URLS, result.total)
        assertEquals(5, result.overflowCount)
        assertTrue(
            "overflow must be reported, not silently dropped",
            result.rejected.all { it.problem == BatchLineProblem.TOO_MANY },
        )
    }

    @Test
    fun `an empty paste is empty and reports nothing`() {
        val result = BatchUrls.parse("   \n\n ")

        assertTrue(result.isEmpty)
        assertTrue(result.rejected.isEmpty())
    }

    @Test
    fun `preview rejects is limited`() {
        val text = (1..10).joinToString("\n") { "prose $it" }

        val result = BatchUrls.parse(text)

        assertEquals(4, result.previewRejects().size)
        assertEquals(10, result.previewRejects(limit = 10).size)
    }

    @Test
    fun `parseCandidates numbers entries so a message can point at one`() {
        val result = BatchUrls.parseCandidates(listOf("https://example.com/a.zip", "nope"))

        assertEquals(1, result.total)
        assertEquals(2, result.rejected.single().lineNumber)
    }

    @Test
    fun `a query string survives normalisation untouched`() {
        // The query is part of the resource identity: two links that differ only
        // in their query are different downloads, never duplicates.
        val result = BatchUrls.parse(
            """
            https://example.com/get?file=a.zip
            https://example.com/get?file=b.zip
            """.trimIndent(),
        )

        assertEquals(2, result.total)
        assertFalse(result.urls[0] == result.urls[1])
        assertEquals("https://example.com/get?file=a.zip", result.urls[0])
    }
}
