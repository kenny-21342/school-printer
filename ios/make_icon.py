#!/usr/bin/env python3
"""
Generate the SchoolPrinter iOS app icon.
Outputs a 1024x1024 master PNG and all required sizes for an iOS app.
Requires Pillow: pip install Pillow
"""
from __future__ import annotations
import math
from PIL import Image, ImageDraw, ImageFont

W = H = 1024

# --- palette ---
BG_TOP    = ( 56,  92, 217)   # indigo-blue (school / trust)
BG_BOT    = ( 26,  46, 140)   # darker blue
PAPER     = (250, 250, 255)
PAPER_SHADOW = (217, 222, 242)
INK       = ( 31,  41,  76)
ACCENT    = ( 51, 199, 140)   # green for "go"
ACCENT_DK = ( 26, 140,  97)
WHITE     = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vertical_gradient(draw, w, h, top, bot):
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=lerp(top, bot, t))


def rounded_rect(draw, xy, radius, **kw):
    draw.rounded_rectangle(xy, radius=radius, **kw)


def draw_icon() -> Image.Image:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img, "RGBA")

    # --- background gradient ---
    vertical_gradient(d, W, H, BG_TOP, BG_BOT)

    # subtle radial highlight top-center
    for r in range(700, 0, -8):
        alpha = max(0, int(40 * (1 - r / 700)))
        d.ellipse([W // 2 - r, -r // 2, W // 2 + r, r * 1.5],
                  fill=(255, 255, 255, alpha))

    cx = W // 2

    # --- printer body (centered, dominant) ---
    body_w, body_h = 720, 420
    bx = cx - body_w // 2
    by = 360
    # shadow
    for off in range(20, 0, -2):
        a = max(0, int(50 * (1 - off / 20)))
        d.rounded_rectangle([bx - off // 2, by + 18 + off // 2,
                             bx + body_w + off // 2, by + body_h + 18 + off // 2],
                            radius=64, fill=(0, 0, 0, a // 3))
    # main body — lighter top half, darker bottom (metallic look)
    body_top = lerp(INK, (220, 228, 245), 0.55)
    body_bot = lerp(INK, BG_BOT, 0.25)
    # draw the body in two halves with a gradient
    for y in range(body_h):
        t = y / max(1, body_h - 1)
        col = lerp(body_top, body_bot, t)
        d.line([(bx + 60, by + y) if y < 60 else (bx, by + y),
                (bx + body_w - 60, by + y) if y < 60 else (bx + body_w, by + y)],
               fill=col)
    # clip to rounded body shape using a mask
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([bx, by, bx + body_w, by + body_h],
                         radius=64, fill=255)
    # re-draw body onto a fresh layer
    body_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body_layer, "RGBA")
    for y in range(body_h):
        t = y / max(1, body_h - 1)
        col = lerp(body_top, body_bot, t)
        bd.line([(bx, by + y), (bx + body_w, by + y)], fill=(*col, 255))
    body_layer.putalpha(mask)
    img.paste(body_layer, (0, 0), body_layer)
    d = ImageDraw.Draw(img, "RGBA")

    # top edge highlight
    d.rounded_rectangle([bx, by, bx + body_w, by + 12],
                        radius=6, fill=(255, 255, 255, 80))

    # --- paper output slot + emerging paper ---
    slot_w = 420
    sx = cx - slot_w // 2
    sy = by + 20
    d.rounded_rectangle([sx, sy, sx + slot_w, sy + 30],
                        radius=14, fill=(*lerp(INK, (0, 0, 0), 0.4), 255))

    # paper coming out (top, centered)
    out_w, out_h = 380, 230
    ox = cx - out_w // 2
    oy = sy - out_h + 20
    # shadow of paper
    d.rounded_rectangle([ox - 4, oy + 8, ox + out_w + 4, oy + out_h + 8],
                        radius=18, fill=(0, 0, 0, 80))
    # paper body
    d.rounded_rectangle([ox, oy, ox + out_w, oy + out_h],
                        radius=16, fill=PAPER)
    # text lines on output paper
    line_color = lerp(PAPER, INK, 0.22)
    margins = 44
    line_x0 = ox + margins
    line_x1 = ox + out_w - margins
    line_y0 = oy + 40
    for i, frac in enumerate([0.92, 0.78, 1.0, 0.65, 0.85, 0.70, 0.50]):
        ly = line_y0 + i * 22
        if ly > oy + out_h - 30:
            break
        lw = int((line_x1 - line_x0) * frac)
        lx0 = line_x0 + ((line_x1 - line_x0) - lw) // 2
        d.rounded_rectangle([lx0, ly, lx0 + lw, ly + 8],
                            radius=4, fill=line_color)

    # --- control panel (right side) ---
    panel_w, panel_h = 180, 76
    pnlx = bx + body_w - panel_w - 60
    pnly = by + 90
    d.rounded_rectangle([pnlx, pnly, pnlx + panel_w, pnly + panel_h],
                        radius=38, fill=(0, 0, 0, 110))
    # tiny LCD screen
    scr_w, scr_h = 110, 40
    scrx = pnlx + 18
    scry = pnly + 18
    d.rounded_rectangle([scrx, scry, scrx + scr_w, scry + scr_h],
                        radius=10, fill=(20, 40, 70))
    # screen pixels
    for px_i in range(4):
        for py_i in range(2):
            dotx = scrx + 12 + px_i * 22
            doty = scry + 10 + py_i * 14
            d.rectangle([dotx, doty, dotx + 14, doty + 6],
                        fill=(90, 200, 255))

    # --- green GO button (left) ---
    btn_d = 130
    btnx = bx + 130
    btny = by + 128
    # outer ring / glow
    for r in range(btn_d // 2 + 36, btn_d // 2, -2):
        a = max(0, int(70 * (1 - (r - btn_d // 2) / 36)))
        d.ellipse([btnx - r, btny - r, btnx + r, btny + r],
                  fill=(*ACCENT, a))
    # button body with radial gradient (lighter top-left)
    for r in range(btn_d // 2, 0, -1):
        t = 1 - (r / (btn_d // 2))
        col = lerp(ACCENT_DK, ACCENT, max(0, t * 2 - 0.4))
        d.ellipse([btnx - r, btny - r, btnx + r, btny + r], fill=col)
    # specular highlight
    d.ellipse([btnx - btn_d // 4, btny - btn_d // 4,
               btnx + btn_d // 8, btny - btn_d // 8],
              fill=(255, 255, 255, 90))
    # checkmark on the button
    cw = 18
    check_pts = [
        (btnx - 34, btny + 4),
        (btnx - 10, btny + 30),
        (btnx + 40, btny - 30),
    ]
    d.line(check_pts[:2], fill=(*WHITE, 255), width=cw, joint="curve")
    d.line(check_pts[1:], fill=(*WHITE, 255), width=cw, joint="curve")

    # --- feet ---
    foot_w, foot_h = 70, 26
    for fx in [bx + 90, bx + body_w - 90 - foot_w]:
        d.rounded_rectangle([fx, by + body_h - 8, fx + foot_w, by + body_h + foot_h - 8],
                            radius=13, fill=lerp(INK, (0, 0, 0), 0.35))

    return img


def main() -> None:
    master = draw_icon()
    master.save("AppIcon-1024.png")
    print("wrote AppIcon-1024.png (1024x1024)")

    # iOS single-size asset (Xcode 14+ uses a single 1024 image in Assets)
    import os
    os.makedirs("AppIcon.appiconset", exist_ok=True)
    master.save("AppIcon.appiconset/AppIcon-1024.png")
    contents = {
        "images": [
            {"filename": "AppIcon-1024.png",
             "idiom": "universal",
             "platform": "ios",
             "size": "1024x1024"}
        ],
        "info": {"author": "xcode", "version": 1}
    }
    import json
    with open("AppIcon.appiconset/Contents.json", "w") as f:
        json.dump(contents, f, indent=2)
    print("wrote AppIcon.appiconset/ (iOS asset catalog)")


if __name__ == "__main__":
    main()
