"""Generate simple PWA icons for MLB Edge.

Run once: python generate_icons.py
Requires Pillow: pip install Pillow
"""

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Install Pillow first: pip install Pillow")

SIZES = [192, 512]
OUT_DIR = Path(__file__).parent / "docs" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BG = (15, 23, 42)       # --bg (#0f172a)
ACCENT = (96, 165, 250)  # --blue (#60a5fa)


def make_icon(size: int) -> None:
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Rounded square background circle
    pad = size // 8
    draw.ellipse([pad, pad, size - pad, size - pad], fill=ACCENT)

    # Baseball stitching lines (simple cross)
    cx, cy = size // 2, size // 2
    r = size // 4
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
    draw.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=BG)

    out = OUT_DIR / f"icon-{size}.png"
    img.save(out, "PNG")
    print(f"Wrote {out}")


for s in SIZES:
    make_icon(s)

print("Done — icons written to docs/icons/")
