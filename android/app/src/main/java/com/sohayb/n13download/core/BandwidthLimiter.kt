package com.sohayb.n13download.core

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Global bandwidth limiter.
 *
 * Ported from `core/throttle.py`.  Token bucket with a one-second burst so TCP
 * ramp-up is not throttled into a stall, and the sleep happens outside the lock
 * so concurrent segments keep making progress.
 *
 * The cap is applied to **real bytes before they are written**, so it shapes the
 * actual transfer rate rather than the number displayed in the UI.
 */
class BandwidthLimiter(maxBytesPerSecond: Long) {

    private val mutex = Mutex()
    private var maxRate: Long = maxBytesPerSecond.coerceAtLeast(0L)
    private var burst: Double = maxRate.toDouble()
    private var tokens: Double = burst
    private var lastRefillNanos: Long = System.nanoTime()

    val enabled: Boolean get() = maxRate > 0L

    /** Current cap in bytes/second; 0 means unlimited. */
    val maxRateBytesPerSecond: Long get() = maxRate

    fun updateLimit(bytesPerSecond: Long) {
        val rate = bytesPerSecond.coerceAtLeast(0L)
        maxRate = rate
        burst = if (rate > 0L) rate.toDouble() else burst
        if (tokens > burst) tokens = burst
        lastRefillNanos = System.nanoTime()
    }

    /**
     * Suspends until [amount] bytes may be transferred under the cap.
     *
     * No-op when unlimited, so the hot download loop pays nothing in the common
     * case.  Cancellation (pause / cancel) aborts the wait immediately.
     */
    suspend fun consume(amount: Int) {
        if (maxRate <= 0L || amount <= 0) return

        var deficit = 0.0
        mutex.withLock {
            refill()
            if (tokens >= amount) {
                tokens -= amount
            } else {
                deficit = amount - tokens
                tokens = 0.0
            }
        }

        if (deficit <= 0.0) return

        var remainingMillis = deficit / maxRate.toDouble() * 1000.0
        while (remainingMillis > 0.0) {
            val slice = minOf(remainingMillis, 100.0)
            delay(slice.toLong().coerceAtLeast(1L))
            remainingMillis -= slice
            // Re-read the rate so a settings change takes effect mid-sleep.
            val current = maxRate
            if (current <= 0L) return
        }
    }

    private fun refill() {
        val now = System.nanoTime()
        val elapsedSeconds = (now - lastRefillNanos) / 1_000_000_000.0
        if (elapsedSeconds <= 0.0) return
        lastRefillNanos = now
        tokens = minOf(burst, tokens + elapsedSeconds * maxRate.toDouble())
    }
}

/**
 * Limits how many segments of one download run at the same time.
 *
 * Ported from `ConnectionGovernor` in `core/optimizer.py`.  Raising the ceiling
 * lets queued segments start; lowering it drains the free permits so no new
 * segment begins, but never interrupts one that is already transferring — the
 * reduction takes effect at the next natural segment boundary.
 */
class ConnectionGovernor(initial: Int) {

    private val mutex = Mutex()
    private val permits = Channel<Unit>(Channel.UNLIMITED)
    private var capacity: Int = initial.coerceAtLeast(1)
    private var held: Int = 0

    init {
        repeat(capacity) { permits.trySend(Unit) }
    }

    suspend fun acquire() {
        permits.receive()
        // The permit must not be lost if the segment is cancelled right here.
        withContext(NonCancellable) { mutex.withLock { held++ } }
    }

    suspend fun release() {
        mutex.withLock {
            held = (held - 1).coerceAtLeast(0)
            // Hand the permit back only while we are under the ceiling;
            // otherwise drop it, which is what lowers concurrency.
            if (held < capacity) permits.trySend(Unit)
        }
    }

    suspend fun setMaxActive(value: Int) {
        mutex.withLock {
            val target = value.coerceAtLeast(1)
            if (target == capacity) return
            if (target > capacity) {
                repeat(target - capacity) { permits.trySend(Unit) }
            } else {
                var toDrain = capacity - target
                while (toDrain > 0 && permits.tryReceive().isSuccess) toDrain--
            }
            capacity = target
        }
    }

    suspend fun maxActive(): Int = mutex.withLock { capacity }
}
