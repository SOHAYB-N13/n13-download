package com.sohayb.n13download.domain.model

/**
 * Queue priority.
 *
 * N13 stores an integer where **0 is the highest priority and 10 the lowest**,
 * defaulting to 5.  The UI only ever shows three named levels plus an exact
 * value, matching the Windows "Priority" dialog.
 */
@JvmInline
value class DownloadPriority(val value: Int) {

    init {
        require(value in MIN..MAX) { "priority must be between $MIN and $MAX" }
    }

    /** "High" when <= 3, "Low" when >= 8, otherwise "Normal". */
    val label: String
        get() = when {
            value <= HIGH_THRESHOLD -> HIGH
            value >= LOW_THRESHOLD -> LOW
            else -> NORMAL
        }

    companion object {
        const val MIN = 0
        const val MAX = 10
        const val DEFAULT_VALUE = 5

        const val HIGH = "High"
        const val NORMAL = "Normal"
        const val LOW = "Low"

        const val HIGH_PRESET = 1
        const val NORMAL_PRESET = 5
        const val LOW_PRESET = 9

        private const val HIGH_THRESHOLD = 3
        private const val LOW_THRESHOLD = 8

        val DEFAULT = DownloadPriority(DEFAULT_VALUE)

        fun of(value: Int): DownloadPriority = DownloadPriority(value.coerceIn(MIN, MAX))
    }
}
