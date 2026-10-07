package com.sohayb.n13download.data.engine

import com.sohayb.n13download.core.DownloadPart
import com.sohayb.n13download.core.PartsPlanner
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Persisted segment layout for a multi-connection download.
 *
 * The `.partN` files already carry their own progress (a part's length *is* its
 * progress), so only the geometry has to be remembered: which byte range each
 * file covers.  Storing it means a resume after a crash or an app restart lands
 * on exactly the same layout instead of re-splitting the file.
 */
data class PartLayout(
    val url: String,
    val totalSize: Long,
    val threads: Int,
    val parts: List<PartRange>,
) {
    data class PartRange(val index: Int, val start: Long, val end: Long)

    fun toJson(): String = JSONObject().apply {
        put("url", url)
        put("totalSize", totalSize)
        put("threads", threads)
        put(
            "parts",
            JSONArray().apply {
                parts.forEach { part ->
                    put(
                        JSONObject().apply {
                            put("index", part.index)
                            put("start", part.start)
                            put("end", part.end)
                        },
                    )
                }
            },
        )
    }.toString()

    companion object {
        fun fromJson(raw: String): PartLayout? = runCatching {
            val root = JSONObject(raw)
            val array = root.getJSONArray("parts")
            val ranges = ArrayList<PartRange>(array.length())
            for (index in 0 until array.length()) {
                val part = array.getJSONObject(index)
                ranges.add(
                    PartRange(
                        index = part.getInt("index"),
                        start = part.getLong("start"),
                        end = part.getLong("end"),
                    ),
                )
            }
            PartLayout(
                url = root.getString("url"),
                totalSize = root.getLong("totalSize"),
                threads = root.getInt("threads"),
                parts = ranges,
            )
        }.getOrNull()
    }
}

/** Reads and writes [PartLayout] next to the part files. */
class PartStateStore(private val directory: File) {

    private val stateFile: File get() = File(directory, STATE_FILE)

    fun load(): PartLayout? {
        val file = stateFile
        if (!file.exists()) return null
        return runCatching { PartLayout.fromJson(file.readText()) }.getOrNull()
    }

    fun save(layout: PartLayout) {
        runCatching {
            directory.mkdirs()
            stateFile.writeText(layout.toJson())
        }
    }

    fun delete() {
        runCatching { stateFile.delete() }
    }

    /** Materialises a saved layout into [DownloadPart]s pointing at the part files. */
    fun toParts(layout: PartLayout, targetFile: File): List<DownloadPart> =
        layout.parts.map { range ->
            DownloadPart(
                index = range.index,
                start = range.start,
                end = range.end,
                file = File(directory, "${targetFile.name}.part${range.index}"),
            )
        }

    fun fromParts(url: String, totalSize: Long, parts: List<DownloadPart>): PartLayout =
        PartLayout(
            url = url,
            totalSize = totalSize,
            threads = parts.size,
            parts = parts.map { PartLayout.PartRange(it.index, it.start, it.end) },
        )

    /** Deletes part files and the state file once a download is fully published. */
    fun cleanup(parts: List<DownloadPart>) {
        PartsPlanner.cleanup(parts)
        delete()
    }

    private companion object {
        const val STATE_FILE = "layout.json"
    }
}
