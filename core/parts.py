"""Download part definitions and adaptive threading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import List
from uuid import uuid4


@dataclass
class DownloadPart:
    index: int
    start: int
    end: int
    path: Path
    done: bool = False
    # A "shared" part writes into the final file at ``start + written`` instead
    # of into its own ``.partN`` sidecar.  That removes the merge copy at the
    # end of a segmented download; ``written`` is the authoritative prefix
    # length for the range because several ranges share one file and its total
    # length cannot identify which ranges are complete.
    shared: bool = False
    written: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def downloaded_size(self) -> int:
        if self.shared:
            with self._lock:
                return min(max(0, self.written), self.size)
        if not self.path.exists():
            return 0
        size = self.path.stat().st_size
        return min(size, self.size)

    def advance(self, byte_count: int) -> None:
        """Record bytes written by a shared-file worker."""
        if byte_count <= 0:
            return
        with self._lock:
            self.written = min(self.size, self.written + byte_count)

    def snapshot(self) -> int:
        """Thread-safe read of the written prefix."""
        return self.downloaded_size

    @property
    def is_complete(self) -> bool:
        return self.downloaded_size >= self.size


def adaptive_thread_count(total_size: int, requested: int, min_part_size: int) -> int:
    if total_size <= 0:
        return 1
    max_by_size = max(1, total_size // max(min_part_size, 1))
    return max(1, min(requested, max_by_size))


def build_parts(
    total_size: int,
    num_threads: int,
    file_path: Path,
    min_part_size: int,
) -> List[DownloadPart]:
    effective_threads = adaptive_thread_count(total_size, num_threads, min_part_size)
    if effective_threads <= 1:
        return [
            DownloadPart(
                index=0,
                start=0,
                end=total_size - 1,
                path=file_path.with_suffix(f"{file_path.suffix}.part0"),
            )
        ]

    part_size = total_size // effective_threads
    parts: List[DownloadPart] = []
    for i in range(effective_threads):
        start = i * part_size
        end = (start + part_size - 1) if i < effective_threads - 1 else total_size - 1
        part_path = file_path.with_suffix(f"{file_path.suffix}.part{i}")
        parts.append(DownloadPart(index=i, start=start, end=end, path=part_path))
    return parts


def build_direct_parts(
    total_size: int,
    num_threads: int,
    file_path: Path,
    min_part_size: int,
) -> List[DownloadPart]:
    """Build a segmented plan that writes straight into ``file_path``.

    The returned parts still describe the same byte ranges as
    :func:`build_parts`, but every worker writes at its absolute offset in the
    destination.  Completion is therefore known before any merge: the final
    validation gate reads the destination itself.  Resume state records each
    range's written prefix separately.
    """
    parts = build_parts(total_size, num_threads, file_path, min_part_size)
    for part in parts:
        part.path = file_path
        part.shared = True
        part.written = 0
    return parts


def remap_parts_for_resume(
    old_parts: List[DownloadPart],
    total_size: int,
    num_threads: int,
    file_path: Path,
    min_part_size: int,
) -> List[DownloadPart]:
    """Safely preserve contiguous data when the number of parts changes.

    A part file represents bytes starting at its own range start, so copying an
    arbitrary overlap into a new part can silently corrupt a resumed download.
    New part paths are therefore unique and receive only the contiguous prefix
    that can be proven to exist in the prior layout.
    """
    if not old_parts:
        return build_parts(total_size, num_threads, file_path, min_part_size)

    new_parts = build_parts(total_size, num_threads, file_path, min_part_size)
    remap_id = uuid4().hex[:10]
    for new_part in new_parts:
        new_part.path = file_path.with_suffix(
            f"{file_path.suffix}.repart-{remap_id}-{new_part.index}"
        )

    sources = sorted(old_parts, key=lambda part: part.start)
    for new_part in new_parts:
        position = new_part.start
        new_part.path.parent.mkdir(parents=True, exist_ok=True)
        with open(new_part.path, "wb") as output:
            while position <= new_part.end:
                source = next(
                    (
                        part
                        for part in sources
                        if part.start <= position <= part.end and part.path.exists()
                    ),
                    None,
                )
                if source is None:
                    break
                source_offset = position - source.start
                available = max(0, source.downloaded_size - source_offset)
                if available == 0:
                    break
                length = min(available, new_part.end - position + 1)
                # Open the source file ONCE per source segment and read the
                # full contiguous region — previously re-opened on every loop
                # iteration, causing O(N²) file-open overhead on large remaps.
                try:
                    with open(source.path, "rb") as input_file:
                        input_file.seek(source_offset)
                        remaining = length
                        while remaining:
                            block = input_file.read(min(1024 * 1024, remaining))
                            if not block:
                                length = 0
                                break
                            output.write(block)
                            written = len(block)
                            remaining -= written
                            position += written
                except OSError:
                    # Source file disappeared mid-copy (e.g. disk full, other
                    # process deleted it).  Stop copying this new part; it will
                    # be re-downloaded from scratch.
                    length = 0
                if length == 0:
                    break
        new_part.done = new_part.is_complete

    # The new state points only at the remapped files. Old files may overlap
    # different ranges now, so retaining them would consume space without being
    # usable by the resume algorithm.
    for source in old_parts:
        source.path.unlink(missing_ok=True)

    return new_parts
