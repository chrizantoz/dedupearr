"""GitHub social preview (1280x640), README hero banner (1600x500) and app icon for Dedupearr.
Background: the app's own screenshots (uibot/screens/*.png or a folder given as argv[1]) tilted and dimmed; falls back to a drawn mosaic.
Foreground: logo mark (stacked discs with a check), wordmark, tagline, feature strip. Output: docs/social-preview.png, docs/banner.png, docs/icon.png, docs/icon-64.png.
Usage: python docs/make_banner.py [screenshot dir]"""
import os, sys, random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
SHOTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "uibot", "screens")
SKY = (56, 189, 248); AMBER = (251, 191, 36); INK = (15, 20, 25); PANEL = (20, 27, 34); LINE = (38, 48, 57); FG = (230, 233, 238); MUT = (149, 160, 173)
def font(size, weight="Bold"):
    for p in (f"/usr/share/fonts/noto/NotoSans-{weight}.ttf", f"/usr/share/fonts/truetype/noto/NotoSans-{weight}.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def mosaic(w, h):
    """Drawn stand-in for screenshots: rows of panels with verdict chips and bars, like the Files page."""
    img = Image.new("RGB", (w, h), INK); d = ImageDraw.Draw(img); y = -40; rnd = random.Random(7)
    chips = [(134, 239, 172), (253, 230, 138), (254, 202, 202), (203, 213, 225)]
    while y < h + 60:
        x = -60 + rnd.randint(0, 80); rowh = rnd.choice([46, 58, 70])
        while x < w + 60:
            pw = rnd.randint(180, 420); d.rounded_rectangle((x, y, x + pw, y + rowh), 8, fill=PANEL, outline=LINE)
            c = rnd.choice(chips); d.rounded_rectangle((x + 12, y + rowh // 2 - 7, x + 78, y + rowh // 2 + 7), 7, fill=c)
            d.rounded_rectangle((x + 90, y + rowh // 2 - 4, x + 90 + rnd.randint(50, pw - 110), y + rowh // 2 + 4), 4, fill=LINE)
            x += pw + 14
        y += rowh + 14
    return img

def background(w, h):
    def pick(kind):
        for pat in (f"files-{kind}-dark.png", f"files-{kind}.png"):
            p = os.path.join(SHOTS, pat)
            if os.path.exists(p): return [p]
        return []
    shots = pick("desktop") + pick("phone")
    if shots:
        desk = Image.open(shots[0]).convert("RGB"); desk = desk.crop((0, 0, desk.width, min(desk.height, int(desk.width * 0.9))))
        big = Image.new("RGB", (int(w * 1.6), int(h * 1.6)), INK)
        tile = desk.resize((int(big.width * 0.62), int(big.width * 0.62 * desk.height / desk.width)))
        big.paste(tile, (0, 0)); big.paste(tile, (tile.width + 30, 120)); big.paste(tile, (-200, tile.height + 30)); big.paste(tile, (tile.width - 170, tile.height + 150))
        ph = [p for p in shots if "phone" in p]
        if ph:
            pimg = Image.open(ph[0]).convert("RGB"); pimg = pimg.crop((0, 0, pimg.width, min(pimg.height, pimg.width * 2)))
            pimg = pimg.resize((int(big.width * 0.17), int(big.width * 0.17 * pimg.height / pimg.width))); big.paste(pimg, (int(big.width * 0.66), int(big.height * 0.05)))
        bg = big
    else: bg = mosaic(int(w * 1.6), int(h * 1.6))
    bg = bg.rotate(-8, resample=Image.BICUBIC, expand=False)
    cx, cy = bg.width // 2, bg.height // 2; bg = bg.crop((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)).filter(ImageFilter.GaussianBlur(1.2))
    overlay = Image.new("L", (w, h), 0); od = ImageDraw.Draw(overlay)
    for i in range(60, 0, -1): od.ellipse((w // 2 - i * w // 90, h // 2 - i * h // 70, w // 2 + i * w // 90, h // 2 + i * h // 70), fill=int(235 - i * 2.4))
    return Image.composite(Image.new("RGB", (w, h), INK), bg, overlay).convert("RGBA")

def logo(d, x, y, s):
    """Three stacked discs (the storage) in sky blue, an amber check on the lowest one (the verdict)."""
    lw = max(3, int(s * 0.075)); cx = x + s * 0.5; rx, ry = s * 0.36, s * 0.13
    for i, cy in enumerate((y + s * 0.22, y + s * 0.47, y + s * 0.72)):
        d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=SKY, width=lw)
        if i < 2: d.line((cx - rx, cy, cx - rx, cy + s * 0.25), fill=SKY, width=lw); d.line((cx + rx, cy, cx + rx, cy + s * 0.25), fill=SKY, width=lw)
    d.line((cx - s * 0.16, y + s * 0.74, cx - s * 0.03, y + s * 0.87), fill=AMBER, width=lw + 1); d.line((cx - s * 0.03, y + s * 0.87, cx + s * 0.2, y + s * 0.6), fill=AMBER, width=lw + 1)

def compose(w, h, out, tagline, strip, scale=1.0):
    img = background(w, h); d = ImageDraw.Draw(img)
    S = int(96 * scale); logo(d, w // 2 - S // 2, int(h * 0.15), S)
    f1 = font(int(118 * scale), "Black"); name = "Dedupearr"; tw = d.textlength(name, font=f1); x = (w - tw) / 2; y = int(h * 0.15) + S + int(6 * scale)
    d.text((x + 3, y + 4), name, font=f1, fill=(0, 0, 0)); d.text((x, y), "Dedupe", font=f1, fill=FG); d.text((x + d.textlength("Dedupe", font=f1), y), "arr", font=f1, fill=SKY)
    f2 = font(int(34 * scale), "Medium"); tw2 = d.textlength(tagline, font=f2); d.text(((w - tw2) / 2 + 2, y + int(150 * scale) + 2), tagline, font=f2, fill=(0, 0, 0)); d.text(((w - tw2) / 2, y + int(150 * scale)), tagline, font=f2, fill=FG)
    f3 = font(int(17 * scale), "Bold"); sp = "   ·   ".join(strip); tw3 = d.textlength(sp, font=f3) * 1.12; sx = (w - tw3) / 2; yy = y + int(210 * scale)
    parts = sp.split("   ·   ")
    for i, part in enumerate(parts):
        for ch in part: d.text((sx, yy), ch, font=f3, fill=MUT); sx += d.textlength(ch, font=f3) + 2.2 * scale
        if i < len(parts) - 1: d.text((sx + 8 * scale, yy - 2), "·", font=f3, fill=AMBER); sx += d.textlength("   ·   ", font=f3)
    img.convert("RGB").save(out, optimize=True); print("wrote", out, img.size)

def icon(size, out):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), int(size * 0.22), fill=INK); logo(d, int(size * 0.08), int(size * 0.06), int(size * 0.84))
    img.save(out, optimize=True); print("wrote", out, img.size)

STRIP = ["HARDLINKS", "SECOND COPIES", "LEFTOVERS", "ORPHANS", "EVIDENCE PER FILE", "OPEN SOURCE"]
compose(1280, 640, os.path.join(HERE, "social-preview.png"), "The truth about your storage.", STRIP)
compose(1600, 500, os.path.join(HERE, "banner.png"), "The truth about your storage.", STRIP, scale=0.85)
icon(512, os.path.join(HERE, "icon.png")); icon(64, os.path.join(HERE, "icon-64.png"))
