package com.sohayb.n13download.domain.batch

/**
 * Reads a saved URL list.
 *
 * Mirrors `batch/sources.py::load_url_list` from the Windows product:
 *
 *  - `.json`  — either `{"urls": [...]}` or a bare array; anything else in the
 *               document is ignored;
 *  - `.csv`   — every cell that is a link (the Windows reader scans every cell,
 *               so a file with a `name,url` header still works);
 *  - `.txt` / `.list` — one link per line;
 *  - anything else — every `http(s)://…` run in the file.
 *
 * Deliberately free of `org.json`: the app's own JSON needs are this narrow, and
 * keeping the parser dependency-free is what lets it be unit-tested on the JVM
 * without the Android runtime.
 */
object BatchImport {

    private val EXTENSION = Regex("\\.([A-Za-z0-9]+)$")

    private val URL_IN_TEXT = Regex("https?://[^\\s\"'<>\\\\]+", RegexOption.IGNORE_CASE)

    private val QUOTED = Regex("\"((?:[^\"\\\\]|\\\\.)*)\"")

    private val URLS_ARRAY = Regex(
        "\"urls\"\\s*:\\s*\\[(.*?)]",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )

    /** Splits a CSV/TSV line set into cells; quotes and semicolons are tolerated. */
    private val CSV_SEPARATORS = charArrayOf(',', ';', '\t', '\n', '\r', '"')

    fun parse(text: String, fileName: String): BatchParseResult {
        if (text.isBlank()) return BatchParseResult()
        return when (extensionOf(fileName)) {
            "json" -> BatchUrls.parseCandidates(jsonCandidates(text))
            "csv", "tsv" -> BatchUrls.parseCandidates(csvCandidates(text))
            "txt", "list", "text" -> BatchUrls.parse(text)
            else -> BatchUrls.parseCandidates(regexCandidates(text))
        }
    }

    private fun extensionOf(fileName: String): String =
        EXTENSION.find(fileName.trim())?.groupValues?.get(1)?.lowercase().orEmpty()

    /**
     * Candidate strings in a JSON document.
     *
     * A `"urls"` array is honoured first — that is the shape N13 itself writes.
     * Otherwise every quoted string that contains a scheme is considered, which
     * covers a bare array and a list of objects with a `url` field.
     */
    private fun jsonCandidates(text: String): List<String> {
        val arrayBody = URLS_ARRAY.find(text)?.groupValues?.get(1)
        if (arrayBody != null) {
            val quoted = QUOTED.findAll(arrayBody)
                .map { unescape(it.groupValues[1]) }
                .filter { it.isNotBlank() }
                .toList()
            if (quoted.isNotEmpty()) return quoted
        }
        val quoted = QUOTED.findAll(text)
            .map { unescape(it.groupValues[1]) }
            .filter { it.contains("://") }
            .toList()
        return quoted.ifEmpty { regexCandidates(text) }
    }

    private fun csvCandidates(text: String): List<String> =
        text.split(*CSV_SEPARATORS)
            .map { it.trim() }
            .filter { it.isNotEmpty() }

    private fun regexCandidates(text: String): List<String> =
        URL_IN_TEXT.findAll(text).map { it.value }.toList()

    private fun unescape(value: String): String =
        value.replace("\\/", "/").replace("\\u0026", "&")
}
