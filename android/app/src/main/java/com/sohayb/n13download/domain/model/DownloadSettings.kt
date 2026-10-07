package com.sohayb.n13download.domain.model

/**
 * User settings.
 *
 * Only knobs that genuinely change behaviour are exposed — there is no toggle
 * here that does not do something.  Defaults match the Windows N13 product
 * (`config/settings.py`) so the two applications behave the same out of the box.
 */
data class DownloadSettings(
    // --- Destination -------------------------------------------------------
    /**
     * Public `Downloads/N13-Download/` through MediaStore by default, so a
     * finished file lands where the user expects to find it and stays visible
     * to every other app.  `APP` and `TREE` remain available as opt-ins.
     */
    val destinationKind: DestinationKind = DestinationKind.MEDIA_STORE,
    /** SAF tree URI or MediaStore subfolder name, depending on [destinationKind]. */
    val destinationUri: String = "",
    /** Sub-folder inside the destination; empty = the destination root. */
    val destinationFolder: String = DEFAULT_FOLDER,

    // --- Connections -------------------------------------------------------
    val connectionMode: ConnectionMode = ConnectionMode.SMART,
    val numThreads: Int = 16,
    val smartMaxConnections: Int = 8,
    val smartAdaptive: Boolean = true,
    val maxConcurrent: Int = 3,

    // --- Bandwidth ---------------------------------------------------------
    /** Global cap in bytes/second; 0 = unlimited. */
    val maxSpeedBps: Long = 0L,

    // --- Behaviour ---------------------------------------------------------
    val duplicatePolicy: DuplicatePolicy = DuplicatePolicy.ASK,
    val autoCategorize: Boolean = true,
    val startImmediately: Boolean = true,
    /** Continue unfinished downloads when the app starts. */
    val resumeOnStartup: Boolean = false,

    // --- Reliability -------------------------------------------------------
    val maxRetries: Int = 15,
    val retryDelay: Double = 3.0,
    val retryBackoff: Double = 2.0,
    val retryJitter: Double = 0.25,
    val retryMaxDelay: Double = 120.0,
    val verifySsl: Boolean = true,
    val verifySize: Boolean = true,
    /** SSRF protection: reject private / loopback / link-local targets. */
    val blockPrivateUrls: Boolean = true,

    // --- Notifications -----------------------------------------------------
    val notificationsEnabled: Boolean = true,
    val notifyCompleted: Boolean = true,
    val notifyFailed: Boolean = true,
    val notifyStarted: Boolean = false,

    // --- Network -----------------------------------------------------------
    val userAgent: String = com.sohayb.n13download.core.BrowserHeaders.DEFAULT_USER_AGENT,

    // --- Appearance --------------------------------------------------------
    val themeMode: ThemeMode = ThemeMode.DARK,
    /** ARGB accent, defaulting to the N13 red. */
    val accentColor: Long = DEFAULT_ACCENT,
    /** UI language. English by default, matching the shipping behaviour. */
    val language: AppLanguage = AppLanguage.DEFAULT,

    // --- Update check ------------------------------------------------------
    /**
     * Epoch millis of the last update check that successfully reached GitHub;
     * 0 means "never checked".
     *
     * Kept here so the ~24h interval rides on the existing DataStore rather than
     * introducing a second persistence mechanism for one timestamp.
     */
    val lastUpdateCheckAt: Long = 0L,
) {
    /** The effective per-download cap, falling back to the global one. */
    fun effectiveSpeedLimit(taskLimit: Long): Long = if (taskLimit > 0L) taskLimit else maxSpeedBps

    /** The effective connection count for a task, honouring per-task overrides. */
    fun effectiveThreads(taskThreads: Int): Int =
        (if (taskThreads > 0) taskThreads else numThreads).coerceIn(1, MAX_THREADS)

    companion object {
        const val MAX_THREADS = 64
        const val MAX_CONCURRENT_LIMIT = 10
        const val MIN_CONCURRENT = 1

        /**
         * The default sub-folder inside the public Downloads collection.
         *
         * Every normal download goes to `Downloads/N13-Download/`, which is
         * created on demand, so the user never has to make it by hand and never
         * has to guess where a file went.
         */
        const val DEFAULT_FOLDER = "N13-Download"

        /** The N13 brand red, matching `--accent` in the Windows tokens. */
        const val DEFAULT_ACCENT = 0xFFEF4444L
    }
}

enum class ThemeMode(val storageValue: String) {
    DARK("dark"),
    LIGHT("light"),
    SYSTEM("system"),
    ;

    companion object {
        fun fromStorage(value: String?): ThemeMode =
            entries.firstOrNull { it.storageValue == value } ?: DARK
    }
}
