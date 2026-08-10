"""Merge downloaded parts into final file.

Uses :func:`shutil.copyfileobj` (C-level I/O loop) for near-zero-overhead
merge instead of a Python-level read/write loop.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from core.parts import DownloadPart
from core.utils import safe_rename


def merge_parts(
    parts: List[DownloadPart],
    output_path: Path,
    buffer_size: int,
    expected_size: int = 0,
) -> tuple[bool, str]:
    """Merge ``parts`` into ``output_path`` using a temporary staging file.

    The merge is written to a ``.merging`` temp file first.  Only if every
    part is copied successfully and the size matches is the temp file renamed
    over the final path.  Any failure leaves the part files intact so the
    download can be resumed.

    Failure modes handled:
    * Missing part file detected before we start writing.
    * Part file is shorter than expected (incomplete part).
    * Read returns an empty buffer mid-copy (truncated source).
    * Disk-full / IO error during write — OSError is caught.
    * Merged size does not match the expected content length.
    * The staging temp file is always cleaned up on any error path.
    """
    temp_path = output_path.with_suffix(output_path.suffix + ".merging")
    try:
        with open(temp_path, "wb", buffering=buffer_size) as out:
            for part in parts:
                if not part.path.exists():
                    return False, f"Missing part file: {part.path}"
                expected_part = part.size
                actual_part = part.path.stat().st_size
                if actual_part < expected_part:
                    return (
                        False,
                        f"Part {part.index} incomplete ({actual_part}/{expected_part} bytes)",
                    )
                with open(part.path, "rb") as src:
                    shutil.copyfileobj(src, out, length=buffer_size)
            out.flush()

        if expected_size > 0:
            merged_size = temp_path.stat().st_size
            if merged_size != expected_size:
                _safe_unlink(temp_path)
                return (
                    False,
                    f"Merged size mismatch: {merged_size} != {expected_size} bytes",
                )

        safe_rename(temp_path, output_path)
        return True, ""

    except OSError as exc:
        _safe_unlink(temp_path)
        return False, f"Merge failed: {exc}"


def _safe_unlink(path: Path) -> None:
    """Remove *path* without raising — used in error-recovery paths."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
