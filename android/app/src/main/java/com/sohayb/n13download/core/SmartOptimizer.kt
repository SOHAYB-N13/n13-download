package com.sohayb.n13download.core

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Smart Download Optimizer.
 *
 * Ported from `core/optimizer.py`.  Picks how many connections a file deserves
 * from its size and Range support, then adapts that parallelism safely while the
 * transfer runs.
 *
 * Safety rules preserved from the Windows implementation:
 *  - Segments are planned once; only *concurrency* is adjusted.
 *  - A server-imposed error halves parallelism and starts a cooldown.
 *  - An artificial speed cap is never mistaken for a slow server, so Smart mode
 *    does not pile on connections chasing a bandwidth limit.
 */
class SmartOptimizer(
    private val maxConnections: Int = 8,
    private val adaptive: Boolean = true,
    private val onStatus: ((String) -> Unit)? = null,
) {
    private val lock = Mutex()

    private var governor: ConnectionGovernor? = null
    private var segmentMax: Int = 1
    private var startedAtMillis: Long = 0L
    private var lastChangeMillis: Long = 0L
    private var lastErrorMillis: Long = Long.MIN_VALUE
    private var lastObservationMillis: Long = 0L
    private var lastObservedBytes: Long = 0L
    private var previousInstantSpeed: Double = 0.0
    private var baselineEstablished = false
    private var speedLimited = false

    /** Recommended segment count — the parallelism ceiling for this file. */
    fun segmentCount(totalSize: Long, supportsRange: Boolean): Int {
        if (!supportsRange || totalSize <= 0L) return 1
        val base = when {
            totalSize < MB -> 1
            totalSize < 16L * MB -> 2
            totalSize < 128L * MB -> 4
            totalSize < GB -> 6
            else -> maxConnections
        }
        return base.coerceIn(1, maxConnections.coerceAtLeast(1))
    }

    /** @return the number of segments to plan, and the governor (null = all at once). */
    suspend fun start(totalSize: Long, supportsRange: Boolean): Pair<Int, ConnectionGovernor?> {
        val segments = segmentCount(totalSize, supportsRange)
        segmentMax = segments
        val initial = if (adaptive) segments.coerceAtMost(4) else segments
        val now = System.currentTimeMillis()
        startedAtMillis = now
        lastChangeMillis = 0L
        lastErrorMillis = Long.MIN_VALUE
        lastObservationMillis = 0L
        lastObservedBytes = 0L
        previousInstantSpeed = 0.0
        baselineEstablished = false
        speedLimited = false

        val created = if (adaptive && segments > 1) ConnectionGovernor(initial) else null
        governor = created
        emitStatus(initial.toString())
        return segments to created
    }

    fun setSpeedLimited(limited: Boolean) {
        speedLimited = limited
    }

    /** Called after a server-imposed failure: reduce parallelism and cool down. */
    suspend fun onServerError() {
        val now = System.currentTimeMillis()
        lock.withLock {
            lastErrorMillis = now
            val current = governor ?: return
            val active = current.maxActive()
            val reduced = (active / 2).coerceAtLeast(1)
            if (reduced < active) {
                current.setMaxActive(reduced)
                lastChangeMillis = now
                emitStatus("$active->$reduced")
            }
        }
    }

    /** Adaptive hook, driven from the throttled progress path (~1 Hz). */
    suspend fun observe(completed: Long, total: Long) {
        if (governor == null) return
        val now = System.currentTimeMillis()
        lock.withLock {
            val current = governor ?: return
            if (now - lastErrorMillis < ERROR_COOLDOWN_MILLIS) return
            if (now - startedAtMillis < WARMUP_MILLIS) return
            if (completed <= 0L || total <= 0L) return
            if (speedLimited) return

            val remaining = total - completed
            if (remaining <= total / segmentMax.coerceAtLeast(1)) return // near the end: be conservative

            val deltaMillis = now - lastObservationMillis
            if (deltaMillis < 1000L) return
            val deltaBytes = completed - lastObservedBytes
            lastObservationMillis = now
            lastObservedBytes = completed
            val instant = deltaBytes * 1000.0 / deltaMillis.toDouble()

            if (!baselineEstablished) {
                baselineEstablished = true
                previousInstantSpeed = instant
                return
            }

            val active = current.maxActive()
            if (active >= segmentMax) return
            if (now - lastChangeMillis < COOLDOWN_MILLIS) return

            if (instant >= previousInstantSpeed * IMPROVEMENT) {
                val increased = minOf(segmentMax, active + 2)
                current.setMaxActive(increased)
                lastChangeMillis = now
                previousInstantSpeed = instant
                emitStatus("$active->$increased")
            } else {
                previousInstantSpeed = instant
            }
        }
    }

    private fun emitStatus(text: String) {
        runCatching { onStatus?.invoke(text) }
    }

    private companion object {
        const val MB = 1024L * 1024L
        const val GB = 1024L * 1024L * 1024L
        const val COOLDOWN_MILLIS = 10_000L
        const val ERROR_COOLDOWN_MILLIS = 30_000L
        const val WARMUP_MILLIS = 3_000L
        const val IMPROVEMENT = 1.2
    }
}
