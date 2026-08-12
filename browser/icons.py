"""Copy the modern N13 icon assets into a Chrome extension directory."""

from __future__ import annotations

import shutil
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_extension_icons(ext_dir: Path) -> None:
    """Copy the generated icon set into *ext_dir* for use by the extension."""
    src_root = _project_root() / "assets" / "icons"
    if not src_root.exists():
        raise RuntimeError(f"Icon assets not found at {src_root}. Run assets/make_icon.py first.")

    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    for size in sizes:
        src = src_root / f"icon-{size}.png"
        if src.exists():
            shutil.copy2(src, ext_dir / f"icon{size}.png")
