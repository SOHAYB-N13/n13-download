package com.sohayb.n13download.core

import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.Locale

/**
 * Filename and URL resolution.
 *
 * Ported from `core/utils.py` (`sanitize_filename`, `_looks_like_filename`,
 * `get_filename_from_response`, `normalize_url`, `MIME_TO_EXT`).  The ordering
 * of the signals is the important part and is preserved exactly:
 *
 * 1. `Content-Disposition` (RFC 5987 `filename*` first, then plain `filename`)
 * 2. last path segment of the final URL, then of the original URL
 * 3. a path segment with no extension + the Content-Type extension
 * 4. `download` + the Content-Type extension
 */
object FilenameResolver {

    private val INVALID_CHARS = Regex("[<>:\"/\\\\|?*\\u0000-\\u001f]")
    private val EXTENSION_PATTERN = Regex("[A-Za-z0-9]+")

    const val FALLBACK = "downloaded_file"

    val MIME_TO_EXT: Map<String, String> = mapOf(
        "application/zip" to ".zip",
        "application/x-zip-compressed" to ".zip",
        "application/x-rar-compressed" to ".rar",
        "application/vnd.rar" to ".rar",
        "application/x-7z-compressed" to ".7z",
        "application/x-tar" to ".tar",
        "application/gzip" to ".gz",
        "application/x-gzip" to ".gz",
        "application/x-bzip2" to ".bz2",
        "application/x-xz" to ".xz",
        "application/pdf" to ".pdf",
        "application/msword" to ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" to ".docx",
        "application/vnd.ms-excel" to ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" to ".xlsx",
        "application/vnd.ms-powerpoint" to ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" to ".pptx",
        "application/octet-stream" to "",
        "application/x-msdownload" to ".exe",
        "application/x-msi" to ".msi",
        "application/vnd.android.package-archive" to ".apk",
        "application/x-iso9660-image" to ".iso",
        "application/java-archive" to ".jar",
        "font/ttf" to ".ttf",
        "application/x-tfont" to ".ttf",
        "font/otf" to ".otf",
        "application/x-rpm" to ".rpm",
        "application/x-debian-package" to ".deb",
        "text/plain" to ".txt",
        "text/html" to ".html",
        "text/csv" to ".csv",
        "text/xml" to ".xml",
        "application/json" to ".json",
        "application/xml" to ".xml",
        "image/jpeg" to ".jpg",
        "image/png" to ".png",
        "image/gif" to ".gif",
        "image/webp" to ".webp",
        "image/svg+xml" to ".svg",
        "image/bmp" to ".bmp",
        "image/x-icon" to ".ico",
        "audio/mpeg" to ".mp3",
        "audio/mp4" to ".m4a",
        "audio/x-wav" to ".wav",
        "audio/ogg" to ".ogg",
        "audio/flac" to ".flac",
        "audio/aac" to ".aac",
        "video/mp4" to ".mp4",
        "video/x-msvideo" to ".avi",
        "video/x-matroska" to ".mkv",
        "video/quicktime" to ".mov",
        "video/webm" to ".webm",
        "video/x-flv" to ".flv",
        "video/mpeg" to ".mpeg",
        "video/3gpp" to ".3gp",
    )

    /** Strips characters that are illegal on the filesystem or enable traversal. */
    fun sanitize(name: String): String {
        val cleaned = INVALID_CHARS.replace(name, "_").trim('.', ' ', '\t')
        // A name made only of dots is a traversal attempt, never a filename.
        if (cleaned.isEmpty() || cleaned.all { it == '.' }) return FALLBACK
        return cleaned
    }

    /** Rejects "." / ".." and any name that escapes the destination folder. */
    fun isSafeName(name: String): Boolean {
        if (name.isBlank()) return false
        if (name == "." || name == "..") return false
        if (name.contains('/') || name.contains('\\')) return false
        if (name.contains('\u0000')) return false
        return true
    }

    private fun looksLikeFilename(name: String): Boolean {
        val trimmed = name.trim()
        if (trimmed.isEmpty() || trimmed == "." || trimmed == "..") return false
        val dot = trimmed.lastIndexOf('.')
        if (dot <= 0 || dot == trimmed.lastIndex) return false
        val stem = trimmed.substring(0, dot)
        val ext = trimmed.substring(dot + 1)
        return stem.isNotEmpty() && EXTENSION_PATTERN.matches(ext)
    }

    fun extensionForContentType(contentType: String?): String {
        val type = contentType?.substringBefore(';')?.trim()?.lowercase(Locale.US).orEmpty()
        return MIME_TO_EXT[type].orEmpty()
    }

    /**
     * Best filename for a response.
     *
     * @param headers response headers, keys compared case-insensitively.
     */
    fun fromResponse(
        headers: Map<String, String>,
        url: String,
        finalUrl: String? = null,
    ): String {
        val resolved = finalUrl?.takeIf { it.isNotBlank() } ?: url
        val disposition = header(headers, "Content-Disposition")

        if (disposition.isNotEmpty()) {
            val extended = Regex("filename\\*\\s*=\\s*UTF-8''([^\\s;]+)", RegexOption.IGNORE_CASE)
                .find(disposition)?.groupValues?.get(1)
            if (extended != null) {
                val name = sanitize(decode(extended))
                if (looksLikeFilename(name)) return name
            }
            val plain = Regex("filename\\s*=\\s*\"?([^\";\r\n]+)\"?", RegexOption.IGNORE_CASE)
                .find(disposition)?.groupValues?.get(1)
            if (plain != null) {
                val name = sanitize(plain.trim())
                if (looksLikeFilename(name)) return name
            }
        }

        val extensionHint = extensionForContentType(header(headers, "Content-Type"))

        for (candidate in listOf(resolved, url)) {
            val raw = pathSegment(candidate)
            if (raw.isNotEmpty()) {
                val name = sanitize(decode(raw))
                if (looksLikeFilename(name)) return name
            }
        }

        for (candidate in listOf(resolved, url)) {
            val raw = pathSegment(candidate)
            if (raw.isNotEmpty()) {
                val name = sanitize(decode(raw))
                if (name.isNotEmpty() && name != FALLBACK && extensionHint.isNotEmpty()) {
                    return name + extensionHint
                }
            }
        }

        if (extensionHint.isNotEmpty()) return "download$extensionHint"
        return FALLBACK
    }

    private fun header(headers: Map<String, String>, name: String): String =
        headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value.orEmpty()

    private fun pathSegment(url: String): String = try {
        URI(url).path.orEmpty().trimEnd('/').substringAfterLast('/')
    } catch (_: Exception) {
        ""
    }

    private fun decode(value: String): String {
        if (value.isEmpty() || '%' !in value) return value
        return try {
            // '+' is a legal path character, so protect it from form-decoding.
            URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8)
        } catch (_: IllegalArgumentException) {
            value
        }
    }

    /**
     * Trims surrounding whitespace/quotes from a pasted link and prefixes
     * `https://` when the user pasted a bare domain.
     */
    fun normalizeUrl(url: String): String {
        val cleaned = url.trim().trim('"', '\'', '`').trim()
        if (cleaned.isEmpty()) return ""
        val lower = cleaned.lowercase(Locale.US)
        if (lower.startsWith("http://") || lower.startsWith("https://") || lower.startsWith("ftp://")) {
            return cleaned
        }
        if (cleaned.startsWith("//")) return "https:$cleaned"
        if (Regex("^[a-z0-9.\\-]+\\.[a-z]{2,}(/|$)").containsMatchIn(lower) || lower.startsWith("www.")) {
            return "https://$cleaned"
        }
        return cleaned
    }

    fun isValidHttpUrl(url: String): Boolean {
        val uri = try {
            URI(url)
        } catch (_: Exception) {
            return false
        }
        return (uri.scheme == "http" || uri.scheme == "https") && !uri.host.isNullOrBlank()
    }

    fun hostOf(url: String): String = try {
        URI(url).host.orEmpty().removePrefix("www.")
    } catch (_: Exception) {
        ""
    }

    fun originOf(url: String): String? = try {
        val uri = URI(url)
        val scheme = uri.scheme
        val host = uri.host
        if (scheme != null && !host.isNullOrBlank()) {
            val port = if (uri.port > 0) ":${uri.port}" else ""
            "$scheme://$host$port/"
        } else {
            null
        }
    } catch (_: Exception) {
        null
    }
}
