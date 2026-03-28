"""
Generate the MHM Pipeline application icon.

Design: deep indigo rounded-square, gold Hebrew letter mem (מ) centre,
        cyan RDF/LOD network nodes, subtle radial glow.

Outputs:
  assets/icon.png         1024×1024 master
  assets/icon_*.png       16/32/48/64/128/256/512 px
  assets/icon.icns        macOS bundle icon
  assets/icon.ico         Windows installer icon
  installer/windows/mhm_pipeline.ico  (copy for Inno Setup)
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── directories ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

# ── palette ───────────────────────────────────────────────────────────────────
BG_DARK   = (10,  14,  45)        # deep indigo
BG_MID    = (22,  30,  80)        # mid indigo
GOLD_CORE = (255, 200,  60)       # bright gold (centre of letter)
GOLD_EDGE = (180, 120,  20)       # darker gold (edges)
CYAN_BRIGHT = (100, 210, 255)     # node highlight
CYAN_DIM    = ( 50, 130, 180)     # edge lines
WHITE       = (255, 255, 255, 255)

SIZE = 1024   # master canvas


# ── helpers ───────────────────────────────────────────────────────────────────

def lerp_color(a: tuple[int,...], b: tuple[int,...], t: float) -> tuple[int,...]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    """Alpha mask for a rounded square."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def radial_gradient(size: int, inner: tuple, outer: tuple) -> Image.Image:
    """Radial gradient from inner colour at centre to outer at edge."""
    img = Image.new("RGB", (size, size))
    cx = cy = size / 2
    max_r = size * 0.72
    px = img.load()
    for y in range(size):
        for x in range(size):
            r = math.hypot(x - cx, y - cy)
            t = min(r / max_r, 1.0)
            px[x, y] = lerp_color(inner, outer, t)
    return img


def draw_network(draw: ImageDraw.ImageDraw, cx: float, cy: float,
                 ring_r: float, node_r: float, n_nodes: int,
                 offset_angle: float = 0.0) -> None:
    """Draw a ring of connected nodes around (cx, cy)."""
    positions: list[tuple[float, float]] = []
    for i in range(n_nodes):
        angle = offset_angle + 2 * math.pi * i / n_nodes
        nx = cx + ring_r * math.cos(angle)
        ny = cy + ring_r * math.sin(angle)
        positions.append((nx, ny))

    # edges first (behind nodes)
    for i, (x1, y1) in enumerate(positions):
        for j in range(i + 1, n_nodes):
            x2, y2 = positions[j]
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < ring_r * 1.5:
                draw.line([(x1, y1), (x2, y2)],
                          fill=(*CYAN_DIM, 120), width=max(1, int(node_r * 0.18)))

    # nodes
    for nx, ny in positions:
        r = node_r
        draw.ellipse(
            [(nx - r, ny - r), (nx + r, ny + r)],
            fill=(*CYAN_BRIGHT, 220),
        )
        # inner highlight
        ri = r * 0.45
        draw.ellipse(
            [(nx - ri, ny - ri), (nx + ri, ny + ri)],
            fill=(*WHITE[:3], 160),
        )


def draw_letter(img: Image.Image, size: int) -> None:
    """Render the Hebrew letter מ (mem) centred on the canvas."""
    font_path = "/System/Library/Fonts/ArialHB.ttc"
    font_size = int(size * 0.52)

    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        # fallback: SF Hebrew
        try:
            font = ImageFont.truetype("/System/Library/Fonts/SFHebrew.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    letter = "מ"  # mem — knowledge, water, manuscripts

    # measure
    tmp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    td  = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), letter, font=font)
    lw   = bbox[2] - bbox[0]
    lh   = bbox[3] - bbox[1]
    tx   = (size - lw) // 2 - bbox[0]
    ty   = (size - lh) // 2 - bbox[1] - int(size * 0.02)

    # glow layer (large soft gold blur)
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    gd.text((tx, ty), letter, font=font, fill=(*GOLD_CORE, 180))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.045))
    img.alpha_composite(glow)

    # mid-glow (sharper)
    mid = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    md  = ImageDraw.Draw(mid)
    md.text((tx, ty), letter, font=font, fill=(*GOLD_CORE, 220))
    mid = mid.filter(ImageFilter.GaussianBlur(radius=size * 0.015))
    img.alpha_composite(mid)

    # crisp letter on top
    sharp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd    = ImageDraw.Draw(sharp)
    sd.text((tx, ty), letter, font=font, fill=(*GOLD_CORE, 255))
    img.alpha_composite(sharp)


# ── main generation ───────────────────────────────────────────────────────────

def make_master(size: int = SIZE) -> Image.Image:
    # 1. radial background
    bg = radial_gradient(size, BG_MID, BG_DARK).convert("RGBA")

    # 2. apply rounded-square mask
    mask = rounded_rect_mask(size, radius=int(size * 0.18))
    bg.putalpha(mask)

    # 3. network nodes — outer ring
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    cx = cy = size / 2
    draw_network(d, cx, cy,
                 ring_r=size * 0.375,
                 node_r=size * 0.028,
                 n_nodes=8,
                 offset_angle=math.pi / 8)

    # 4. network nodes — inner ring (smaller)
    draw_network(d, cx, cy,
                 ring_r=size * 0.255,
                 node_r=size * 0.018,
                 n_nodes=6,
                 offset_angle=0.0)

    bg.alpha_composite(overlay)

    # 5. Hebrew letter with glow
    draw_letter(bg, size)

    # 6. thin border highlight
    border_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border_layer)
    bw = max(2, int(size * 0.006))
    bd.rounded_rectangle(
        [bw, bw, size - bw - 1, size - bw - 1],
        radius=int(size * 0.175),
        outline=(100, 140, 255, 60),
        width=bw,
    )
    bg.alpha_composite(border_layer)

    return bg


def save_png_sizes(master: Image.Image) -> list[Path]:
    sizes = [16, 32, 48, 64, 128, 256, 512, 1024]
    paths = []
    for s in sizes:
        p = ASSETS / f"icon_{s}.png"
        master.resize((s, s), Image.LANCZOS).save(p, "PNG")
        paths.append(p)
    # also save canonical icon.png at 1024
    master.save(ASSETS / "icon.png", "PNG")
    paths.append(ASSETS / "icon.png")
    print(f"  PNG sizes saved: {[s for s in sizes]}")
    return paths


def make_icns(master: Image.Image) -> Path:
    """Build a .icns file using macOS iconutil."""
    iconset = ASSETS / "icon.iconset"
    iconset.mkdir(exist_ok=True)

    specs = [
        (16,   "icon_16x16.png"),
        (32,   "icon_16x16@2x.png"),
        (32,   "icon_32x32.png"),
        (64,   "icon_32x32@2x.png"),
        (128,  "icon_128x128.png"),
        (256,  "icon_128x128@2x.png"),
        (256,  "icon_256x256.png"),
        (512,  "icon_256x256@2x.png"),
        (512,  "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for px, name in specs:
        master.resize((px, px), Image.LANCZOS).save(iconset / name, "PNG")

    out = ASSETS / "icon.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"  .icns created: {out}")
    else:
        print(f"  iconutil failed: {result.stderr.decode()}")
    return out


def make_ico(master: Image.Image) -> Path:
    """Build a Windows .ico with multiple embedded sizes."""
    ico_sizes = [16, 32, 48, 64, 128, 256]
    frames = [master.resize((s, s), Image.LANCZOS).convert("RGBA")
              for s in ico_sizes]
    out = ASSETS / "icon.ico"
    frames[0].save(
        out, format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=frames[1:],
    )
    print(f"  .ico created:  {out}")

    # copy to installer/windows/ for Inno Setup
    win_ico = ROOT / "installer" / "windows" / "mhm_pipeline.ico"
    import shutil
    shutil.copy(out, win_ico)
    print(f"  .ico copied to: {win_ico}")
    return out


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating MHM Pipeline icon...")
    master = make_master(SIZE)
    save_png_sizes(master)
    make_icns(master)
    make_ico(master)
    print("Done. All icon assets written to assets/")
