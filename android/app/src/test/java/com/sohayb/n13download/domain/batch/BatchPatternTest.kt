package com.sohayb.n13download.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pattern expansion.
 *
 * Every expectation here is a direct consequence of the Windows reference
 * (`batch/pattern.py`):
 *
 * ```python
 * num_str = f"{num:0{padding}d}" if padding > 0 else str(num)
 * test_url = pattern.replace("*", num_str)
 * ```
 *
 * so the numbering, the zero padding and the "replace every placeholder"
 * behaviour are the parts that must not drift.
 */
class BatchPatternTest {

    // ---- Numbering -------------------------------------------------------

    @Test
    fun `padding zero means no leading zeros`() {
        assertEquals("7", BatchPattern.numberText(7, padding = 0))
        assertEquals("123", BatchPattern.numberText(123, padding = 0))
    }

    @Test
    fun `padding pads with leading zeros`() {
        assertEquals("01", BatchPattern.numberText(1, padding = 2))
        assertEquals("007", BatchPattern.numberText(7, padding = 3))
        assertEquals("000001", BatchPattern.numberText(1, padding = 6))
    }

    @Test
    fun `a number wider than the padding is not truncated`() {
        // Python's format spec is a *minimum* width, and truncating here would
        // silently request the wrong file.
        assertEquals("1234", BatchPattern.numberText(1234, padding = 2))
    }

    // ---- Substitution ----------------------------------------------------

    @Test
    fun `the placeholder is replaced once`() {
        assertEquals(
            "https://example.com/file03.zip",
            BatchPattern.urlFor("https://example.com/file*.zip", 3, padding = 2),
        )
    }

    @Test
    fun `every placeholder gets the same number`() {
        // `str.replace` substitutes all occurrences; the Windows GUI path passes
        // the pattern straight to it, so this is the real behaviour.
        assertEquals(
            "https://example.com/05/05.zip",
            BatchPattern.urlFor("https://example.com/*/*.zip", 5, padding = 2),
        )
    }

    @Test
    fun `surrounding whitespace in the pattern is ignored`() {
        assertEquals(
            "https://example.com/a1.zip",
            BatchPattern.urlFor("  https://example.com/a*.zip  ", 1, padding = 0),
        )
    }

    // ---- Validation ------------------------------------------------------

    @Test
    fun `an empty pattern is rejected`() {
        val check = BatchPattern.check(BatchPatternSpec(pattern = "   "))

        assertTrue(check is BatchPatternCheck.Invalid)
        assertEquals(BatchPatternProblem.EMPTY, (check as BatchPatternCheck.Invalid).problem)
    }

    @Test
    fun `a pattern without a placeholder is rejected`() {
        val check = BatchPattern.check(BatchPatternSpec(pattern = "https://example.com/a.zip"))

        assertEquals(
            BatchPatternProblem.NO_PLACEHOLDER,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `padding outside the windows range is rejected`() {
        val tooMuch = BatchPattern.check(BatchPatternSpec(pattern = "https://e.com/*.zip", padding = 13))
        val negative = BatchPattern.check(BatchPatternSpec(pattern = "https://e.com/*.zip", padding = -1))

        assertEquals(
            BatchPatternProblem.PADDING_OUT_OF_RANGE,
            (tooMuch as BatchPatternCheck.Invalid).problem,
        )
        assertEquals(
            BatchPatternProblem.PADDING_OUT_OF_RANGE,
            (negative as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `a negative start is rejected`() {
        val check = BatchPattern.check(BatchPatternSpec(pattern = "https://e.com/*.zip", start = -1))

        assertEquals(
            BatchPatternProblem.START_OUT_OF_RANGE,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `a broken host is caught before any probing`() {
        // The sample substitution is validated, so a typo costs one message
        // instead of 200 failed requests.
        val check = BatchPattern.check(BatchPatternSpec(pattern = "not a url *.zip"))

        assertEquals(
            BatchPatternProblem.INVALID_URL,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `a valid pattern is accepted in both modes`() {
        val auto = BatchPattern.check(
            BatchPatternSpec(pattern = "https://e.com/*.zip", mode = BatchPatternMode.AUTO),
        )
        val range = BatchPattern.check(
            BatchPatternSpec(
                pattern = "https://e.com/*.zip",
                mode = BatchPatternMode.RANGE,
                start = 1,
                end = 10,
            ),
        )

        assertTrue(auto is BatchPatternCheck.Valid)
        assertTrue(range is BatchPatternCheck.Valid)
        // The trimmed pattern is what gets stored, so the queued URLs match the
        // preview exactly.
        assertEquals(
            "https://e.com/*.zip",
            (auto as BatchPatternCheck.Valid).spec.pattern,
        )
    }

    @Test
    fun `a zero step is rejected in range mode`() {
        val check = BatchPattern.check(
            BatchPatternSpec(
                pattern = "https://e.com/*.zip",
                mode = BatchPatternMode.RANGE,
                step = 0,
            ),
        )

        assertEquals(
            BatchPatternProblem.STEP_OUT_OF_RANGE,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `end before start is rejected`() {
        val check = BatchPattern.check(
            BatchPatternSpec(
                pattern = "https://e.com/*.zip",
                mode = BatchPatternMode.RANGE,
                start = 10,
                end = 5,
            ),
        )

        assertEquals(
            BatchPatternProblem.END_BEFORE_START,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `a range larger than the batch ceiling is rejected`() {
        val check = BatchPattern.check(
            BatchPatternSpec(
                pattern = "https://e.com/*.zip",
                mode = BatchPatternMode.RANGE,
                start = 1,
                end = BatchPattern.MAX_GENERATED + 1,
            ),
        )

        assertEquals(
            BatchPatternProblem.TOO_MANY,
            (check as BatchPatternCheck.Invalid).problem,
        )
    }

    @Test
    fun `auto mode ignores the range bounds`() {
        // The scan decides where the sequence ends; start/end are only used by
        // range mode, so a nonsense end must not block a scan.
        val check = BatchPattern.check(
            BatchPatternSpec(
                pattern = "https://e.com/*.zip",
                mode = BatchPatternMode.AUTO,
                start = 10,
                end = 1,
            ),
        )

        assertTrue(check is BatchPatternCheck.Valid)
    }

    // ---- Expansion -------------------------------------------------------

    @Test
    fun `range count is inclusive of both ends`() {
        assertEquals(1L, BatchPattern.rangeCount(spec(start = 1, end = 1)))
        assertEquals(10L, BatchPattern.rangeCount(spec(start = 1, end = 10)))
        assertEquals(5L, BatchPattern.rangeCount(spec(start = 0, end = 4)))
    }

    @Test
    fun `range count honours the step`() {
        // 0, 2, 4, 6, 8, 10 — the last step lands exactly on the end.
        assertEquals(6L, BatchPattern.rangeCount(spec(start = 0, end = 10, step = 2)))
        // 1, 4, 7, 10
        assertEquals(4L, BatchPattern.rangeCount(spec(start = 1, end = 10, step = 3)))
    }

    @Test
    fun `range count is zero when the range is inverted`() {
        assertEquals(0L, BatchPattern.rangeCount(spec(start = 10, end = 1)))
    }

    @Test
    fun `expand produces the numbered urls in order`() {
        val urls = BatchPattern.expand(spec(start = 1, end = 4, padding = 2))

        assertEquals(
            listOf(
                "https://example.com/file01.zip",
                "https://example.com/file02.zip",
                "https://example.com/file03.zip",
                "https://example.com/file04.zip",
            ),
            urls,
        )
    }

    @Test
    fun `expand honours the step`() {
        val urls = BatchPattern.expand(spec(start = 2, end = 10, step = 4, padding = 2))

        assertEquals(
            listOf(
                "https://example.com/file02.zip",
                "https://example.com/file06.zip",
                "https://example.com/file10.zip",
            ),
            urls,
        )
    }

    @Test
    fun `expand with no padding leaves the numbers bare`() {
        val urls = BatchPattern.expand(spec(start = 8, end = 10, padding = 0))

        assertEquals(
            listOf(
                "https://example.com/file8.zip",
                "https://example.com/file9.zip",
                "https://example.com/file10.zip",
            ),
            urls,
        )
    }

    @Test
    fun `expand is deterministic`() {
        val spec = spec(start = 1, end = 50, step = 3, padding = 4)

        assertEquals(BatchPattern.expand(spec), BatchPattern.expand(spec))
    }

    @Test
    fun `expand never exceeds the batch ceiling`() {
        val urls = BatchPattern.expand(
            spec(start = 0, end = BatchPattern.MAX_INDEX, step = 1),
        )

        assertEquals(BatchPattern.MAX_GENERATED, urls.size)
    }

    @Test
    fun `auto candidates stop at the attempt ceiling`() {
        val urls = BatchPattern.autoCandidates(spec(start = 1, end = 1, padding = 3))

        assertEquals(BatchPattern.AUTO_MAX_ATTEMPTS, urls.size)
        assertEquals("https://example.com/file001.zip", urls.first())
        assertEquals("https://example.com/file200.zip", urls.last())
    }

    @Test
    fun `planned count matches what each mode can produce`() {
        assertEquals(
            10,
            BatchPattern.plannedCount(spec(start = 1, end = 10, mode = BatchPatternMode.RANGE)),
        )
        assertEquals(
            BatchPattern.AUTO_MAX_ATTEMPTS,
            BatchPattern.plannedCount(spec(start = 1, end = 10, mode = BatchPatternMode.AUTO)),
        )
    }

    @Test
    fun `a zero start is allowed`() {
        // Windows allows `0 <= start`; episode and part numbering often starts at 0.
        val check = BatchPattern.check(
            BatchPatternSpec(pattern = "https://e.com/*.zip", mode = BatchPatternMode.RANGE, start = 0, end = 3),
        )

        assertTrue(check is BatchPatternCheck.Valid)
        assertEquals("https://example.com/file00.zip", BatchPattern.expand(spec(start = 0, end = 3))[0])
    }

    @Test
    fun `invalid specs never expand into urls`() {
        // expand() itself is unchecked by design (the caller validates first),
        // but it must still refuse an inverted range rather than loop forever.
        assertTrue(BatchPattern.expand(spec(start = 5, end = 1)).isEmpty())
    }

    private fun spec(
        start: Int = 1,
        end: Int = 10,
        step: Int = 1,
        padding: Int = 2,
        mode: BatchPatternMode = BatchPatternMode.RANGE,
    ) = BatchPatternSpec(
        pattern = "https://example.com/file*.zip",
        mode = mode,
        start = start,
        end = end,
        step = step,
        padding = padding,
    )
}
