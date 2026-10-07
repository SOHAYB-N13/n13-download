package com.sohayb.n13download.core

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Rolling-window speed tracker.
 *
 * Ported from `core/speed.py`.  The hot path ([add]) is called from every segment
 * after each flushed block, so it keeps an atomic byte count and only samples
 * when enough wall-clock time has passed.  A running sum avoids an O(window)
 * loop on every sample.
 */
class SpeedTracker(
    private val windowSize: Int = 20,
    private val sampleIntervalSeconds: Double = 0.2,
) {
    private val mutex = Mutex()

    private var bytesDownloaded = 0L
    private var startNanos = System.nanoTime()
    private var lastSampleNanos = startNanos
    private var lastSampleBytes = 0L
    private val samples = ArrayDeque<Double>()
    private var sampleSum = 0.0
    private var cachedAverage = 0.0

    /** Records [count] bytes and refreshes the average when the window ticks. */
    suspend fun add(count: Long) {
        mutex.withLock {
            bytesDownloaded += count
            val now = System.nanoTime()
            val elapsed = (now - lastSampleNanos) / 1_000_000_000.0
            if (elapsed >= sampleIntervalSeconds) {
                val delta = bytesDownloaded - lastSampleBytes
                val instant = if (elapsed > 0.0) delta / elapsed else 0.0
                if (samples.size == windowSize) sampleSum -= samples.removeFirst()
                samples.addLast(instant)
                sampleSum += instant
                lastSampleBytes = bytesDownloaded
                lastSampleNanos = now
                cachedAverage = if (samples.isEmpty()) 0.0 else sampleSum / samples.size
            }
        }
    }

    /** Pretends [alreadyBytes] were transferred at reset time, so resume does not over-report. */
    suspend fun seed(alreadyBytes: Long) {
        mutex.withLock {
            bytesDownloaded = alreadyBytes
            lastSampleBytes = alreadyBytes
        }
    }

    suspend fun bytesDownloaded(): Long = mutex.withLock { bytesDownloaded }

    suspend fun elapsedSeconds(): Double = mutex.withLock {
        ((System.nanoTime() - startNanos) / 1_000_000_000.0).coerceAtLeast(0.0)
    }

    /** Windowed average speed in bytes/second. */
    suspend fun averageSpeed(): Double = mutex.withLock { cachedAverage }

    /** Whole-transfer average, which is what the "Average" readout shows. */
    suspend fun overallSpeed(): Double = mutex.withLock {
        val elapsed = (System.nanoTime() - startNanos) / 1_000_000_000.0
        if (elapsed > 0.0 && bytesDownloaded > 0L) bytesDownloaded / elapsed else 0.0
    }

    suspend fun etaSeconds(totalBytes: Long): Double? {
        val speed = averageSpeed()
        val done = bytesDownloaded()
        if (totalBytes <= 0L) return null
        if (done >= totalBytes) return 0.0
        if (speed <= 1.0) return null
        return (totalBytes - done) / speed
    }
}
