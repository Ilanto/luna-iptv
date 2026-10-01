#!/usr/bin/env python3
"""Prepare Luna's eclipse logo from generated artwork layers.

Usage: build-logo.py LAYER_DIR

LAYER_DIR holds backdrop.png, corona.png, sparkle.png and still.png (1024 px,
the corona and sparkle on black). The script measures the disc and sparkle
placement, writes the layers and geometry to assets/logo, renders the desktop
icon sizes from the same painter the app uses, and exports an animated GIF.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from luna_iptv import brand  # noqa: E402

LAYER_SIZE = 768
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def brightness(image, x, y):
    colour = image.pixelColor(x, y)
    return (colour.red() + colour.green() + colour.blue()) / 3


def measure(corona, still):
    """Disc edge, rim radius and sparkle angle as fractions of the image width."""
    width = corona.width()
    cx = cy = width / 2
    rims, edges = [], []
    for step in range(36):
        angle = math.tau * step / 36
        profile = [
            brightness(corona, int(cx + r * math.cos(angle)), int(cy + r * math.sin(angle)))
            for r in range(int(width * 0.1), int(width * 0.4))
        ]
        peak = max(range(len(profile)), key=profile.__getitem__)
        rims.append(peak + int(width * 0.1))
        threshold = profile[peak] * 0.25
        edge = next(i for i in range(len(profile)) if profile[i] >= threshold)
        edges.append(edge + int(width * 0.1))
    rims.sort()
    edges.sort()
    rim = rims[len(rims) // 2] / width
    disc = (edges[len(edges) // 2] / width) * 0.985
    # The sparkle is in the finished artwork but not in the corona layer: the
    # point around the rim where the two differ most is where it sits.
    best, best_angle = -1.0, -42.0
    for step in range(360):
        angle = math.tau * step / 360
        for offset in (-0.01, 0.0, 0.01, 0.02):
            r = (rim + offset) * still.width()
            x = int(still.width() / 2 + r * math.cos(angle))
            y = int(still.height() / 2 + r * math.sin(angle))
            value = brightness(still, x, y) - brightness(corona, x, y)
            if value > best:
                best, best_angle = value, math.degrees(angle)
    if best_angle > 180:
        best_angle -= 360
    return {"disc": round(disc, 4), "rim": round(rim, 4), "sparkle_angle": round(best_angle, 1)}


def render(size, seconds=0.0, tile=True):
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    brand.paint(painter, QRectF(0, 0, size, size), seconds, tile=tile)
    painter.end()
    return image


def main():
    source = Path(sys.argv[1])
    app = QGuiApplication([])  # noqa: F841 - pixmaps need a GUI application
    target = brand.ASSETS
    target.mkdir(parents=True, exist_ok=True)
    for name in ("backdrop.png", "corona.png", "sparkle.png"):
        layer = QImage(str(source / name))
        if layer.isNull():
            raise SystemExit(f"Missing layer: {source / name}")
        layer.scaled(LAYER_SIZE, LAYER_SIZE, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(
            str(target / name), "PNG", 0
        )
    geometry = measure(QImage(str(source / "corona.png")), QImage(str(source / "still.png")))
    (target / "geometry.json").write_text(json.dumps(geometry, indent=2) + "\n")
    brand.geometry.cache_clear()
    brand._layer.cache_clear()
    print("geometry", geometry)

    for size in ICON_SIZES:
        # Small icons are rendered large and reduced, which keeps the rays crisp.
        render(max(size * 4, 256)).scaled(
            size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        ).save(str(target / f"icon-{size}.png"))

    frames = 72
    with tempfile.TemporaryDirectory() as folder:
        for index in range(frames):
            render(512, brand.PERIOD * index / frames).scaled(
                256, 256, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            ).save(f"{folder}/frame-{index:03d}.png")
        rate = frames / brand.PERIOD
        palette = f"{folder}/palette.png"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                f"{rate}",
                "-i",
                f"{folder}/frame-%03d.png",
                "-vf",
                "palettegen=reserve_transparent=1",
                palette,
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                f"{rate}",
                "-i",
                f"{folder}/frame-%03d.png",
                "-i",
                palette,
                "-lavfi",
                "paletteuse=dither=bayer:bayer_scale=4:alpha_threshold=128",
                "-loop",
                "0",
                str(ROOT / "docs" / "luna-logo.gif"),
            ],
            check=True,
        )
    print("icons written to", target, "and the GIF to docs/luna-logo.gif")


if __name__ == "__main__":
    main()
