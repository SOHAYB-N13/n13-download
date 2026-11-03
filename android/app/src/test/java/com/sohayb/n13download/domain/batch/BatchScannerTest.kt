package com.sohayb.n13download.domain.batch

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/**
 * The pattern scan.
 *
 * The stop rules are a direct port of `batch/pattern.py::scan_pattern_urls`, and
 * they are the reason a mistyped pattern does not turn into 200 requests to a
 * dead host.  Each test here pins one of them, plus the one behaviour that is
 * easy to get wrong: a timeout is *inconclusive* — it neither counts as a miss
 * nor resets the counter.
 */
class BatchScannerTest {

    private val spec = BatchPatternSpec(
        pattern = "https://example.com/file*.zip",
        mode = BatchPatternMode.AUTO,
        start = 1,
        step = 1,
        padding = 2,
    )

    @Test
    fun `a scan keeps every reachable url in sequence order`() = runBlocking {
        val reachable = setOf(1, 2, 3, 5)
        val scanner = BatchScanner(probe = { url -> outcome(url, reachable) })

        val result = scanner.scanAuto(spec)

        assertEquals(
            listOf(1, 2, 3, 5).map { "https://example.com/file0$it.zip" },
            result.urls,
        )
        assertEquals(BatchScanStop.CONSECUTIVE_MISSES, result.stop)
    }

    @Test
    fun `five consecutive misses stop the scan`() = runBlocking {
        val calls = AtomicInteger(0)
        // Nothing exists: every probe is a 404.
        val scanner = BatchScanner(
            probe = {
                calls.incrementAndGet()
                BatchProbeOutcome(reachable = false, statusCode = 404)
            },
        )

        val result = scanner.scanAuto(spec)

        assertEquals(BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES, calls.get())
        assertEquals(BatchScanStop.CONSECUTIVE_MISSES, result.stop)
        assertTrue(result.urls.isEmpty())
    }

    @Test
    fun `a success resets the miss counter`() = runBlocking {
        // Four misses, one hit, then misses again: the scan must survive the
        // second run of misses because the hit reset the counter.
        val hits = setOf(5, 10)
        val calls = AtomicInteger(0)
        val scanner = BatchScanner(
            probe = { url ->
                calls.incrementAndGet()
                outcome(url, hits)
            },
        )

        val result = scanner.scanAuto(spec)

        assertEquals(2, result.urls.size)
        assertEquals(BatchScanStop.CONSECUTIVE_MISSES, result.stop)
        assertTrue("the scan must not stop after the first run of misses", calls.get() > 9)
    }

    @Test
    fun `timeouts neither count as misses nor reset the counter`() = runBlocking {
        // Two 404s, then a timeout, then three more 404s: the counter keeps
        // climbing across the timeout, so the scan stops at the fifth miss.
        val plan = listOf(404, 404, 0, 404, 404, 404)
        val calls = AtomicInteger(0)
        val scanner = BatchScanner(
            probe = {
                val index = calls.getAndIncrement()
                val code = plan.getOrElse(index) { 404 }
                if (code == 0) {
                    BatchProbeOutcome(reachable = false, error = "Connection timed out")
                } else {
                    BatchProbeOutcome(reachable = false, statusCode = code)
                }
            },
        )

        val result = scanner.scanAuto(spec)

        assertEquals(BatchScanStop.CONSECUTIVE_MISSES, result.stop)
        assertEquals("the timeout must not have reset the counter", 6, calls.get())
    }

    @Test
    fun `ten server errors stop the scan`() = runBlocking {
        val calls = AtomicInteger(0)
        val scanner = BatchScanner(
            probe = {
                calls.incrementAndGet()
                BatchProbeOutcome(reachable = false, statusCode = 503)
            },
        )

        val result = scanner.scanAuto(spec)

        assertEquals(BatchPattern.AUTO_MAX_SERVER_ERRORS, calls.get())
        assertEquals(BatchScanStop.SERVER_ERRORS, result.stop)
    }

    @Test
    fun `a success resets the server error counter`() = runBlocking {
        // Non-hits answer 503, so the miss counter never advances and only the
        // server-error counter can end the scan.
        val hits = setOf(6, 12)
        val scanner = BatchScanner(
            probe = { url ->
                if (numberOf(url) in hits) {
                    BatchProbeOutcome(reachable = true, statusCode = 200)
                } else {
                    BatchProbeOutcome(reachable = false, statusCode = 503)
                }
            },
        )

        val result = scanner.scanAuto(spec)

        // Without the reset, the tenth 5xx in a row would have ended the scan
        // before the second hit.
        assertEquals(2, result.urls.size)
        assertEquals(BatchScanStop.SERVER_ERRORS, result.stop)
    }

    @Test
    fun `the attempt ceiling bounds an endless sequence`() = runBlocking {
        val calls = AtomicInteger(0)
        // Every probe times out: nothing ends the sequence on its own terms.
        val scanner = BatchScanner(
            probe = {
                calls.incrementAndGet()
                BatchProbeOutcome(reachable = false, error = "timed out")
            },
        )

        val result = scanner.scanAuto(spec)

        assertEquals(BatchPattern.AUTO_MAX_ATTEMPTS, calls.get())
        assertEquals(BatchScanStop.ATTEMPT_LIMIT, result.stop)
    }

    @Test
    fun `progress is reported for every attempt`() = runBlocking {
        val seen = mutableListOf<BatchScanProgress>()
        val scanner = BatchScanner(
            probe = { url -> outcome(url, setOf(1, 2)) },
            onProgress = { seen += it },
        )

        scanner.scanAuto(spec)

        assertTrue(seen.isNotEmpty())
        assertEquals(1, seen.first().attempted)
        assertEquals("https://example.com/file01.zip", seen.first().url)
        assertTrue(seen.first().reachable)
    }

    @Test
    fun `cancellation stops the scan immediately`() = runBlocking {
        val calls = AtomicInteger(0)
        val gate = CompletableDeferred<Unit>()
        val scanner = BatchScanner(
            probe = {
                calls.incrementAndGet()
                gate.await()
                BatchProbeOutcome(reachable = true)
            },
        )

        val job = async(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) {
            scanner.scanAuto(spec)
        }
        // Let the first probe block, then cancel.
        delay(50)
        job.cancel()
        gate.complete(Unit)
        runCatching { job.await() }

        assertTrue(job.isCancelled)
        assertEquals("only the in-flight probe may have run", 1, calls.get())
    }

    @Test
    fun `range mode probes every generated url and keeps the reachable ones`() = runBlocking {
        val reachable = setOf(2, 4)
        val scanner = BatchScanner(probe = { url -> outcome(url, reachable) })

        val result = scanner.scanRange(
            BatchPatternSpec(
                pattern = "https://example.com/file*.zip",
                mode = BatchPatternMode.RANGE,
                start = 1,
                end = 4,
                padding = 2,
            ),
        )

        assertEquals(
            listOf("https://example.com/file02.zip", "https://example.com/file04.zip"),
            result.urls,
        )
        assertEquals(4, result.attempted)
        assertEquals(BatchScanStop.FINISHED, result.stop)
    }

    @Test
    fun `range mode preserves input order despite concurrent probing`() = runBlocking {
        // Completion order is deliberately reversed: the result must still be
        // the generated order, because that is what the preview showed.
        val scanner = BatchScanner(
            probe = { url ->
                val number = numberOf(url)
                delay((10 - number).toLong())
                BatchProbeOutcome(reachable = true)
            },
        )

        val result = scanner.scanRange(
            BatchPatternSpec(
                pattern = "https://example.com/file*.zip",
                mode = BatchPatternMode.RANGE,
                start = 1,
                end = 8,
                padding = 2,
            ),
            concurrency = 8,
        )

        assertEquals(
            (1..8).map { "https://example.com/file%02d.zip".format(it) },
            result.urls,
        )
    }

    @Test
    fun `range mode of an empty range finishes with nothing`() = runBlocking {
        val scanner = BatchScanner(probe = { BatchProbeOutcome(reachable = true) })

        val result = scanner.scanRange(
            BatchPatternSpec(
                pattern = "https://example.com/file*.zip",
                mode = BatchPatternMode.RANGE,
                start = 5,
                end = 1,
            ),
        )

        assertTrue(result.urls.isEmpty())
        assertEquals(0, result.attempted)
    }

    @Test
    fun `miss classification follows the windows buckets`() = runBlocking {
        val kinds = mutableListOf<BatchMissKind?>()
        val plan = listOf(404, 410, 500, 503, 408, 429, 403, 0)
        val calls = AtomicInteger(0)
        val scanner = BatchScanner(
            probe = {
                val code = plan[calls.getAndIncrement() % plan.size]
                if (code == 0) {
                    BatchProbeOutcome(reachable = false, error = "not found")
                } else {
                    BatchProbeOutcome(reachable = false, statusCode = code)
                }
            },
            onProgress = { kinds += it.missKind },
        )

        // Stops after five consecutive misses, which is all we need to classify.
        scanner.scanAuto(spec)

        assertEquals(
            listOf(
                BatchMissKind.NOT_FOUND,
                BatchMissKind.NOT_FOUND,
                BatchMissKind.SERVER,
                BatchMissKind.SERVER,
                BatchMissKind.TIMEOUT,
            ),
            kinds.filterNotNull().take(5),
        )
    }

    @Test
    fun `a scan that finds nothing still returns a stop reason`() = runBlocking {
        val scanner = BatchScanner(probe = { BatchProbeOutcome(reachable = false, statusCode = 404) })

        val result = withTimeout(5_000) { scanner.scanAuto(spec) }

        assertTrue(result.urls.isEmpty())
        assertEquals(BatchPattern.AUTO_MAX_CONSECUTIVE_MISSES, result.attempted)
        assertEquals(BatchScanStop.CONSECUTIVE_MISSES, result.stop)
    }

    private fun outcome(url: String, reachable: Set<Int>): BatchProbeOutcome =
        if (numberOf(url) in reachable) {
            BatchProbeOutcome(reachable = true, statusCode = 200, size = 1024)
        } else {
            BatchProbeOutcome(reachable = false, statusCode = 404)
        }

    private fun numberOf(url: String): Int = url.substringAfter("file").substringBefore('.').toInt()
}
