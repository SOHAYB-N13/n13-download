"""Unit-level integrity invariants.

These are the arithmetic and parsing rules the transfer layer depends on.  They
are cheap to test and each one, if wrong, produces a file of the right size with
the wrong bytes — so they are worth pinning precisely.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.download import _EntityGuard, _content_range_span
from core.parts import adaptive_thread_count, build_parts


class ContentRangeParsingTest(unittest.TestCase):
    def test_well_formed_header(self):
        self.assertEqual(_content_range_span("bytes 100-199/1000"), (100, 199, 1000))

    def test_unknown_total_is_reported_as_zero(self):
        self.assertEqual(_content_range_span("bytes 0-99/*"), (0, 99, 0))

    def test_missing_or_malformed_headers_are_unusable(self):
        for header in ["", "bytes", "bytes 0-99", "items 0-9/10", "bytes a-b/c",
                       "bytes 10-5/100", "bytes -1-9/10", "bytes 0-9/0"]:
            with self.subTest(header=header):
                self.assertIsNone(_content_range_span(header))

    def test_reversed_range_is_rejected(self):
        # A server that answers 200-100 is telling us nothing usable.
        self.assertIsNone(_content_range_span("bytes 200-100/1000"))

    def test_offsets_beyond_4gb_are_parsed_exactly(self):
        # A 32-bit parse would wrap here and silently address the wrong bytes,
        # so this pins the widths used for large-file support.
        total = 5 * 1024 ** 3
        start = 4 * 1024 ** 3 + 7
        self.assertEqual(
            _content_range_span(f"bytes {start}-{start + 1023}/{total}"),
            (start, start + 1023, total),
        )


class PartLayoutTest(unittest.TestCase):
    def _assert_covers(self, parts, total):
        """Parts must tile [0, total) exactly: no gap, no overlap."""
        ordered = sorted(parts, key=lambda p: p.start)
        self.assertEqual(ordered[0].start, 0)
        self.assertEqual(ordered[-1].end, total - 1)
        for left, right in zip(ordered, ordered[1:]):
            self.assertEqual(
                right.start, left.end + 1,
                f"gap or overlap between {left.start}-{left.end} and "
                f"{right.start}-{right.end}",
            )

    def test_parts_tile_the_entity_exactly(self):
        for total, threads in [(1000, 1), (1001, 3), (7, 8), (1, 4),
                               (5 * 1024 * 1024 + 7, 4)]:
            with self.subTest(total=total, threads=threads):
                parts = build_parts(total, threads, Path("x.bin"), 1 << 20)
                self._assert_covers(parts, total)
                self.assertEqual(sum(p.size for p in parts), total)

    def test_large_file_offsets_do_not_overflow(self):
        # 6 GB: past the 32-bit boundary, where a wrong width would wrap and
        # silently produce overlapping or missing ranges.
        total = 6 * 1024 ** 3 + 12345
        parts = build_parts(total, 8, Path("x.bin"), 2 * 1024 * 1024)
        self._assert_covers(parts, total)
        self.assertEqual(sum(p.size for p in parts), total)
        self.assertTrue(all(p.size > 0 for p in parts))
        self.assertGreater(parts[-1].end, 2 ** 32)

    def test_file_smaller_than_the_thread_count_is_not_over_split(self):
        parts = build_parts(1000, num_threads=16, file_path=Path("x.bin"),
                            min_part_size=1 << 20)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start, 0)
        self.assertEqual(parts[0].end, 999)

    def test_adaptive_thread_count_never_exceeds_the_entity(self):
        self.assertEqual(adaptive_thread_count(0, 16, 1 << 20), 1)
        self.assertEqual(adaptive_thread_count(1, 16, 1 << 20), 1)
        self.assertLessEqual(adaptive_thread_count(3 * 1024 * 1024, 16, 1 << 20), 3)
        self.assertEqual(adaptive_thread_count(1 << 30, 16, 1 << 20), 16)


class EntityGuardTest(unittest.TestCase):
    def test_first_observation_is_recorded(self):
        guard = _EntityGuard()
        self.assertTrue(guard.observe({"ETag": '"a"', "Last-Modified": "Mon"}))
        self.assertEqual(guard.etag, '"a"')
        self.assertEqual(guard.last_modified, "Mon")
        self.assertFalse(guard.changed)

    def test_a_different_etag_is_a_mismatch(self):
        guard = _EntityGuard(etag='"a"')
        self.assertFalse(guard.observe({"ETag": '"b"'}))
        self.assertTrue(guard.changed)

    def test_missing_validators_are_not_a_mismatch(self):
        # Some servers omit ETag on 206 responses.  That is "cannot tell",
        # not "changed" — failing here would break working downloads.
        guard = _EntityGuard(etag='"a"')
        self.assertTrue(guard.observe({}))
        self.assertFalse(guard.changed)

    def test_last_modified_is_used_when_there_is_no_etag(self):
        guard = _EntityGuard(last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
        self.assertFalse(
            guard.observe({"Last-Modified": "Tue, 02 Jan 2026 00:00:00 GMT"})
        )
        self.assertTrue(guard.changed)


if __name__ == "__main__":
    unittest.main()


class LargeFileStateTest(unittest.TestCase):
    """Resume state must survive sizes past the 32-bit boundary."""

    def test_state_for_a_file_over_4gb_round_trips(self):
        import tempfile
        import shutil

        from core.state import DownloadState

        total = 5 * 1024 ** 3 + 17
        tmp = Path(tempfile.mkdtemp())
        try:
            parts = build_parts(total, 4, tmp / "m.mkv", 1 << 20)
            mgr = DownloadState(tmp / "m.mkv.dlstate")
            mgr.save("http://example/x.mkv", total, parts, 4,
                     etag='"abc"', last_modified="Wed, 21 Oct 2015 07:28:00 GMT")
            loaded = mgr.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.total_size, total)
            self.assertEqual([p.start for p in loaded.parts],
                             [p.start for p in parts])
            self.assertEqual([p.end for p in loaded.parts],
                             [p.end for p in parts])
            self.assertEqual(loaded.etag, '"abc"')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
