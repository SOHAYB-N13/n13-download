package com.sohayb.n13download.core

/**
 * Download category detection.
 *
 * Ported from `core/analyzer.py` (`DEFAULT_CATEGORY_EXTENSIONS`,
 * `detect_category`).  The category names are the exact N13 strings and are
 * user-visible, so they must not be translated or reordered.
 */
object CategoryDetector {

    /** Display order used by the N13 category strip. */
    val ORDER = listOf(
        "General",
        "Archives",
        "Videos",
        "Music",
        "Documents",
        "Programs",
        "Images",
        "Other",
    )

    /** The order here decides precedence: the first match wins. */
    val DEFAULT_EXTENSIONS: Map<String, List<String>> = linkedMapOf(
        "Videos" to listOf(
            "mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts",
            "mpeg", "mpg", "3gp", "wmv", "m2ts",
        ),
        "Music" to listOf(
            "mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma", "mid",
        ),
        "Images" to listOf(
            "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico",
            "tiff", "heic", "avif", "jfif",
        ),
        "Documents" to listOf(
            "pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx",
            "ppt", "pptx", "csv", "md", "epub", "odp", "ods",
        ),
        "Archives" to listOf(
            "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso",
            "tgz", "tbz2", "cab", "zst",
        ),
        "Programs" to listOf(
            "exe", "msi", "apk", "dmg", "deb", "rpm", "jar", "pkg",
            "appimage", "bat", "sh",
        ),
        "Other" to listOf(
            "bin", "dat", "db", "dll", "so", "dylib", "img", "part", "torrent",
        ),
    )

    /**
     * Maps a filename (falling back to the content type) to a category.
     *
     * @param customExtensions extra per-category extensions from settings.
     */
    fun detect(
        filename: String,
        contentType: String = "",
        customExtensions: Map<String, List<String>> = emptyMap(),
    ): String {
        val ext = filename.substringAfterLast('.', "").lowercase()
            .takeIf { filename.contains('.') }
            .orEmpty()

        val merged = LinkedHashMap<String, MutableList<String>>()
        DEFAULT_EXTENSIONS.forEach { (category, extensions) ->
            merged[category] = extensions.toMutableList()
        }
        customExtensions.forEach { (category, extensions) ->
            val target = merged.getOrPut(category) { mutableListOf() }
            extensions.forEach { raw ->
                val clean = raw.lowercase().removePrefix(".")
                if (clean.isNotEmpty() && clean !in target) target.add(clean)
            }
        }

        if (ext.isNotEmpty()) {
            merged.forEach { (category, extensions) ->
                if (ext in extensions) return category
            }
        }

        val type = contentType.substringBefore(';').trim().lowercase()
        return when {
            type.startsWith("video/") -> "Videos"
            type.startsWith("audio/") -> "Music"
            type.startsWith("image/") -> "Images"
            type in setOf(
                "application/pdf", "text/plain", "text/csv", "text/html", "text/xml",
            ) -> "Documents"
            type in setOf(
                "application/zip", "application/x-7z-compressed", "application/x-rar-compressed",
                "application/x-tar", "application/gzip", "application/x-bzip2", "application/x-xz",
            ) -> "Archives"
            type in setOf(
                "application/x-msdownload", "application/x-msi",
                "application/vnd.android.package-archive",
            ) -> "Programs"
            type.isNotEmpty() -> "Other"
            else -> "General"
        }
    }
}
