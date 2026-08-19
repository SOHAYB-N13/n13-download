"""Copy the modern N13 icon assets into a Chrome extension directory."""

from __future__ import annotations

import shutil
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_extension_icons(ext_dir: Path) -> None:
    """Copy the generated icon set into *ext_dir* for use by the extension.

    The extension template already ships every icon (``icon{size}.png``), so
    this step is only a dev-time refresh of the generated assets.  In a frozen
    (installed) build ``assets/icons`` is not bundled, so it must be a no-op
    rather than a fatal error — otherwise "Create Chrome extension copy" fails
    in the installed application.
    """
    src_root = _project_root() / "assets" / "icons"
    if not src_root.exists():
        return

    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    for size in sizes:
        src = src_root / f"icon-{size}.png"
        if src.exists():
            shutil.copy2(src, ext_dir / f"icon{size}.png")
