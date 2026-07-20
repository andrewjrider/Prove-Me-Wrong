"""Dynamic Open Graph share images — one per claim, with the claim text and its
live vote split baked in, so a link preview shows *that* debate and where it
currently stands.

Rendered with Pillow using the bundled DejaVu fonts (see fonts/), so output is
identical on any host regardless of what system fonts are installed. Images are
cached to disk keyed on the vote counts, so a claim only re-renders when its
split actually changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"

W, H = 1200, 630

# Light-mode brand palette (link previews render on their own surfaces; a light
# card reads well in both Twitter/X and iMessage/Slack).
BG = (239, 238, 233)
CARD = (255, 255, 255)
BORDER = (214, 211, 202)
INK = (23, 22, 28)
MUTED = (109, 107, 118)
AGREE = (18, 133, 90)
DISAGREE = (214, 58, 38)
TRACK = (231, 229, 223)
WHITE = (255, 255, 255)

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(FONT_DIR / name), size)
    return _font_cache[key]


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _text_h(draw, font):
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return bbox[3] - bbox[1], bbox[1]


def _centered(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _rounded_split_bar(draw, box, radius, agree_pct, disagree_pct, has_votes):
    """Rounded outer corners, straight seam in the middle — the site's bar look."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    draw.rounded_rectangle(box, radius=radius, fill=TRACK)
    if not has_votes:
        return

    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    aw = round(bw * agree_pct / 100)
    if aw > 0:
        ld.rectangle([0, 0, aw, bh], fill=AGREE)
    if disagree_pct > 0:
        ld.rectangle([aw, 0, bw, bh], fill=DISAGREE)

    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=radius, fill=255)
    layer.putalpha(mask)
    # The caller pastes this rounded-masked layer onto the base image.
    return layer


def render_og_png(claim_text, agree_pct, disagree_pct, agree_n, disagree_n, total) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    m = 52
    draw.rounded_rectangle([m, m, W - m, H - m], radius=30, fill=CARD, outline=BORDER, width=2)

    pad = m + 46
    inner_w = W - 2 * pad

    # Brand kicker with a little two-colour mark (agree green | disagree red).
    my = m + 46
    draw.rounded_rectangle([pad, my, pad + 30, my + 30], radius=8, fill=AGREE)
    draw.rectangle([pad + 17, my + 1, pad + 29, my + 29], fill=DISAGREE)
    draw.text((pad + 44, my + 3), "PROVE ME WRONG", font=_font("DejaVuSans-Bold.ttf", 26), fill=MUTED)

    # Bar is anchored to the bottom of the card; the claim fills the space above.
    bar_h = 56
    bar_y0 = H - m - 168
    bar_y1 = bar_y0 + bar_h
    bar_box = (pad, bar_y0, W - pad, bar_y1)
    has_votes = total > 0

    # Claim statement — serif, adaptively sized to fill the room above the verdict
    # line without ever colliding with it (long claims shrink and wrap).
    region_top = m + 108
    region_bottom = bar_y0 - 58
    avail_h = region_bottom - region_top
    lines = cf = line_h = None
    for size in (64, 56, 48, 42, 36, 32):
        f = _font("DejaVuSerif-Bold.ttf", size)
        lh = int(size * 1.2)
        max_lines = max(1, avail_h // lh)
        wrapped = _wrap(draw, claim_text, f, inner_w)
        if len(wrapped) <= max_lines:
            lines, cf, line_h = wrapped, f, lh
            break
    if lines is None:
        cf = _font("DejaVuSerif-Bold.ttf", 32)
        line_h = int(32 * 1.2)
        max_lines = max(1, avail_h // line_h)
        lines = _wrap(draw, claim_text, cf, inner_w)[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    y = region_top
    for ln in lines:
        draw.text((pad, y), ln, font=cf, fill=INK)
        y += line_h

    # Verdict line above the bar.
    vf = _font("DejaVuSans-Bold.ttf", 30)
    if not has_votes:
        verdict, vcolor = "No votes yet — you decide.", MUTED
    elif agree_pct == disagree_pct:
        verdict, vcolor = f"Dead heat — {agree_pct}% / {disagree_pct}%", MUTED
    elif agree_pct > disagree_pct:
        verdict, vcolor = f"Agree leads · {agree_pct}% to {disagree_pct}%", AGREE
    else:
        verdict, vcolor = f"Disagree leads · {disagree_pct}% to {agree_pct}%", DISAGREE
    draw.text((pad, bar_y0 - 46), verdict, font=vf, fill=vcolor)

    # The split bar (rounded outer, straight seam).
    layer = _rounded_split_bar(draw, bar_box, bar_h // 2, agree_pct, disagree_pct, has_votes)
    if layer is not None:
        img.paste(layer, (bar_box[0], bar_box[1]), layer)
        draw = ImageDraw.Draw(img)  # refresh after paste

    # Percentages inside wide-enough segments.
    if has_votes:
        pf = _font("DejaVuSans-Bold.ttf", 26)
        th, toff = _text_h(draw, pf)
        ty = bar_y0 + (bar_h - th) / 2 - toff
        bw = W - pad - pad
        aw = round(bw * agree_pct / 100)
        if agree_pct >= 14:
            _centered(draw, pad + aw / 2, ty, f"{agree_pct}%", pf, WHITE)
        if disagree_pct >= 14:
            _centered(draw, pad + aw + (bw - aw) / 2, ty, f"{disagree_pct}%", pf, WHITE)

    # Counts below the bar.
    lf = _font("DejaVuSans-Bold.ttf", 24)
    below_y = bar_y1 + 16
    draw.text((pad, below_y), f"Agree · {agree_n}", font=lf, fill=AGREE)
    right = f"Disagree · {disagree_n}"
    draw.text((W - pad - draw.textlength(right, font=lf), below_y), right, font=lf, fill=DISAGREE)

    from io import BytesIO

    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def og_png_for_claim(cache_dir, claim_id, claim_text, agree_pct, disagree_pct, agree_n, disagree_n) -> bytes:
    """Return the claim's OG PNG, rendering + caching to disk on a miss. The cache
    key includes the vote counts, so any vote change produces a fresh image; stale
    variants for the same claim are pruned to keep the cache bounded."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_dir / f"claim_{claim_id}_{agree_n}x{disagree_n}.png"
    if key.exists():
        return key.read_bytes()

    total = agree_n + disagree_n
    data = render_og_png(claim_text, agree_pct, disagree_pct, agree_n, disagree_n, total)

    for old in cache_dir.glob(f"claim_{claim_id}_*.png"):
        if old != key:
            try:
                old.unlink()
            except OSError:
                pass

    tmp = key.with_suffix(".png.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, key)
    return data
