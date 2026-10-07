package com.sohayb.n13download.core

import java.io.File
import java.util.UUID

/**
 * A contiguous byte range of the target file, written to its own `.part` file.
 *
 * Ported from `core/parts.py`.  Each part file holds the bytes starting at
 * [start], so a part's own length is its progress — no separate bookkeeping is
 * needed and a crash can never desynchronise counters from bytes on disk.
 */
data class DownloadPart(
    val index: Int,
    val start: Long,
    val end: Long,
    var file: File,
    var done: Boolean = false,
) {
    /** Total bytes this part is responsible for. */
    val size: Long get() = end - start + 1

    /** Bytes actually on disk, clamped to the part's own range. */
    val downloadedSize: Long
        get() {
            if (!file.exists()) return 0L
            return minOf(file.length(), size)
        }

    val isComplete: Boolean get() = downloadedSize >= size
}

/**
 * Segment planning, matching N13's `adaptive_thread_count` / `build_parts` /
 * `remap_parts_for_resume`.
 */
object PartsPlanner {

    /**
     * Never open more connections than the file can usefully be split into.
     * A 1 MB file gets one connection no matter what the user configured.
     */
    fun adaptiveThreadCount(totalSize: Long, requested: Int, minPartSize: Long): Int {
        if (totalSize <= 0L) return 1
        val maxBySize = maxOf(1L, totalSize / maxOf(minPartSize, 1L))
        return maxOf(1, minOf(requested.toLong(), maxBySize).toInt())
    }

    fun build(
        totalSize: Long,
        numThreads: Int,
        targetFile: File,
        minPartSize: Long,
        partSuffix: String = ".part",
    ): List<DownloadPart> {
        val effective = adaptiveThreadCount(totalSize, numThreads, minPartSize)
        if (effective <= 1) {
            return listOf(
                DownloadPart(
                    index = 0,
                    start = 0L,
                    end = totalSize - 1,
                    file = File(targetFile.parentFile, "${targetFile.name}$partSuffix" + "0"),
                ),
            )
        }

        val partSize = totalSize / effective
        return (0 until effective).map { index ->
            val start = index * partSize
            val end = if (index < effective - 1) start + partSize - 1 else totalSize - 1
            DownloadPart(
                index = index,
                start = start,
                end = end,
                file = File(targetFile.parentFile, "${targetFile.name}$partSuffix$index"),
            )
        }
    }

    /**
     * Re-segments an existing download when the connection count changed.
     *
     * A part file holds bytes from its own range start, so copying an arbitrary
     * overlap into a new part would silently corrupt the file.  Only the
     * contiguous prefix that can be proven to exist is carried over; the rest is
     * re-downloaded.  Old files are deleted because the new layout no longer
     * refers to them.
     */
    fun remapForResume(
        oldParts: List<DownloadPart>,
        totalSize: Long,
        numThreads: Int,
        targetFile: File,
        minPartSize: Long,
    ): List<DownloadPart> {
        if (oldParts.isEmpty()) return build(totalSize, numThreads, targetFile, minPartSize)

        val remapId = UUID.randomUUID().toString().replace("-", "").take(10)
        val newParts = build(
            totalSize = totalSize,
            numThreads = numThreads,
            targetFile = targetFile,
            minPartSize = minPartSize,
            partSuffix = ".repart-$remapId-",
        )
        val sources = oldParts.sortedBy { it.start }

        for (newPart in newParts) {
            var position = newPart.start
            newPart.file.parentFile?.mkdirs()
            newPart.file.outputStream().use { output ->
                while (position <= newPart.end) {
                    val source = sources.firstOrNull {
                        it.start <= position && position <= it.end && it.file.exists()
                    } ?: break

                    val sourceOffset = position - source.start
                    val available = source.downloadedSize - sourceOffset
                    if (available <= 0L) break

                    var length = minOf(available, newPart.end - position + 1)
                    source.file.inputStream().use { input ->
                        input.skip(sourceOffset)
                        val buffer = ByteArray(1024 * 1024)
                        var remaining = length
                        while (remaining > 0L) {
                            val read = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                            if (read <= 0) break
                            output.write(buffer, 0, read)
                            remaining -= read
                            position += read
                        }
                        if (remaining > 0L) length = 0L
                    }
                    if (length <= 0L) break
                }
            }
            newPart.done = newPart.isComplete
        }

        oldParts.forEach { it.file.delete() }
        return newParts
    }

    /** Removes every part file of a task (after a successful merge, or on delete). */
    fun cleanup(parts: List<DownloadPart>) {
        parts.forEach { runCatching { it.file.delete() } }
    }
}
