"""Merge downloaded parts into final file.

Design
======
Parts are separate files on disk, so assembling them means copying bytes.  The
merge is written to a staging file first and only renamed over the destination
once every part has been copied and the total matches, so an interrupted merge
can never leave a half-file in place of a good download.

Two things keep that copy honest and cheap:

* **Exact-length copy** — each part contributes exactly ``part.size`` bytes.
  The previous version used :func:`shutil.copyfileobj` with no length limit, so
  an over-long part file (a retry that appended, a range the server overshot)
  silently wrote extra bytes and the failure only surfaced later as a size
  mismatch, after the whole staging file had been written.
* **Single-part fast path** — when the download produced one part (a small
  file, or a server without range support that still took the ranged path),
  the part file *is* the finished file.  Renaming it is atomic and skips a full
  read+write of the whole download.

Free space is checked up front: running out of disk mid-merge is recoverable
(the parts survive) but produces a confusing error, so it is better to refuse
before touching the disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from core.parts import DownloadPart
from core.utils import safe_rename


def _free_space_check(directory: Path, needed: int) -> str:
    """Return an error message when *directory* clearly cannot hold *needed*.

    Best-effort: a filesystem that cannot report free space is not treated as
    full, because refusing a merge on a probe failure would be worse than
    trying it and reporting the real ``ENOSPC``.
    """
    if needed <= 0:
        return ""
    try:
        free = shutil.disk_usage(str(directory)).free
    except OSError:
        return ""
    if free < needed:
        return (
            f"Not enough free disk space to merge: need {needed} bytes, "
            f"{free} available"
        )
    return ""


def _copy_exact(src, dst, length: int, buffer_size: int) -> None:
    """Copy exactly *length* bytes, raising if the source ends early."""
    remaining = length
    while remaining > 0:
        block = src.read(min(buffer_size, remaining))
        if not block:
            raise OSError(
                f"source ended after {length - remaining} of {length} bytes"
            )
        dst.write(block)
        remaining -= len(block)


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
    * A part ends early mid-copy (truncated source).
    * Disk-full / IO error during write — OSError is caught.
    * Merged size does not match the expected content length.
    * Not enough free disk space to hold the result.
    * The staging temp file is always cleaned up on any error path.
    """
    if not parts:
        return False, "No parts to merge"

    total_bytes = sum(part.size for part in parts)

    # ---- Single-part fast path: the part *is* the file -------------------
    if len(parts) == 1:
        only = parts[0]
        if not only.path.exists():
            return False, f"Missing part file: {only.path}"
        actual = only.path.stat().st_size
        if actual != only.size:
            return False, f"Part {only.index} incomplete ({actual}/{only.size} bytes)"
        if expected_size > 0 and actual != expected_size:
            return False, f"Merged size mismatch: {actual} != {expected_size} bytes"
        try:
            safe_rename(only.path, output_path)
        except OSError as exc:
            return False, f"Merge failed: {exc}"
        return True, ""

    temp_path = output_path.with_suffix(output_path.suffix + ".merging")
    try:
        # Validate every part *before* writing anything, so a missing part is
        # reported without having produced a staging file to clean up.
        for part in parts:
            if not part.path.exists():
                return False, f"Missing part file: {part.path}"
            actual_part = part.path.stat().st_size
            if actual_part < part.size:
                return (
                    False,
                    f"Part {part.index} incomplete ({actual_part}/{part.size} bytes)",
                )

        space_error = _free_space_check(temp_path.parent, total_bytes)
        if space_error:
            return False, space_error

        with open(temp_path, "wb", buffering=buffer_size) as out:
            for part in parts:
                with open(part.path, "rb") as src:
                    _copy_exact(src, out, part.size, buffer_size)
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
