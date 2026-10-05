"""Which files on disk belong to a single download task.

One definition, because two callers must agree on it:

* ``ui/common.py::TaskManager._cleanup_task_files`` removes the temporary
  artifacts of a task that was cancelled or failed.
* ``projects/service.py`` removes the final file *and* its artifacts when the
  user explicitly deletes a project together with its downloads.

If those two drifted apart, one of them would start leaving orphans behind —
partial ``.part3`` files nobody owns, or worse, a cleanup that stopped matching
and quietly stopped working.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Optional

# Sidecars the engine writes next to the final file.  ``core/parts.py`` produces
# ``<name>.<ext>.partN``; merge and state files add the rest.  Kept as a suffix
# alternation so it can be appended to an escaped base name.
ARTIFACT_SUFFIX_PATTERN = r"\.(part\d+|repart-[0-9a-f]+-\d+|tmp|merging|dlstate)$"

ARTIFACT_SUFFIX_RE = re.compile(ARTIFACT_SUFFIX_PATTERN, re.IGNORECASE)


def task_base_path(
    resolved_path: str = "",
    directory: str = "",
    filename: str = "",
    label: str = "",
) -> Optional[Path]:
    """The final destination file of a task, or ``None`` when it is unknown.

    Prefers ``resolved_path`` — the engine's post-unique-name answer — and falls
    back to ``directory/filename``.  Never guesses a directory: an empty
    ``directory`` yields ``None`` rather than the process working directory,
    which would scope a delete to somewhere the task never wrote.
    """
    if resolved_path:
        return Path(str(resolved_path))
    name = str(filename or label or "")
    if not name or not directory:
        return None
    return Path(str(directory)) / name


def _artifact_matcher(base_name: str):
    try:
        return re.compile(r"^" + re.escape(base_name) + ARTIFACT_SUFFIX_PATTERN, re.IGNORECASE)
    except re.error:  # pragma: no cover - re.escape cannot produce an invalid pattern
        return None


def iter_artifacts(base: Path) -> Iterator[Path]:
    """Temporary siblings of *base*.  Never yields *base* itself."""
    try:
        if not base.parent.is_dir():
            return
    except OSError:
        return
    matcher = _artifact_matcher(base.name)
    if matcher is None:
        return
    try:
        entries = list(base.parent.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and matcher.match(entry.name):
                yield entry
        except OSError:
            continue


def deletion_plan(base: Path) -> List[Path]:
    """The final file plus every artifact — for an explicit, user-requested delete.

    Ordered final-file-last so that an interrupted delete leaves the artifacts
    gone and the real file present, rather than the reverse: a missing partial
    costs nothing, a missing final file costs a re-download.
    """
    plan: List[Path] = []
    try:
        if base.is_file():
            plan.append(base)
    except OSError:
        pass
    plan = list(iter_artifacts(base)) + plan
    return plan
