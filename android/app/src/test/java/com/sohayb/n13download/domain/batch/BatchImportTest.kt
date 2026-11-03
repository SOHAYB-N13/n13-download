package com.sohayb.n13download.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Importing a saved URL list.
 *
 * The Windows reference is `batch/sources.py::load_url_list`, which accepts JSON,
 * CSV and plain text and falls back to scanning for `http(s)://` runs.  What
 * matters here is that a file the user saved is read the same way the desktop
 * app would read it — a `name,url` CSV must not queue the word "name".
 */
class BatchImportTest {

    @Test
    fun `a json urls array is read`() {
        val json = """
            {"version": 1, "urls": ["https://example.com/a.zip", "https://example.com/b.zip"]}
        """.trimIndent()

        val result = BatchImport.parse(json, "list.json")

        assertEquals(
            listOf("https://example.com/a.zip", "https://example.com/b.zip"),
            result.urls,
        )
    }

    @Test
    fun `a bare json array is read`() {
        val result = BatchImport.parse(
            """["https://example.com/a.zip", "https://example.com/b.zip"]""",
            "list.json",
        )

        assertEquals(2, result.total)
    }

    @Test
    fun `a json list of objects is read by its url field`() {
        val json = """
            [
              {"url": "https://example.com/a.zip", "name": "a"},
              {"url": "https://example.com/b.zip", "name": "b"}
            ]
        """.trimIndent()

        val result = BatchImport.parse(json, "list.json")

        assertEquals(
            listOf("https://example.com/a.zip", "https://example.com/b.zip"),
            result.urls,
        )
    }

    @Test
    fun `escaped slashes in json are unescaped`() {
        val json = """{"urls": ["https:\/\/example.com\/a.zip"]}"""

        assertEquals(listOf("https://example.com/a.zip"), BatchImport.parse(json, "list.json").urls)
    }

    @Test
    fun `a csv with a header queues only the links`() {
        val csv = """
            name,url
            first,https://example.com/a.zip
            second,https://example.com/b.zip
        """.trimIndent()

        val result = BatchImport.parse(csv, "list.csv")

        assertEquals(
            listOf("https://example.com/a.zip", "https://example.com/b.zip"),
            result.urls,
        )
        // "name", "url", "first", "second" — every non-link cell is reported, so
        // the user can see what the file contained that was not queued.
        assertEquals(4, result.invalidCount)
    }

    @Test
    fun `a semicolon separated csv is read`() {
        val result = BatchImport.parse(
            "https://example.com/a.zip;https://example.com/b.zip",
            "list.csv",
        )

        assertEquals(2, result.total)
    }

    @Test
    fun `a plain text file is read one link per line`() {
        val text = """
            https://example.com/a.zip
            # a comment that is not a link
            https://example.com/b.zip
        """.trimIndent()

        val result = BatchImport.parse(text, "list.txt")

        assertEquals(2, result.total)
        assertEquals(1, result.invalidCount)
    }

    @Test
    fun `an unknown extension falls back to finding every link`() {
        val html = """
            <html><body>
            <a href="https://example.com/a.zip">a</a>
            <a href="https://example.com/b.zip">b</a>
            </body></html>
        """.trimIndent()

        val result = BatchImport.parse(html, "page.html")

        assertEquals(
            listOf("https://example.com/a.zip", "https://example.com/b.zip"),
            result.urls,
        )
    }

    @Test
    fun `the extension is matched case insensitively`() {
        val result = BatchImport.parse("""{"urls": ["https://example.com/a.zip"]}""", "LIST.JSON")

        assertEquals(1, result.total)
    }

    @Test
    fun `a file with no links yields nothing`() {
        val result = BatchImport.parse("just some prose\nand nothing else", "notes.txt")

        assertTrue(result.isEmpty)
    }

    @Test
    fun `an empty file yields nothing`() {
        assertTrue(BatchImport.parse("", "list.txt").isEmpty)
        assertTrue(BatchImport.parse("   \n\n", "list.json").isEmpty)
    }

    @Test
    fun `duplicates across formats collapse`() {
        val json = """
            {"urls": ["https://example.com/a.zip", "https://example.com/a.zip"]}
        """.trimIndent()

        val result = BatchImport.parse(json, "list.json")

        assertEquals(1, result.total)
        assertEquals(1, result.duplicateCount)
    }

    @Test
    fun `a file without an extension is read as text`() {
        val result = BatchImport.parse("https://example.com/a.zip", "urls")

        assertEquals(listOf("https://example.com/a.zip"), result.urls)
    }
}
