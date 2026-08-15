"""Single source of truth for the N13 application version."""

from __future__ import annotations

VERSION = "1.0.5"


def get_version() -> str:
    """Return the current application version string."""
    return VERSION


def parse_version(value: str):
    """Parse a semantic version string into a comparable integer tuple.

    Returns ``None`` if the string is not a valid ``MAJOR.MINOR.PATCH`` value.
    """
    import re

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", (value or "").strip())
    if not match:
        return None
    return tuple(int(match.group(i)) for i in range(1, 4))


def compare_versions(a: str, b: str) -> int:
    """Compare two semantic version strings.

    Returns ``-1`` if *a* < *b*, ``0`` if equal, ``1`` if *a* > *b*.
    """
    va = parse_version(a)
    vb = parse_version(b)
    if va is None or vb is None:
        return 0
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0
