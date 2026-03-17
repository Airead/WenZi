"""Generate DMG installer background image.

Creates a 600x400 background with a light neutral gradient.
"""

from PIL import Image, ImageDraw


def main():
    width, height = 600, 400
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Light warm gray gradient — neutral so purple icons pop
    top = (240, 238, 245)
    bottom = (215, 210, 225)

    for y in range(height):
        t = y / height
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Quantize for small file size
    img_p = img.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    out = "resources/dmg-background.png"
    img_p.save(out, "PNG", optimize=True)

    size_kb = __import__("os").path.getsize(out) / 1024
    print(f"Saved {out} ({width}x{height}, {size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
