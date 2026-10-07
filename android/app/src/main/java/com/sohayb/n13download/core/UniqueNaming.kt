package com.sohayb.n13download.core

/** Duplicate-name handling, shared by the manager and the queue. */
object UniqueNaming {

    /**
     * `report.pdf` -> `report (1).pdf` when `report.pdf` is taken.
     * Never returns a name that already exists in [existing].
     */
    fun unique(existing: Set<String>, filename: String): String {
        if (filename !in existing) return filename

        val hasExtension = filename.contains('.') && !filename.endsWith('.')
        val stem = if (hasExtension) filename.substringBeforeLast('.') else filename
        val suffix = if (hasExtension) filename.substringAfterLast('.') else ""

        var counter = 1
        while (counter <= MAX_ATTEMPTS) {
            val candidate = if (suffix.isEmpty()) "$stem ($counter)" else "$stem ($counter).$suffix"
            if (candidate !in existing) return candidate
            counter++
        }
        val fallback = if (suffix.isEmpty()) {
            "$stem (${System.currentTimeMillis()})"
        } else {
            "$stem (${System.currentTimeMillis()}).$suffix"
        }
        return fallback
    }

    private const val MAX_ATTEMPTS = 10_000
}
