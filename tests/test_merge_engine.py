"""Merge engine: assemble parts into the final file without lying about it.

The merge is the one step that can turn a correct set of parts into a wrong
file, so it is tested against the ways a part set can be malformed — a missing
part, a short part, an over-long part, a size that does not add up — as well as
the fast path that avoids copying a single-part download at all.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.merge import merge_parts
from core.parts import DownloadPart, build_parts


def _part(directory: Path, index: int, start: int, end: int, payload: bytes) -> DownloadPart:
    path = directory / f"blob.bin.part{index}"
    path.write_bytes(payload)
    return DownloadPart(index=index, start=start, end=end, path=path)


class MergeEngineTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.out = self.dir / "blob.bin"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- fast path ----------------------------------------------------

    def test_single_part_is_renamed_not_copied(self):
        payload = b"x" * 4096
        part = _part(self.dir, 0, 0, len(payload) - 1, payload)
        ok, err = merge_parts([part], self.out, 1 << 20, expected_size=len(payload))
        self.assertTrue(ok, err)
        self.assertEqual(self.out.read_bytes(), payload)
        # The part file was moved, not duplicated, and no staging file remains.
        self.assertFalse(part.path.exists())
        self.assertFalse((self.dir / "blob.bin.merging").exists())

    def test_single_part_wrong_size_is_refused(self):
        part = _part(self.dir, 0, 0, 99, b"too short")
        ok, err = merge_parts([part], self.out, 1 << 20)
        self.assertFalse(ok)
        self.assertIn("incomplete", err)
        self.assertFalse(self.out.exists())

    def test_single_part_expected_size_mismatch_is_refused(self):
        payload = b"y" * 100
        part = _part(self.dir, 0, 0, 99, payload)
        ok, err = merge_parts([part], self.out, 1 << 20, expected_size=101)
        self.assertFalse(ok)
        self.assertIn("Merged size mismatch", err)

    # ---- multi-part ---------------------------------------------------

    def test_parts_are_concatenated_in_index_order(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BBBB"),
            _part(self.dir, 2, 8, 11, b"CCCC"),
        ]
        ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=12)
        self.assertTrue(ok, err)
        self.assertEqual(self.out.read_bytes(), b"AAAABBBBCCCC")
        self.assertFalse((self.dir / "blob.bin.merging").exists())

    def test_oversized_part_contributes_exactly_its_range(self):
        # A part file longer than its declared range (an appended retry, or a
        # server that overshot) must not leak extra bytes into the output.
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA" + b"XXXX"),
            _part(self.dir, 1, 4, 7, b"BBBB"),
        ]
        ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=8)
        self.assertTrue(ok, err)
        self.assertEqual(self.out.read_bytes(), b"AAAABBBB")

    def test_missing_part_is_reported_before_anything_is_written(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            DownloadPart(index=1, start=4, end=7, path=self.dir / "gone.part1"),
        ]
        ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=8)
        self.assertFalse(ok)
        self.assertIn("Missing part file", err)
        self.assertFalse(self.out.exists())
        # Nothing was staged, so there is nothing to clean up either.
        self.assertFalse((self.dir / "blob.bin.merging").exists())

    def test_short_part_is_reported(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BB"),
        ]
        ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=8)
        self.assertFalse(ok)
        self.assertIn("Part 1 incomplete", err)
        self.assertFalse((self.dir / "blob.bin.merging").exists())

    def test_merged_size_mismatch_removes_the_staging_file(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BBBB"),
        ]
        ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=999)
        self.assertFalse(ok)
        self.assertIn("Merged size mismatch", err)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.dir / "blob.bin.merging").exists())

    def test_parts_survive_a_failed_merge(self):
        # A failure must leave the parts intact so the download can be resumed.
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BB"),
        ]
        merge_parts(parts, self.out, 1 << 20, expected_size=8)
        for part in parts:
            self.assertTrue(part.path.exists(), f"{part.path} was deleted on failure")

    def test_no_parts(self):
        ok, err = merge_parts([], self.out, 1 << 20)
        self.assertFalse(ok)
        self.assertIn("No parts", err)

    # ---- disk space ---------------------------------------------------

    def test_insufficient_free_space_is_refused_up_front(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BBBB"),
        ]
        usage = shutil.disk_usage
        with patch(
            "core.merge.shutil.disk_usage",
            side_effect=lambda _p: usage(self.dir).__class__(usage(self.dir).total, 1, 1),
        ):
            ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=8)
        self.assertFalse(ok)
        self.assertIn("free disk space", err)
        self.assertFalse(self.out.exists())
        for part in parts:
            self.assertTrue(part.path.exists())

    def test_unreadable_free_space_does_not_block_the_merge(self):
        parts = [
            _part(self.dir, 0, 0, 3, b"AAAA"),
            _part(self.dir, 1, 4, 7, b"BBBB"),
        ]
        with patch("core.merge.shutil.disk_usage", side_effect=OSError("nope")):
            ok, err = merge_parts(parts, self.out, 1 << 20, expected_size=8)
        self.assertTrue(ok, err)
        self.assertEqual(self.out.read_bytes(), b"AAAABBBB")


class BuildPartsTest(unittest.TestCase):
    """The planner must never create more parts than the file can support."""

    def test_small_file_is_one_part(self):
        parts = build_parts(1000, num_threads=8, file_path=Path("x.bin"), min_part_size=1 << 20)
        self.assertEqual(len(parts), 1)
        self.assertEqual((parts[0].start, parts[0].end), (0, 999))

    def test_parts_tile_the_file_without_gaps_or_overlap(self):
        total = 10 * 1024 * 1024 + 7
        parts = build_parts(total, num_threads=4, file_path=Path("x.bin"), min_part_size=1 << 20)
        self.assertEqual(parts[0].start, 0)
        self.assertEqual(parts[-1].end, total - 1)
        for previous, current in zip(parts, parts[1:]):
            self.assertEqual(current.start, previous.end + 1, "gap or overlap between parts")
        self.assertEqual(sum(p.size for p in parts), total)

    def test_thread_count_is_capped_by_file_size(self):
        # 3 MB with a 2 MB minimum part size can only support one part.
        parts = build_parts(3 * 1024 * 1024, num_threads=16, file_path=Path("x.bin"),
                            min_part_size=2 * 1024 * 1024)
        self.assertEqual(len(parts), 1)

    def test_unknown_size_is_a_single_part(self):
        parts = build_parts(0, num_threads=4, file_path=Path("x.bin"), min_part_size=1 << 20)
        self.assertEqual(len(parts), 1)


if __name__ == "__main__":
    unittest.main()
