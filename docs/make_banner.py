"""README hero banner (1600x500), GitHub social preview (1280x640) and app icon for Dedupearr.
Its own look, not Auditarr's tilted screenshots: a flat slate ground with a faint dot grid, the disc-stack mark, the wordmark, and the
app's signature element, the storage bar (library · stored twice · leftovers · free), with three verdict chips floating beside it.
Output: docs/banner.png, docs/social-preview.png, docs/icon.png, docs/icon-64.png.      Usage: python docs/make_banner.py"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SKY = (56, 189, 248); AMBER = (251, 191, 36); INK = (15, 20, 25); PANEL = (20, 27, 34); LINE = (38, 48, 57); FG = (230, 233, 238); MUT = (149, 160, 173)
BLUE = (30, 58, 138); ORANGE = (180, 83, 9); ORANGE2 = (217, 119, 6); RED = (153, 27, 27); GREEN = (134, 239, 172); GREENBG = (15, 46, 29); AMBERBG = (59, 47, 10); REDBG = (59, 29, 29); PALE = (254, 243, 199); PALER = (254, 202, 202)
def font(size, weight="Bold"):
    for p in (f"/usr/share/fonts/noto/NotoSans-{weight}.ttf", f"/usr/share/fonts/truetype/noto/NotoSans-{weight}.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def ground(w, h):
    img = Image.new("RGB", (w, h), INK); d = ImageDraw.Draw(img)
    for x in range(24, w, 36):
        for y in range(24, h, 36): d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(28, 36, 45))
    glow = Image.new("RGB", (w, h), INK); gd = ImageDraw.Draw(glow)
    for i in range(80, 0, -1): gd.ellipse((int(w * 0.18) - i * 9, int(h * 0.45) - i * 6, int(w * 0.18) + i * 9, int(h * 0.45) + i * 6), fill=(15 + (80 - i) // 5, 20 + (80 - i) // 4, 25 + (80 - i) // 2))
    return Image.blend(img, glow, 0.55)

def logo(d, x, y, s):
    """Three stacked discs (the storage) in sky blue, an amber check on the lowest one (the verdict)."""
    lw = max(3, int(s * 0.075)); cx = x + s * 0.5; rx, ry = s * 0.36, s * 0.13
    for i, cy in enumerate((y + s * 0.22, y + s * 0.47, y + s * 0.72)):
        d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=SKY, width=lw)
        if i < 2: d.line((cx - rx, cy, cx - rx, cy + s * 0.25), fill=SKY, width=lw); d.line((cx + rx, cy, cx + rx, cy + s * 0.25), fill=SKY, width=lw)
    d.line((cx - s * 0.16, y + s * 0.74, cx - s * 0.03, y + s * 0.87), fill=AMBER, width=lw + 1); d.line((cx - s * 0.03, y + s * 0.87, cx + s * 0.2, y + s * 0.6), fill=AMBER, width=lw + 1)

def chip(d, x, y, text, bg, fg, f):
    tw = d.textlength(text, font=f); d.rounded_rectangle((x, y, x + tw + 26, y + 34), 17, fill=bg); d.text((x + 13, y + 7), text, font=f, fill=fg); return tw + 26

def storage_bar(d, x, y, w, h, f, labels=True):
    segs = [("Library 11.8 TB", 0.68, BLUE, (219, 234, 254)), ("Stored twice 1.1 TB", 0.10, ORANGE, PALE), ("Leftovers 540 GB", 0.05, ORANGE2, (31, 19, 0)), ("", 0.02, RED, FG), ("Free 2.3 TB", 0.15, PANEL, MUT)]
    d.rounded_rectangle((x - 1, y - 1, x + w + 1, y + h + 1), 12, outline=LINE, width=2); cx = x
    for i, (name, frac, bg, fg) in enumerate(segs):
        sw = int(w * frac); box = (cx, y, cx + sw, y + h)
        if i == 0: d.rounded_rectangle((cx, y, cx + sw + 12, y + h), 11, fill=bg); d.rectangle((cx + sw - 2, y, cx + sw, y + h), fill=bg)
        elif i == len(segs) - 1: d.rounded_rectangle((cx - 12, y, cx + sw, y + h), 11, fill=bg); d.rectangle((cx, y, cx + 2, y + h), fill=bg)
        else: d.rectangle(box, fill=bg)
        if labels and name and d.textlength(name, font=f) < sw - 16: d.text((cx + 12, y + (h - f.size) / 2 - 2), name, font=f, fill=fg)
        cx += sw

def compose(w, h, out, scale=1.0, top=0.20, bar=0.70):
    img = ground(w, h); d = ImageDraw.Draw(img)
    S = int(150 * scale); lx, ly = int(w * 0.07), int(h * top); logo(d, lx, ly, S)
    f1 = font(int(96 * scale), "Black"); name_x = lx + S + int(28 * scale); name_y = ly - int(6 * scale)
    d.text((name_x, name_y), "Dedupe", font=f1, fill=FG); d.text((name_x + d.textlength("Dedupe", font=f1), name_y), "arr", font=f1, fill=SKY)
    f2 = font(int(32 * scale), "Medium"); d.text((name_x + int(6 * scale), name_y + int(112 * scale)), "The truth about your storage.", font=f2, fill=FG)
    f3 = font(int(17 * scale), "Regular"); d.text((name_x + int(6 * scale), name_y + int(158 * scale)), "Hardlinks, second copies, leftovers and orphans, explained per file with the evidence.", font=f3, fill=MUT)
    fb = font(int(15 * scale), "Bold"); bx, by, bw, bh = int(w * 0.07), int(h * bar), int(w * 0.60), int(40 * scale); storage_bar(d, bx, by, bw, bh, fb)
    fc = font(int(14 * scale), "Bold"); cxp = bx + bw + int(28 * scale); cyp = by - int(44 * scale)
    chip(d, cxp, cyp, "Real copy", GREENBG, GREEN, fc); chip(d, cxp, cyp + int(44 * scale), "Stored twice", AMBERBG, PALE, fc); chip(d, cxp, cyp + int(88 * scale), "Orphan", REDBG, PALER, fc)
    fs = font(int(13 * scale), "Bold"); d.text((bx, by + bh + int(14 * scale)), "ONE SCAN A NIGHT   ·   ONE VERDICT PER FILE   ·   NOTHING DELETED WITHOUT YOU   ·   OPEN SOURCE", font=fs, fill=MUT)
    img.save(out, optimize=True); print("wrote", out, img.size)

def icon(size, out):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), int(size * 0.22), fill=INK); logo(d, int(size * 0.08), int(size * 0.06), int(size * 0.84))
    img.save(out, optimize=True); print("wrote", out, img.size)

compose(1600, 500, os.path.join(HERE, "banner.png"))
compose(1280, 640, os.path.join(HERE, "social-preview.png"), scale=0.9, top=0.26, bar=0.62)
icon(512, os.path.join(HERE, "icon.png")); icon(64, os.path.join(HERE, "icon-64.png"))
