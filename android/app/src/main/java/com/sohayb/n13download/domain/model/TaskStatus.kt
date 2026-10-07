package com.sohayb.n13download.domain.model

/**
 * The canonical, persisted status strings.
 *
 * Declared top-level (not inside the enum's companion) because an enum entry
 * cannot read a companion `const` in its own constructor, and because Room's
 * `@Query` annotations need real compile-time constants.  Keeping the SQL and
 * the enum on one set of names is what stops a query from silently drifting
 * away from the values the app writes — the bug class behind the History screen
 * that never showed anything.
 */
object TaskStatusValues {
    const val QUEUED = "Queued"
    const val ANALYZING = "Analyzing"
    const val STARTING = "Starting"
    const val DOWNLOADING = "Downloading"
    const val PAUSED = "Paused"
    const val MERGING = "Merging"
    const val VERIFYING = "Verifying"
    const val COMPLETE = "Complete"
    const val FAILED = "Failed"
    const val CANCELLED = "Cancelled"
    const val REMOVED = "Removed"

    /** Completed, failed and cancelled: what the user sees in History. */
    const val SQL_HISTORY = "'$COMPLETE','$FAILED','$CANCELLED'"

    /** Terminal states, including the internal REMOVED marker. */
    const val SQL_TERMINAL = "'$COMPLETE','$FAILED','$CANCELLED','$REMOVED'"

    /** Live + waiting work: everything that is not terminal. */
    const val SQL_ACTIVE = "'$QUEUED','$ANALYZING','$STARTING'," +
        "'$DOWNLOADING','$PAUSED','$MERGING','$VERIFYING'"

    /**
     * Mid-transfer work a crash interrupted.  PAUSED is deliberately absent:
     * that was a user decision and must survive a restart as PAUSED.
     */
    const val SQL_INTERRUPTED = "'$ANALYZING','$STARTING'," +
        "'$DOWNLOADING','$MERGING','$VERIFYING'"

    /** States where bytes are legitimately moving right now. */
    const val SQL_RUNNING = "'$STARTING','$DOWNLOADING','$MERGING','$VERIFYING'"

    // Single-value fragments, quoted and ready to splice into a query.
    const val SQL_QUEUED = "'$QUEUED'"
    const val SQL_STARTING = "'$STARTING'"
    const val SQL_PAUSED = "'$PAUSED'"
}

/**
 * Canonical download lifecycle states.
 *
 * The stored values are the exact strings the Windows N13 product uses
 * (`core/task.py`), so persisted state, logs and the UI all speak the same
 * language.  Core state is never represented as an arbitrary string.
 */
enum class TaskStatus(val value: String) {
    QUEUED(TaskStatusValues.QUEUED),
    ANALYZING(TaskStatusValues.ANALYZING),
    STARTING(TaskStatusValues.STARTING),
    DOWNLOADING(TaskStatusValues.DOWNLOADING),
    PAUSED(TaskStatusValues.PAUSED),
    MERGING(TaskStatusValues.MERGING),
    VERIFYING(TaskStatusValues.VERIFYING),
    COMPLETED(TaskStatusValues.COMPLETE),
    FAILED(TaskStatusValues.FAILED),
    CANCELLED(TaskStatusValues.CANCELLED),
    REMOVED(TaskStatusValues.REMOVED),
    ;

    /** Still alive: counts against the simultaneous-download limit. */
    val isActive: Boolean get() = this in ACTIVE_STATES

    /** Will never progress again on its own. */
    val isTerminal: Boolean get() = this in TERMINAL_STATES

    /** Mid-transfer work that was interrupted by process death. */
    val isInterrupted: Boolean get() = this in INTERRUPTED_STATES

    /** Bytes are moving right now. */
    val isRunning: Boolean get() = this == DOWNLOADING || this == STARTING

    fun canTransitionTo(target: TaskStatus): Boolean =
        target == this || target in TRANSITIONS[this].orEmpty()

    companion object {
        val ACTIVE_STATES = setOf(ANALYZING, STARTING, DOWNLOADING, MERGING, VERIFYING, PAUSED)
        val TERMINAL_STATES = setOf(COMPLETED, FAILED, CANCELLED, REMOVED)

        /**
         * A pause is an explicit user decision and survives a restart as PAUSED;
         * everything else mid-flight goes back to the waiting queue.
         */
        val INTERRUPTED_STATES = ACTIVE_STATES - PAUSED

        private val TRANSITIONS: Map<TaskStatus, Set<TaskStatus>> = mapOf(
            // PAUSED is reachable from QUEUED because parking a waiting task is a
            // legitimate user action (the N13 "Pause all" button does exactly that).
            QUEUED to setOf(ANALYZING, PAUSED, CANCELLED, REMOVED),
            ANALYZING to setOf(STARTING, FAILED, CANCELLED, REMOVED),
            STARTING to setOf(DOWNLOADING, FAILED, CANCELLED, REMOVED),
            DOWNLOADING to setOf(PAUSED, MERGING, FAILED, CANCELLED, REMOVED),
            // A restored pause owns no worker, so QUEUED is the only way it can run again.
            PAUSED to setOf(DOWNLOADING, QUEUED, CANCELLED, REMOVED),
            MERGING to setOf(VERIFYING, FAILED, CANCELLED, REMOVED),
            VERIFYING to setOf(COMPLETED, FAILED, CANCELLED, REMOVED),
            COMPLETED to setOf(QUEUED, REMOVED),
            FAILED to setOf(QUEUED, REMOVED),
            CANCELLED to setOf(QUEUED, REMOVED),
            REMOVED to emptySet(),
        )

        /** Coerces a stored/legacy value into a canonical status. */
        fun fromValue(value: String?): TaskStatus {
            val text = value?.trim().orEmpty()
            entries.firstOrNull { it.value.equals(text, ignoreCase = true) }?.let { return it }
            return when (text.lowercase()) {
                "stopping" -> DOWNLOADING
                "stopped" -> CANCELLED
                else -> QUEUED
            }
        }
    }
}

/** Raised when something asks a task to make an illegal state transition. */
class TransitionException(message: String) : IllegalStateException(message)
