"""
MM-VideoAssembler — Money Matrix Video Assembly Engine v2
Generates real charts, vector illustrations, gradient stock-photo placeholders,
text overlays, and audio waveform visualizations into a branded MP4.
"""

import io, os, re, struct, subprocess, textwrap, shutil, sys, math, random
from pathlib import Path
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageFont, ImageDraw as PILDraw

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

NAVY   = (10, 22, 40)
TEAL   = (0, 201, 167)
GOLD   = (255, 209, 102)
WHITE  = (255, 255, 255)
LGRAY  = (200, 200, 200)
DMID   = (120, 130, 150)

W, H = 1920, 1080
FFMPEG = r"C:\Users\user\Desktop\AGENTIC WORK\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\bin\ffmpeg.exe"


def _font(font_size=32):
    try: return ImageFont.truetype("arial.ttf", font_size)
    except: return ImageFont.load_default()


def _dtext(draw, text, x, y, fill=WHITE, font_size=32, anchor="mm"):
    fnt = _font(font_size)
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    px = {"l": x, "r": x-tw}.get(anchor[0] if anchor else "m", x-tw//2)
    py = {"t": y, "b": y-th}.get(anchor[-1] if anchor else "m", y-th//2)
    draw.text((px, py), text, fill=fill, font=fnt)


def _dmultiline(draw, text, x, y, fill=WHITE, font_size=36, spacing=1.5):
    lines = text.split("\n")
    start_y = y - (len(lines) * font_size * spacing) / 2
    for i, line in enumerate(lines):
        _dtext(draw, line, x, start_y + i*font_size*spacing, fill, font_size)


def _slide(bg=NAVY):
    return Image.new("RGB", (W, H), bg)


def _gradient(bg_from=NAVY, bg_to=(15, 35, 60)):
    base = Image.new("RGB", (W, H))
    for y in range(H):
        t = y / H
        r = int(bg_from[0]*(1-t) + bg_to[0]*t)
        g = int(bg_from[1]*(1-t) + bg_to[1]*t)
        b = int(bg_from[2]*(1-t) + bg_to[2]*t)
        PILDraw.Draw(base).line([(0,y),(W,y)], fill=(r,g,b))
    return base


def _rounded_rect(draw, xy, r, fill=None, outline=None, width=1):
    x1,y1,x2,y2 = xy
    draw.rounded_rectangle(xy, r, fill=fill, outline=outline, width=width)


def _star(draw, cx, cy, points, outer_r, inner_r, fill, rotation=0):
    pts = []
    for i in range(points*2):
        angle = rotation + math.pi*i/points
        rad = outer_r if i%2==0 else inner_r
        pts.append((cx + rad*math.cos(angle), cy + rad*math.sin(angle)))
    draw.polygon(pts, fill=fill)


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE A — STOCK PHOTO PLACEHOLDER (geometric gradient backgrounds)
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_a(query, dur):
    img = _gradient()
    d = PILDraw.Draw(img)

    seed = hash(query) % 1000
    rng = random.Random(seed)

    for _ in range(rng.randint(3, 6)):
        cx = rng.randint(100, W-100)
        cy = rng.randint(100, H-100)
        ra = rng.randint(80, 300)
        alpha = PILDraw.Image.new("L", (W, H), 0)
        PILDraw.Draw(alpha).ellipse([cx-ra, cy-ra, cx+ra, cy+ra], fill=80)
        petal = Image.new("RGBA", (W, H), (0,0,0,0))
        col = rng.choice([TEAL, GOLD, (20,50,90), (255,255,255)])
        PILDraw.Draw(petal).ellipse([cx-ra, cy-ra, cx+ra, cy+ra], fill=col+(8,))
        img = Image.alpha_composite(img.convert("RGBA"), petal).convert("RGB")

    d = PILDraw.Draw(img)

    for _ in range(rng.randint(8, 15)):
        x = rng.randint(0, W)
        y = rng.randint(0, H)
        sz = rng.randint(2, 6)
        d.ellipse([x, y, x+sz, y+sz], fill=GOLD+(40,) if rng.random()<0.3 else TEAL+(20,))

    bar_h = rng.randint(3, 8)
    for i in range(rng.randint(3, 8)):
        bx = rng.randint(50, W-50)
        bw = rng.randint(80, 200)
        by = rng.randint(H//2, H-60)
        bh = rng.randint(10, 80)
        col = rng.choice([TEAL, GOLD, (20,50,90)])
        d.rectangle([bx, by-bh, bx+bw, by], fill=col+(12,))

    _rounded_rect(d, [60, H-140, W-60, H-60], 12, fill=(0,0,0,80))
    _dtext(d, query, W//2, H-100, WHITE, 26)
    _dtext(d, "STOCK VIDEO", W-120, 50, TEAL, 18, "rm")
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE B — REAL CHARTS (matplotlib)
# ═══════════════════════════════════════════════════════════════════════════════

def _fig_to_img(fig):
    fig.patch.set_facecolor((10/255, 22/255, 40/255))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    img = Image.open(buf).convert("RGB")
    plt.close(fig)
    w_scale = W / img.width
    h_scale = H / img.height
    scale = min(w_scale, h_scale) * 0.92
    nw = int(img.width * scale)
    nh = int(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = _slide()
    xo = (W - nw) // 2
    yo = (H - nh) // 2
    canvas.paste(img, (xo, yo))
    return canvas


def _nc(c):
    return (c[0]/255, c[1]/255, c[2]/255)

def _gen_b_line(desc, dur):
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(_nc(NAVY))
    ax.set_facecolor((0.06, 0.12, 0.22))

    years = np.arange(0, 31)
    vals = np.cumsum(np.random.randn(31) * 0.5 + 2.0) + 10
    vals = np.maximum(vals, 0)

    ax.plot(years, vals, color=_nc(TEAL), linewidth=3, zorder=3)
    ax.fill_between(years, vals, 0, color=_nc(TEAL), alpha=0.15)
    ax.scatter(years[::5], vals[::5], color=_nc(GOLD), s=40, zorder=4)

    ax.set_xlabel("Years", color="white", fontsize=14)
    ax.set_ylabel("Value ($)", color="white", fontsize=14)
    ax.tick_params(colors="white", labelsize=11)
    for spine in ax.spines.values(): spine.set_color((0.24, 0.27, 0.35))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.set_xlim(0, 30)
    return _fig_to_img(fig)


def _gen_b_bar(desc, dur):
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(_nc(NAVY))
    ax.set_facecolor((0.06, 0.12, 0.22))

    labels = ["Category A", "Category B", "Category C", "Category D"]
    vals_a = [85, 45, 62, 30]
    bars = ax.bar(labels, vals_a, color=[_nc(TEAL), _nc((30,60,100)),
                                          _nc(TEAL), _nc((30,60,100))],
                  width=0.6, edgecolor="none")
    for bar, v in zip(bars, vals_a):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1, f"{v}%",
                ha="center", color="white", fontsize=13, fontweight="bold")

    ax.tick_params(colors="white", labelsize=12)
    for spine in ax.spines.values(): spine.set_color((0.24, 0.27, 0.35))
    ax.set_ylim(0, 100)
    ax.set_ylabel("%", color="white", fontsize=14)
    return _fig_to_img(fig)


def _gen_b_compare(desc, dur):
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(_nc(NAVY))
    ax.set_facecolor((0.06, 0.12, 0.22))

    years = np.arange(0, 31)
    line_a = np.cumsum(np.random.randn(31)*0.4 + 0.8) + 10
    line_b = np.cumsum(np.random.randn(31)*0.3 + 0.4) + 10
    line_a = np.maximum(line_a, 0)*1000
    line_b = np.maximum(line_b, 0)*1000

    ax.plot(years, line_a, color=_nc(TEAL), linewidth=3, label="Option A")
    ax.plot(years, line_b, color=(0.78, 0.78, 0.82), linewidth=2, linestyle="--", label="Option B", alpha=0.7)
    ax.fill_between(years, line_a, line_b, color=_nc(TEAL), alpha=0.08)
    ax.legend(facecolor=_nc(NAVY), edgecolor=(0.24,0.27,0.35), labelcolor="white", fontsize=13)

    ax.tick_params(colors="white", labelsize=11)
    for spine in ax.spines.values(): spine.set_color((0.24, 0.27, 0.35))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    return _fig_to_img(fig)


def _gen_b_pie(desc, dur):
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor(_nc(NAVY))

    sizes = [60, 30, 10]
    labels = ["US Stocks", "International", "Bonds"]
    colors = [_nc(TEAL), _nc(GOLD), _nc((60,80,110))]
    wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct="%1.0f%%",
                                       colors=colors, startangle=90,
                                       textprops={"color": "white", "fontsize": 14})
    for at in autotexts: at.set_fontweight("bold")
    return _fig_to_img(fig)


def _gen_b(desc, dur):
    dl = desc.lower().replace("-", " ").replace("_", " ")
    if any(w in dl for w in ["pie", "allocation", "split"]): return _gen_b_pie(desc, dur)
    if any(w in dl for w in ["comparison", "side by side", "two line", "vs ", "dca", "lump sum"]):
        return _gen_b_compare(desc, dur)
    if any(w in dl for w in ["line", "growth", "curve", "rising", "v shaped", "recovery"]):
        return _gen_b_line(desc, dur)
    return _gen_b_bar(desc, dur)


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE C — VECTOR ILLUSTRATIONS (Pillow drawings)
# ═══════════════════════════════════════════════════════════════════════════════

def _ill_tree(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    trunk_x = W//2
    d.rectangle([trunk_x-12, H-250, trunk_x+12, H-50], fill=(101, 67, 33))
    for level in range(3):
        ry = H-300 + level*150
        rx = 200 - level*40
        col = TEAL if level%2==0 else (0, 150, 120)
        d.ellipse([trunk_x-rx, ry-rx//2, trunk_x+rx, ry+rx//2], fill=col)
    for _ in range(20):
        lx = trunk_x + random.randint(-180, 180)
        ly = H-350 + random.randint(0, 200)
        d.ellipse([lx-4, ly-4, lx+4, ly+4], fill=GOLD)
    _dtext(d, "Compound Growth", W//2, 60, GOLD, 36)
    return img


def _ill_coin(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    for _ in range(40):
        cx = random.randint(50, W-50)
        cy = random.randint(50, H-100)
        r = random.randint(8, 18)
        d.ellipse([cx-r, cy-r//2, cx+r, cy+r//2], fill=GOLD+(180,))
        d.ellipse([cx-r+2, cy-r//2+2, cx+r-2, cy+r//2-2], fill=GOLD+(220,), outline=GOLD)
    for _ in range(30):
        cx = random.randint(50, W-50)
        cy = random.randint(0, H-120)
        r = random.randint(3, 8)
        d.ellipse([cx-r, cy-r//2, cx+r, cy+r//2], fill=GOLD+(100,))
    hands_y = H-150
    for side in [-1, 1]:
        hx = W//2 + side*120
        d.ellipse([hx-25, hands_y-20, hx+25, hands_y+30], fill=(200,180,160))
        d.rectangle([hx-8, hands_y+10, hx+8, hands_y+60], fill=(200,180,160))
        for fi in range(3):
            fx = hx + side*(20+fi*12)
            d.ellipse([fx-6, hands_y+5, fx+6, hands_y+18], fill=(200,180,160))
    _dtext(d, "Wealth Accumulation", W//2, 70, GOLD, 36)
    return img


def _ill_lightbulb(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx, cy = W//2, H//2-60
    d.ellipse([cx-70, cy-70, cx+70, cy+80], fill=(255, 230, 150))
    d.ellipse([cx-55, cy-55, cx+55, cy+65], fill=(255, 245, 200))
    d.rectangle([cx-12, cy+75, cx+12, cy+95], fill=(200,180,160))
    d.polygon([(cx-20, cy+90), (cx+20, cy+90), (cx+15, cy+110), (cx-15, cy+110)], fill=(200,180,160))
    for i in range(8):
        angle = math.radians(i*45)
        r = 100 + random.randint(10, 30)
        lx = cx + r*math.cos(angle)
        ly = cy + r*math.sin(angle)
        lw = random.randint(2, 4)
        d.line([(cx+60*math.cos(angle), cy+60*math.sin(angle)), (lx, ly)],
               fill=GOLD+(180,), width=lw)
        d.ellipse([lx-4, ly-4, lx+4, ly+4], fill=GOLD)
    _dtext(d, "Insight", W//2, H-120, GOLD, 40)
    return img


def _ill_calendar(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx, cy = W//2, H//2
    _rounded_rect(d, [cx-160, cy-130, cx+160, cy+130], 16, fill=(20, 40, 70))
    d.rectangle([cx-160, cy-130, cx+160, cy-90], fill=TEAL)
    for i in range(5):
        x = cx-140 + i*70
        _rounded_rect(d, [x+2, cy-70, x+66, cy+110], 6, fill=(25, 50, 85))
        num = random.choice(["2000", "2008", "2020", "2024", "2026"])
        _dtext(d, num, x+34, cy, TEAL, 20)
        _dtext(d, str(random.randint(1,31)), x+34, cy+40, WHITE, 32)
    d.ellipse([cx-20, cy-145, cx+20, cy-120], fill=WHITE+(180,))
    d.ellipse([cx-4, cy-135, cx+4, cy-127], fill=TEAL)
    _dtext(d, "DECADES", W//2, H-100, TEAL, 28)
    return img


def _ill_person(desc, dur, happy=True):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx = W//2
    head_y = H//2-80
    d.ellipse([cx-45, head_y-45, cx+45, head_y+45], fill=(220, 200, 180))
    eye_y = head_y-8
    for side in [-1, 1]:
        d.ellipse([cx+side*18-5, eye_y-4, cx+side*18+5, eye_y+4], fill=NAVY)
    mouth_y = head_y+22
    if happy:
        d.arc([cx-20, mouth_y-5, cx+20, mouth_y+20], 0, 180, fill=NAVY, width=3)
    body_top = head_y+50
    d.rectangle([cx-50, body_top, cx+50, body_top+130], fill=TEAL+(180,))
    d.rectangle([cx-15, body_top+60, cx+15, body_top+130], fill=NAVY+(60,))
    arm_len = 70
    for side in [-1, 1]:
        d.line([(cx+side*50, body_top+30), (cx+side*90, body_top+80)],
               fill=(220,200,180), width=12)
    leg_len = 80
    for side in [-1, 1]:
        d.line([(cx+side*20, body_top+130), (cx+side*40, body_top+130+leg_len)],
               fill=(60,60,80), width=14)
    return img


def _ill_person_happy(desc, dur):
    return _ill_person(desc, dur, happy=True)

def _ill_person_stressed(desc, dur):
    img = _ill_person(desc, dur, happy=False)
    d = PILDraw.Draw(img)
    cx = W//2
    d.arc([cx-25, H//2-65, cx-10, H//2-50], 0, 180, fill=(200,50,50), width=2)
    d.arc([cx+10, H//2-65, cx+25, H//2-50], 0, 180, fill=(200,50,50), width=2)
    return img


def _ill_split(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    mid = W//2
    d.line([(mid, 80), (mid, H-80)], fill=WHITE+(40,), width=2)
    for side, col, label in [(-1, TEAL, "WINNER"), (1, (100,60,60), "LOSER")]:
        r = 80
        d.ellipse([W//2+side*W//4-r, H//2-50-r, W//2+side*W//4+r, H//2-50+r], fill=col+(80,))
        _dtext(d, label, W//2+side*W//4, H//2+80, col, 30)
    _dtext(d, "The Farmer vs The Gambler", W//2, 60, GOLD, 34)
    return img


def _ill_gear(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx, cy = W//2, H//2
    for i in range(3):
        gx = cx + (i-1)*120
        sz = 50 + i*15
        for t in range(12):
            angle = math.radians(t*30)
            inner_r, outer_r = sz-15, sz
            p1 = (gx+inner_r*math.cos(angle), cy+inner_r*math.sin(angle))
            p2 = (gx+outer_r*math.cos(angle), cy+outer_r*math.sin(angle))
            p3 = (gx+outer_r*math.cos(angle+0.15), cy+outer_r*math.sin(angle+0.15))
            p4 = (gx+inner_r*math.cos(angle+0.15), cy+inner_r*math.sin(angle+0.15))
            d.polygon([p1, p2, p3, p4], fill=GOLD if i==1 else TEAL)
        d.ellipse([gx-20, cy-20, gx+20, cy+20], fill=NAVY)
    arrows = [(100, H-150, 200, "In"), (W-100, H-150, -200, "Out")]
    for ax, ay, al, label in arrows:
        d.line([(ax, ay), (ax+al, ay)], fill=TEAL, width=4)
        d.polygon([(ax+al, ay), (ax+al-20, ay-8), (ax+al-20, ay+8)], fill=TEAL)
        _dtext(d, label, ax+al//2, ay-40, WHITE, 22)
    _dtext(d, "Compound Engine", W//2, 70, GOLD, 34)
    return img


def _ill_world(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx, cy = W//2, H//2
    d.ellipse([cx-300, cy-180, cx+300, cy+180], outline=TEAL, width=3)
    for lon in range(-150, 180, 60):
        angle = math.radians(lon)
        d.arc([cx-300, cy-180, cx+300, cy+180], lon-10, lon+10, fill=TEAL+(60,), width=1)
    d.ellipse([cx-250, cy-120, cx-50, cy+60], fill=TEAL+(60,))
    d.ellipse([cx-30, cy-80, cx+180, cy+50], fill=GOLD+(60,))
    _dtext(d, "60% US", cx-150, cy+100, TEAL, 24)
    _dtext(d, "40% International", cx+90, cy+100, GOLD, 24)
    _dtext(d, "Global Diversification", W//2, 60, WHITE, 34)
    return img


def _ill_seesaw(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    cx, cy = W//2, H//2+50
    d.line([(cx-350, cy), (cx+350, cy)], fill=WHITE+(60,), width=4)
    d.polygon([(cx-15, cy), (cx+15, cy), (cx, cy+60)], fill=WHITE+(80,))
    years = np.arange(0, 10)
    us = np.sin(years*0.5)*15 + 5
    inter = np.sin(years*0.5+math.pi)*15 + 5
    us_norm = cy - us*5 - 30
    inter_norm = cy - inter*5 - 30
    for i in range(9):
        d.line([(cx-300+i*66, us_norm[i]), (cx-300+(i+1)*66, us_norm[i+1])], fill=TEAL, width=3)
        d.line([(cx-300+i*66, inter_norm[i]), (cx-300+(i+1)*66, inter_norm[i+1])], fill=GOLD, width=3)
    _dtext(d, "US", cx-300, int(us_norm[0])-30, TEAL, 22, "mm")
    _dtext(d, "International", cx+300, int(inter_norm[-1])-30, GOLD, 22, "mm")
    _dtext(d, "Inverse Correlation", W//2, 60, WHITE, 32)
    return img


def _ill_handshake(desc, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    for side, arm_x in [(-1, W//2-120), (1, W//2+120)]:
        arm_c = (220, 200, 180)
        d.line([(arm_x, H//2), (W//2+side*40, H//2-10)], fill=arm_c, width=18)
    d.ellipse([W//2-30, H//2-25, W//2-5, H//2-5], fill=(230,210,190))
    d.ellipse([W//2+5, H//2-25, W//2+30, H//2-5], fill=(220,200,180))
    _dtext(d, "Future Self Agreement", W//2, H-100, GOLD, 32)
    return img


_ILLUSTRATORS = [
    ("tree", _ill_tree), ("seed", _ill_tree), ("growing", _ill_tree),
    ("coin", _ill_coin), ("money", _ill_coin), ("gold", _ill_coin),
    ("light bulb", _ill_lightbulb), ("bulb", _ill_lightbulb), ("insight", _ill_lightbulb),
    ("calendar", _ill_calendar), ("decade", _ill_calendar), ("flipping years", _ill_calendar),
    ("gear", _ill_gear), ("engine", _ill_gear), ("compound growth engine", _ill_gear),
    ("world map", _ill_world), ("globe", _ill_world), ("diversification", _ill_world),
    ("split screen", _ill_split), ("side-by-side", _ill_split), ("vs ", _ill_split),
    ("seesaw", _ill_seesaw), ("inverse", _ill_seesaw),
    ("handshake", _ill_handshake), ("shaking hands", _ill_handshake), ("agreement", _ill_handshake),
    ("relaxed", _ill_person_happy), ("confident", _ill_person_happy), ("happy", _ill_person_happy),
    ("calm", _ill_person_happy), ("sleeping", _ill_person_happy),
    ("stressed", _ill_person_stressed), ("worried", _ill_person_stressed), ("anxious", _ill_person_stressed),
    ("panic", _ill_person_stressed),
]


def _gen_c(desc, dur):
    dl = desc.lower()
    for kw, func in _ILLUSTRATORS:
        if kw in dl:
            return func(desc, dur)
    return _ill_gear(desc, dur)


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE D — TEXT OVERLAY (existing, enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

def gen_d(text, dur):
    img = _gradient()
    d = PILDraw.Draw(img)
    lines = [l.strip() for l in text.split("//")]
    d.rectangle([60, 80, 70, H-80], fill=TEAL)
    ys = H//2 - (len(lines)*60)//2
    for i, line in enumerate(lines):
        dy = ys + i*60
        d.ellipse([100, dy-8, 116, dy+8], fill=GOLD if i==0 else TEAL)
        fsize = 38 if len(line) < 25 else 30
        _dtext(d, line, 150, dy, WHITE, fsize, "lm")
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# WAVEFORM VISUALIZER
# ═══════════════════════════════════════════════════════════════════════════════

def _read_wav_samples(path, max_samples=2000):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:4] != b"RIFF": return None
        pos = 12
        bits, channels, sr = 16, 1, 24000
        data = b""
        while pos < len(raw) - 8:
            ck_id = raw[pos:pos+4]
            ck_sz = struct.unpack("<I", raw[pos+4:pos+8])[0]
            if ck_id == b"fmt ":
                audio_fmt = struct.unpack("<H", raw[pos+8:pos+10])[0]
                channels = struct.unpack("<H", raw[pos+10:pos+12])[0]
                sr = struct.unpack("<I", raw[pos+12:pos+16])[0]
                bits = struct.unpack("<H", raw[pos+22:pos+24])[0]
            elif ck_id == b"data":
                data = raw[pos+8:pos+8+ck_sz]
            pos += 8 + ck_sz
            if ck_id in (b"data", b"fact") and ck_sz % 2: pos += 1
        if not data: return None
        if bits == 16:
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        elif bits == 8:
            samples = np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128
        else: return None
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        block = max(1, len(samples) // max_samples)
        truncated = samples[:len(samples)-len(samples)%block]
        peaks = np.max(np.abs(truncated.reshape(-1, block)), axis=1)
        mx = np.max(peaks) if np.max(peaks) > 0 else 1
        return peaks[:max_samples] / mx
    except Exception as e:
        print(f"  Waveform read warning: {e}")
        return None


def _render_waveform_overlay(img, waveform, alpha=25):
    if waveform is None or len(waveform) < 10: return img
    w, h = img.width, 60
    overlay = Image.new("RGBA", (img.width, img.height), (0,0,0,0))
    d = PILDraw.Draw(overlay)
    bar_w = max(2, w // len(waveform))
    for i, amp in enumerate(waveform):
        bar_h = max(1, int(amp * h * 0.45))
        bx = i * bar_w
        by = img.height - bar_h
        col = TEAL if amp > 0.45 else GOLD
        d.rectangle([bx, by, bx+bar_w-1, img.height], fill=col+(alpha,))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# GENERATOR DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

GENERATORS = {
    "A": _gen_a,
    "B": _gen_b,
    "C": _gen_c,
    "D": gen_d,
}


# ═══════════════════════════════════════════════════════════════════════════════
# VISUAL PARSING (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_visuals(path):
    text = Path(path).read_text(encoding="utf-8")
    br = re.compile(r'^\[MM-([A-Z0-9]+-[A-Z0-9]+(?:-[A-Z0-9]+)?)\]\s*$', re.MULTILINE)
    parts = br.split(text)
    ids, contents = parts[1::2] if len(parts)>2 else [], parts[2::2] if len(parts)>2 else []
    entries = []
    for vid, content in zip(ids, contents):
        pm = re.match(r'([A-Z]+\d+)', vid)
        seg_id = pm.group(1) if pm else vid
        dur, vtype = 5.0, "A"
        query = ttext = ""
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("TYPE:"): vtype = s.split(":",1)[1].strip()
            elif s.startswith("DURATION:"):
                try: dur = float(s.split(":",1)[1].strip().lower().replace("s",""))
                except: pass
            elif s.startswith("QUERY:"): query = s.split(":",1)[1].strip().strip("\"'")
            elif s.startswith("TEXT:"): ttext = s.split(":",1)[1].strip().strip("\"'")
            elif s.startswith("CHART:") or s.startswith("ILLUSTRATION:"):
                query = s.split(":",1)[1].strip().strip("\"'")
        entries.append({"seg_id": seg_id, "type": vtype, "query": query, "text": ttext, "dur": dur})
    return entries


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════

def assemble_video(project_dir, output_path=None, music_path=None):
    proj = Path(project_dir)
    if output_path is None:
        output_path = str(proj / f"{proj.name}_FINAL.mp4")

    visuals_path = proj / "03_VISUALS.txt"
    tts_dir = proj / "tts_segments" / "MM-2026-001"
    work = proj / "_work"
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)

    print(f"[MM-VideoAssembler] {proj.name}")

    entries = parse_visuals(str(visuals_path))
    groups = OrderedDict()
    for e in entries:
        groups.setdefault(e["seg_id"], []).append(e)
    seg_keys = sorted(groups.keys(),
                      key=lambda x: int(re.search(r'\d+', x).group() or 0) if re.search(r'\d+', x) else 0)

    concat_lines = []
    audio_lines = []
    idx = 0

    for seg_id in seg_keys:
        visuals = groups[seg_id]

        for v in visuals:
            gen = GENERATORS.get(v["type"], _gen_a)
            img = gen(v["query"] if v["type"] in "ABC" else v["text"], v["dur"])
            fname = f"v{idx:04d}.png"
            img.save(work / fname)
            concat_lines.append(f"file '{fname}'")
            concat_lines.append(f"duration {v['dur']}")
            idx += 1

        ap = tts_dir / f"{seg_id}.wav"
        if ap.exists():
            audio_lines.append(f"file '{ap.resolve()}'")

        print(f"  {seg_id}: {len(visuals)}v")

    img_concat = work / "img_concat.txt"
    img_concat.write_text("\n".join(concat_lines), encoding="utf-8")

    audio_concat = work / "audio_concat.txt"
    audio_concat.write_text("\n".join(audio_lines), encoding="utf-8")
    mixed_audio = work / "mixed.wav"

    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                    "-i", str(audio_concat), "-c", "copy", str(mixed_audio)],
                   check=True, capture_output=True, timeout=120)

    waveform = _read_wav_samples(str(mixed_audio))

    if waveform is not None:
        print(f"  Adding waveform overlay ({len(waveform)} samples)")
        for fname in sorted(work.iterdir()):
            if fname.suffix == ".png":
                img = Image.open(fname)
                img = _render_waveform_overlay(img, waveform)
                img.save(fname)

    cmd = [FFMPEG, "-y",
           "-f", "concat", "-safe", "0",
           "-i", str(img_concat),
           "-i", str(mixed_audio)]

    if music_path and os.path.exists(music_path):
        cmd.extend(["-stream_loop", "-1", "-i", music_path])
        cmd.extend(["-filter_complex",
                    "[2:a]volume=0.07[music];[1:a][music]amix=inputs=2:duration=first:weights=1 0.15[aout]",
                    "-map", "0:v:0", "-map", "[aout]"])
    else:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])

    cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-r", "30",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-shortest",
                str(output_path)])

    print(f"  Rendering...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print(f"  ffmpeg error: {r.stderr[-500:]}")
        return False

    probe = subprocess.run([FFMPEG, "-i", str(mixed_audio), "-f", "null", "-"],
                          capture_output=True, text=True)
    dm = re.search(r"time=(\d+:\d+:\d+\.\d+)", probe.stderr)
    dur_s = dm.group(1) if dm else "0:00:00"
    h, m, s = dur_s.split(":")
    final_dur = int(h)*3600 + int(m)*60 + float(s)

    shutil.rmtree(work)
    sz = os.path.getsize(output_path) / 1024 / 1024

    print(f"\n[MM-VideoAssembler] Done -> {output_path}")
    print(f"  Duration: {final_dur:.1f}s ({final_dur/60:.1f} min)")
    print(f"  Size:     {sz:.1f} MB")
    return True


if __name__ == "__main__":
    music = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--music" and i+1 < len(args):
            music = args.pop(i+1)
            args.pop(i)
            break
    project = args[0] if args else r"C:\Users\user\Desktop\milo\command\milo\artisan\mm_pipeline\INDEX_FUNDS"
    out = args[1] if len(args) > 1 else None
    assemble_video(project, out, music)
