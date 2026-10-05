"""Filesystem checks for a project's destination folder.

Separated from :mod:`projects.models` because the model layer is pure and must
stay testable without touching a disk.  Separated from
:mod:`projects.repository` because "is this a usable folder?" is a question about
the operating system, not about SQL.

Every failure raises :class:`~projects.models.ProjectValidationError` with a
machine-readable ``code``.  The UI translates the code; this module never ships
user-facing prose, so the message can be localised and the raw OS text can still
be logged for diagnosis.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

from projects.models import ProjectValidationError

# Windows MAX_PATH is 260 including the trailing "\0" and the file name, and the
# download engine appends ".part0", ".n13tmp" and similar suffixes.  Rejecting a
# path that leaves no room produces a clear error now instead of a mysterious
# failure halfway through a download.
MAX_DIRECTORY_LENGTH = 200


def normalize_directory(raw: Any) -> str:
    return str(raw or "").strip()


def _writable(path: Path) -> bool:
    try:
        return os.access(str(path), os.W_OK)
    except OSError:
        return False


def _from_oserror(exc: OSError, path: Path) -> ProjectValidationError:
    """Map an OS failure onto a code the UI can explain."""
    winerror = getattr(exc, "winerror", None)
    err = getattr(exc, "errno", None)

    if winerror in (5, 65) or isinstance(exc, PermissionError):
        return ProjectValidationError(
            "directory_permission_denied",
            f"No permission to use {path}. Pick another folder, or run N13 as an "
            f"administrator. ({exc})",
            "directory",
        )
    if winerror in (3, 15, 53, 64, 67, 123) or isinstance(
        exc, (FileNotFoundError, NotADirectoryError)
    ):
        return ProjectValidationError(
            "directory_unavailable",
            f"{path} is not reachable. If it is a removable or network drive, "
            f"reconnect it and try again. ({exc})",
            "directory",
        )
    if err == errno.ENOSPC or winerror == 112:
        return ProjectValidationError(
            "disk_full",
            f"There is no free space on the drive holding {path}.",
            "directory",
        )
    if err == errno.EROFS or winerror == 19:
        return ProjectValidationError(
            "directory_read_only",
            f"{path} is on a read-only drive.",
            "directory",
        )
    return ProjectValidationError(
        "directory_unavailable",
        f"{path} could not be used as a destination: {exc}",
        "directory",
    )


def check_directory(
    raw: Any, *, create: bool = True, require_writable: bool = True
) -> str:
    """Validate a project destination and return it cleaned.

    An empty value is valid and means "inherit the application download folder" —
    the same convention the settings layer uses, so a project does not have to
    duplicate a path the user may later change.

    Raises :class:`ProjectValidationError` with one of:
    ``directory_too_long``, ``directory_not_absolute``, ``directory_not_a_folder``,
    ``directory_unavailable``, ``directory_permission_denied``,
    ``directory_not_writable``, ``directory_read_only``, ``disk_full``.
    """
    text = normalize_directory(raw)
    if not text:
        return ""

    try:
        path = Path(text).expanduser()
    except (OSError, ValueError) as exc:
        raise ProjectValidationError(
            "directory_invalid", f"{text!r} is not a usable path. ({exc})", "directory"
        ) from exc

    if len(str(path)) > MAX_DIRECTORY_LENGTH:
        raise ProjectValidationError(
            "directory_too_long",
            f"The destination path is too long ({len(str(path))} characters; the "
            f"limit is {MAX_DIRECTORY_LENGTH}). Choose a shorter folder.",
            "directory",
        )

    if not path.is_absolute():
        raise ProjectValidationError(
            "directory_not_absolute",
            f"{path} is a relative path. Enter a full path such as "
            f"C:\\Downloads\\Movies.",
            "directory",
        )

    if path.exists() and not path.is_dir():
        raise ProjectValidationError(
            "directory_not_a_folder",
            f"{path} is a file, not a folder.",
            "directory",
        )

    if create and not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _from_oserror(exc, path) from exc

    if require_writable and not _writable(path):
        raise ProjectValidationError(
            "directory_not_writable",
            f"N13 cannot write to {path}. Check the folder's permissions.",
            "directory",
        )

    return str(path)
