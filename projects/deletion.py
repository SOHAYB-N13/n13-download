"""Deleting a project's downloads from disk — the only destructive path.

Split out of :mod:`projects.service` because it is a different *kind* of code
from everything else there.  The rest of the service decides what a project
means; this module removes files from a user's disk, and it is the one place in
the project layer where a mistake is unrecoverable.

Two guards make it safe to expose:

* a file must resolve **inside the project's own destination directory**, so a
  task whose path was retargeted by hand can never make this delete something
  elsewhere on the disk;
* a file referenced by a task in **another** project is skipped, because two
  projects may legitimately point at the same folder.

Files are deleted **before** the database rows, and a single failure aborts the
whole operation.  The alternative — drop the rows and report a locked file
afterwards — would leave the user with no way to retry and no record of what
survived.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from core import artifacts
from projects.models import Project, ProjectValidationError

logger = logging.getLogger("n13")


def real_path(path: str) -> Optional[str]:
    """Fully resolved, case-normalised path — ``None`` when it cannot be resolved."""
    if not path:
        return None
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return None


def is_within(path: str, root: str) -> bool:
    """True when *path* is *root* itself or lives underneath it."""
    if not path or not root:
        return False
    if path == root:
        return True
    return path.startswith(root.rstrip(os.sep) + os.sep)


def _task_base(row: Dict[str, Any]) -> Optional[Path]:
    return artifacts.task_base_path(
        str(row.get("resolved_path") or ""),
        str(row.get("directory") or ""),
        str(row.get("filename") or ""),
        str(row.get("label") or ""),
    )


def referenced_by_other_projects(tasks: Any, project_id: str) -> Set[str]:
    """Every real path owned by a task that does **not** belong to *project_id*.

    Used to skip a shared file: two projects may point at the same folder, and
    deleting one project must not remove a download the other still lists.
    """
    shared: Set[str] = set()
    for row in tasks.list_tasks():
        if str(row.get("project_id") or "") == project_id:
            continue
        base = _task_base(row)
        if base is None:
            continue
        real = real_path(str(base))
        if real:
            shared.add(real)
    return shared


def delete_project_files(project: Project, tasks: Any) -> int:
    """Delete the downloads a project owns.  Returns how many files were removed.

    Raises :class:`~projects.models.ProjectValidationError` with
    ``files_require_directory`` when the project has no usable destination — with
    no folder there is no way to tell which files are the project's own — and
    with ``file_delete_failed`` when any file could not be removed.
    """
    if not project.directory:
        raise ProjectValidationError(
            "files_require_directory",
            "This project has no destination folder, so N13 cannot tell which "
            "files are its own. Delete the records instead.",
            "directory",
        )
    root = real_path(str(project.directory))
    if root is None:
        raise ProjectValidationError(
            "files_require_directory",
            f"{project.directory} is not reachable right now.",
            "directory",
        )

    mine: Sequence[Dict[str, Any]] = tasks.list_tasks_by_project(project.id)
    shared = referenced_by_other_projects(tasks, project.id)

    plan: List[Path] = []
    for row in mine:
        base = _task_base(row)
        if base is None:
            continue
        for path in artifacts.deletion_plan(base):
            real = real_path(str(path))
            if real is None or not is_within(real, root):
                continue
            if real in shared:
                logger.info("Keeping %s: another project references it", path)
                continue
            plan.append(Path(real))

    failures: List[str] = []
    deleted = 0
    for path in dict.fromkeys(plan):
        try:
            if path.is_dir():  # never remove a folder, only files
                continue
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{path}: {exc}")
            logger.warning("Could not delete %s: %s", path, exc)

    if failures:
        raise ProjectValidationError(
            "file_delete_failed",
            "Some files could not be deleted, so the project was left in place. "
            "Close whatever is using them and try again, or delete the records "
            "only. " + "; ".join(failures[:5]),
            "directory",
        )
    return deleted


__all__ = [
    "delete_project_files",
    "is_within",
    "real_path",
    "referenced_by_other_projects",
]
