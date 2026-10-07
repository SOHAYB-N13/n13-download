package com.sohayb.n13download.data.storage

import java.io.File

/**
 * Keeps a download's target name inside its destination folder.
 *
 * A server-supplied `Content-Disposition` filename is untrusted input, so every
 * name is checked for separators, `..`, NUL bytes and absolute paths before it is
 * ever joined to a directory.
 */
object SafePath {

    private val INVALID = Regex("[<>:\"/\\\\|?*\\u0000-\\u001f]")

    /** Returns the name if it is safe to use, otherwise null. */
    fun validate(name: String): String? {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return null
        if (trimmed == "." || trimmed == "..") return null
        if (trimmed.contains('/') || trimmed.contains('\\')) return null
        if (trimmed.contains('\u0000')) return null
        if (trimmed.length > 200) return null
        if (File(trimmed).isAbsolute) return null
        // Windows-style drive prefix would be meaningless but is still suspicious.
        if (trimmed.length >= 2 && trimmed[1] == ':') return null
        return trimmed
    }

    fun requireSafe(name: String): String =
        validate(name) ?: throw IllegalArgumentException("Unsafe file name")

    /** Sanitises a user-entered sub-folder into a single path segment. */
    fun safeFolder(folder: String): String {
        val cleaned = INVALID.replace(folder.trim(), "").trim('.', ' ', '/', '\\')
        if (cleaned == "." || cleaned == "..") return ""
        return cleaned
    }

    /**
     * Resolves `name` inside `directory` and confirms the result really is inside
     * it — the last line of defence against traversal.
     */
    fun resolve(directory: File, name: String): File? {
        val safe = validate(name) ?: return null
        val candidate = File(directory, safe)
        return try {
            val base = directory.canonicalFile
            val target = candidate.canonicalFile
            if (target.path == base.path || target.path.startsWith(base.path + File.separator)) {
                target
            } else {
                null
            }
        } catch (_: java.io.IOException) {
            null
        }
    }
}
