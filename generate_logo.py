#!/usr/bin/env python3
"""
generate_logo.py
================
assets/logo.svg ile görsel olarak eşleşen logoyu üretir:
  • assets/logo.png  (512x512, README/önizleme için)
  • assets/icon.ico  (16..256 çok boyutlu — exe + pencere + installer ikonu)

SVG'yi rasterize edecek bir araç (cairosvg/Inkscape) gerekmez; şekiller burada
doğrudan Pillow ile çizilir. SVG ile aynı geometriyi/renkleri kullanır.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 512
RADIUS = 110

# Köşe renkleri (SVG gradyanıyla aynı): mor -> indigo -> camgöbeği
TL, TR, BL, BR = (124, 58, 237), (79, 70, 229), (37, 99, 235), (6, 182, 212)


def build() -> Image.Image:
    # 2x2 köşe gradyanını BILINEAR ile büyüterek yumuşak 2B gradyan elde et.
    g = Image.new("RGB", (2, 2))
    g.putpixel((0, 0), TL)
    g.putpixel((1, 0), TR)
    g.putpixel((0, 1), BL)
    g.putpixel((1, 1), BR)
    grad = g.resize((SIZE, SIZE), Image.BILINEAR)

    # Yuvarlak köşe maskesi.
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # Beyaz indirme oku (SVG ile aynı koordinatlar).
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = SIZE // 2
    d.rounded_rectangle([cx - 29, 150, cx + 29, 300], radius=20, fill=white)   # gövde
    d.polygon([(136, 280), (376, 280), (cx, 400)], fill=white)                 # ok başı
    d.rounded_rectangle([106, 420, 406, 452], radius=16, fill=white)           # tepsi
    return img


def main() -> None:
    img = build()
    img.save(ASSETS / "logo.png")
    img.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Üretildi: {ASSETS/'logo.png'}  ve  {ASSETS/'icon.ico'}")


if __name__ == "__main__":
    main()
