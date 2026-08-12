"""Generate the N13 icon asset set from the source artwork.

Reads ``icone.png`` at the repository root, center-crops it to a square, and
writes:

  assets/icon.png              1024x1024 master
  assets/icons/icon-16.png
  assets/icons/icon-20.png
  assets/icons/icon-24.png
  assets/icons/icon-32.png
  assets/icons/icon-48.png
  assets/icons/icon-64.png
  assets/icons/icon-128.png
  assets/icons/icon-256.png
  assets/icon.ico              multi-resolution Windows icon
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "icone.png"
SIZES = [16, 20, 24, 32, 48, 64, 128, 256]
MASTER_SIZE = 1024


def load_source() -> Image.Image:
    """Open icone.png and center-crop it to a square."""
    if not SOURCE.exists():
        raise FileNotFoundError(f"Source artwork not found: {SOURCE}")
    img = Image.open(SOURCE)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def main() -> int:
    icons_dir = ROOT / "assets" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    master = load_source().resize((MASTER_SIZE, MASTER_SIZE), Image.LANCZOS)
    master.save(ROOT / "assets" / "icon.png", "PNG")

    for s in SIZES:
        scaled = master.resize((s, s), Image.LANCZOS)
        scaled.save(icons_dir / f"icon-{s}.png", "PNG")
        print(f"Created icon-{s}.png")

    ico_sizes = [(s, s) for s in [16, 20, 24, 32, 48, 64, 128, 256]]
    ico_images = [master.resize((s, s), Image.LANCZOS) for s, _ in ico_sizes]
    # PIL's ICO writer skips any size larger than the FIRST image passed to
    # save().  Pass the largest (256x256) first so every size is kept.
    ico_images[-1].save(
        ROOT / "assets" / "icon.ico",
        format="ICO",
        sizes=ico_sizes,
        append_images=ico_images[:-1],
    )
    print("Created icon.ico")
    print("Created icon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
