"""One-off generator for the static Open Graph share image.

Not a runtime dependency of the app — run manually when the branding
changes: `pip install pillow` then `python scripts/generate_og_image.py`.
Output is committed as prove_me_wrong/static/og-image.png.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
BG = "#f7f7f5"
CARD = "#ffffff"
BORDER = "#e3e2df"
TEXT = "#1a1a1a"
MUTED = "#6b6b6b"
AGREE = "#1f7a4d"
DISAGREE = "#b3391f"

FONT_DIR = Path("C:/Windows/Fonts")
OUTPUT = Path(__file__).resolve().parent.parent / "prove_me_wrong" / "static" / "og-image.png"


def font(name, size):
    return ImageFont.truetype(str(FONT_DIR / name), size)


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def centered_text(draw, cx, y, text, f, fill):
    bbox = draw.textbbox((0, 0), text, font=f)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=f, fill=fill)
    return bbox[3] - bbox[1]


def main():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    card_box = (60, 60, WIDTH - 60, HEIGHT - 60)
    rounded(draw, card_box, 24, fill=CARD, outline=BORDER, width=2)

    title_font = font("segoeuib.ttf", 72)
    subtitle_font = font("segoeui.ttf", 32)
    pill_font = font("segoeuib.ttf", 26)

    cx = WIDTH // 2
    y = 190
    y += centered_text(draw, cx, y, "Prove Me Wrong", title_font, TEXT) + 30
    centered_text(draw, cx, y, "Structured disagreement. Pick a side.", subtitle_font, MUTED)

    # AGREE / DISAGREE pills
    pill_y = 400
    pill_h = 64
    agree_text = "AGREE"
    disagree_text = "DISAGREE"
    agree_w = draw.textbbox((0, 0), agree_text, font=pill_font)[2] + 56
    disagree_w = draw.textbbox((0, 0), disagree_text, font=pill_font)[2] + 56
    gap = 24
    total_w = agree_w + disagree_w + gap
    start_x = cx - total_w / 2

    rounded(draw, (start_x, pill_y, start_x + agree_w, pill_y + pill_h), pill_h / 2, fill="#e6f4ec")
    centered_text(draw, start_x + agree_w / 2, pill_y + 15, agree_text, pill_font, AGREE)

    dx = start_x + agree_w + gap
    rounded(draw, (dx, pill_y, dx + disagree_w, pill_y + pill_h), pill_h / 2, fill="#fbeae6")
    centered_text(draw, dx + disagree_w / 2, pill_y + 15, disagree_text, pill_font, DISAGREE)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, "PNG")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
