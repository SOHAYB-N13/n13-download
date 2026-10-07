package com.sohayb.n13download.domain.model

/**
 * Canonical download lifecycle states.
 *
 * The stored values are the exact strings the Windows N13 product uses
 * (`core/task.py`), so persisted state, logs and the UI all speak the same
 * language.  Core state is never represented as an arbitrary string.
 */
enum class TaskStatus(val value: String) {
    QUEUED("Queued"),
    ANALYZING("Analyzing"),
    STARTING("Starting"),
    DOWNLOADING("Downloading"),
    PAUSED("Paused"),
    MERGING("Merging"),
    VERIFYING("Verifying"),
    COMPLETED("Complete"),
    FAILED("Failed"),
    CANCELLED("Cancelled"),
    REMOVED("Removed"),
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
