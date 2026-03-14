"""Process Gemini-generated icon into a proper macOS app icon.

Removes fake checkerboard transparency background, crops to the icon,
resizes to 1024x1024, and generates .icns file.
"""

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def remove_checkerboard_bg(img: Image.Image, threshold: int = 30) -> Image.Image:
    """Remove fake checkerboard transparency and drop shadow, keep only the icon."""
    rgba = img.convert("RGBA")
    data = np.array(rgba)

    # The checkerboard pattern uses light gray (~204) and white (~255).
    # The purple icon area is distinctly different.
    # Strategy: find pixels that are "gray-ish" (low saturation, high lightness)
    # and make them transparent.

    r, g, b, _a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]

    # Checkerboard pixels are near-white or near-light-gray with no color
    # They have very similar R, G, B values and are bright
    max_rgb = np.maximum(np.maximum(r.astype(int), g.astype(int)), b.astype(int))
    min_rgb = np.minimum(np.minimum(r.astype(int), g.astype(int)), b.astype(int))
    saturation = max_rgb - min_rgb  # color spread
    brightness = (r.astype(int) + g.astype(int) + b.astype(int)) / 3

    # Checkerboard: low saturation + high brightness
    # Also catch the shadow: low saturation + medium brightness + grayish
    is_background = (saturation < threshold) & (brightness > 150)

    # Also catch darker shadow areas (grayish, low saturation)
    is_shadow = (saturation < 20) & (brightness > 100) & (brightness <= 150)

    # Make background and shadow transparent
    data[is_background | is_shadow, 3] = 0

    return Image.fromarray(data)


def crop_to_content(img: Image.Image, padding: int = 0) -> Image.Image:
    """Crop image to non-transparent content."""
    bbox = img.getbbox()
    if bbox is None:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(img.width, right + padding)
    bottom = min(img.height, bottom + padding)
    return img.crop((left, top, right, bottom))


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

    # Required icon sizes for macOS .icns
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

    # Clean up iconset
    import shutil
    shutil.rmtree(iconset_dir)
    print(f"Created {icns_path}")


def main():
    src = Path("resources/Gemini_Generated_Image_bfmqacbfmqacbfmq.png")
    out_png = Path("resources/icon.png")
    out_icns = Path("resources/icon.icns")

    img = Image.open(src)
    print(f"Source: {img.size}, mode={img.mode}")

    # Remove fake checkerboard background
    clean = remove_checkerboard_bg(img)

    # Crop to icon content with no extra padding
    cropped = crop_to_content(clean)
    print(f"Cropped: {cropped.size}")

    # Make square and resize to 1024x1024
    squared = make_square(cropped)

    # Resize to 1024x1024 and flatten onto white background
    target_size = 1024
    resized = squared.resize((target_size, target_size), Image.LANCZOS)
    final = Image.new("RGBA", (target_size, target_size), (255, 255, 255, 255))
    final = Image.alpha_composite(final, resized)

    final.save(out_png, "PNG")
    print(f"Saved {out_png} (1024x1024)")

    # Generate .icns
    create_icns(str(out_png), str(out_icns))


if __name__ == "__main__":
    main()
