"""Process Gemini-generated DMG volume icon.

Removes fake checkerboard transparency background, crops to the icon,
resizes to 1024x1024, and generates .icns file.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def remove_checkerboard_bg(img: Image.Image, threshold: int = 30) -> Image.Image:
    """Remove fake checkerboard transparency, keep only the icon."""
    rgba = img.convert("RGBA")
    data = np.array(rgba)

    r, g, b = data[:, :, 0], data[:, :, 1], data[:, :, 2]

    max_rgb = np.maximum(np.maximum(r.astype(int), g.astype(int)), b.astype(int))
    min_rgb = np.minimum(np.minimum(r.astype(int), g.astype(int)), b.astype(int))
    saturation = max_rgb - min_rgb
    brightness = (r.astype(int) + g.astype(int) + b.astype(int)) / 3

    is_background = (saturation < threshold) & (brightness > 150)
    is_shadow = (saturation < 20) & (brightness > 100) & (brightness <= 150)

    data[is_background | is_shadow, 3] = 0

    return Image.fromarray(data)


def crop_to_content(img: Image.Image) -> Image.Image:
    """Crop image to non-transparent content."""
    bbox = img.getbbox()
    if bbox is None:
        return img
    return img.crop(bbox)


def make_square(img: Image.Image) -> Image.Image:
    """Pad image to square, centered."""
    w, h = img.size
    size = max(w, h)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, ((size - w) // 2, (size - h) // 2))
    return result


def create_icns(png_path: str, icns_path: str):
    """Create .icns file from a 1024x1024 PNG using macOS iconutil."""
    iconset_dir = tempfile.mkdtemp(suffix=".iconset")

    img = Image.open(png_path).convert("RGBA")

    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    for size, filename in sizes:
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(f"{iconset_dir}/{filename}", "PNG")

    subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
        check=True,
    )
    shutil.rmtree(iconset_dir)
    print(f"Created {icns_path}")


def main():
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        src = Path("resources/wenzi_dmg_raw.png")

    out_png = Path("resources/dmg-volume.png")
    out_icns = Path("resources/dmg-volume.icns")

    img = Image.open(src)
    print(f"Source: {img.size}, mode={img.mode}")

    clean = remove_checkerboard_bg(img)
    cropped = crop_to_content(clean)
    print(f"Cropped: {cropped.size}")

    squared = make_square(cropped)
    resized = squared.resize((1024, 1024), Image.LANCZOS)

    # Flatten onto white background (no transparency)
    final = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    final = Image.alpha_composite(final, resized)
    final.save(out_png, "PNG", optimize=True)
    print(f"Saved {out_png} ({resized.size})")

    create_icns(str(out_png), str(out_icns))


if __name__ == "__main__":
    main()
