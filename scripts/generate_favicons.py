"""Generate the raster favicons from the brand mark (green | red split rounded
square). Run manually when branding changes:  python scripts/generate_favicons.py
Outputs favicon.ico (multi-size) and apple-touch-icon.png into prove_me_wrong/static/.
The SVG favicon (favicon.svg) is hand-authored, not generated here.
"""

from pathlib import Path

from PIL import Image, ImageDraw

AGREE = (18, 133, 90)
DISAGREE = (214, 58, 38)
STATIC = Path(__file__).resolve().parent.parent / "prove_me_wrong" / "static"


def render(size, radius_ratio=0.22):
    """Render the split mark at 4x then downscale for antialiased edges."""
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(s * radius_ratio)
    # Rounded-rect mask, then paint the two-colour split through it.
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    split = int(s * 0.56)
    d.rectangle([0, 0, split, s], fill=AGREE)
    d.rectangle([split, 0, s, s], fill=DISAGREE)
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def main():
    ico = render(64)
    ico.save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # apple-touch-icon: full-bleed (iOS masks its own corners), so no rounding.
    apple = render(180, radius_ratio=0.0)
    apple.save(STATIC / "apple-touch-icon.png", "PNG")
    print(f"Wrote {STATIC / 'favicon.ico'} and {STATIC / 'apple-touch-icon.png'}")


if __name__ == "__main__":
    main()
