"""Luna's eclipse logo, painted from layered artwork so it can move.

The artwork layers live in ``assets/logo`` (generated once, see
``scripts/build-logo.py``): a night backdrop, the corona on black and the
diamond-ring sparkle on black. The bright layers are added with the "plus"
composition mode, so their black backgrounds vanish; the dark disc and the
faint play mark are drawn as vectors on top. Every pixmap is scaled once per
size, so a frame is a few cached blits.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "logo"
# One loop of the logo's motion; GIF exports use the same period.
PERIOD = 6.0


def asset(name):
    return ASSETS / name


@lru_cache(maxsize=1)
def geometry():
    """Disc and sparkle placement measured from the artwork (fractions of its width)."""
    try:
        return json.loads(asset("geometry.json").read_text())
    except (OSError, ValueError):
        return {"disc": 0.205, "rim": 0.228, "sparkle_angle": -42.0}


@lru_cache(maxsize=32)
def _layer(name, pixels):
    image = QPixmap(str(asset(name)))
    if image.isNull():
        return None
    return image.scaled(pixels, pixels, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def tile_path(rect):
    path = QPainterPath()
    radius = rect.width() * 0.225
    path.addRoundedRect(rect, radius, radius)
    return path


def paint(painter, rect, seconds=0.0, *, tile=True):
    """Paint the logo into ``rect`` at time ``seconds`` (0 gives the still pose)."""
    side = min(rect.width(), rect.height())
    box = QRectF(0, 0, side, side)
    box.moveCenter(rect.center())
    ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
    pixels = max(8, round(side * ratio))
    shape = geometry()
    phase = math.tau * (seconds % PERIOD) / PERIOD
    centre = box.center()

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    if tile:
        painter.setClipPath(tile_path(box))
        backdrop = _layer("backdrop.png", pixels)
        if backdrop is not None:
            painter.drawPixmap(box, backdrop, QRectF(backdrop.rect()))
        else:
            painter.fillRect(box, QColor("#0b1020"))

    corona = _layer("corona.png", pixels)
    if corona is not None:
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.translate(centre)
        # A slow sway rather than a spin keeps every loop seamless.
        painter.rotate(5.0 * math.sin(phase))
        breathe = 1.0 + 0.018 * math.sin(phase * 2)
        painter.scale(breathe, breathe)
        painter.setOpacity(0.92 + 0.08 * math.sin(phase * 2))
        painter.drawPixmap(QRectF(-side / 2, -side / 2, side, side), corona, QRectF(corona.rect()))
        painter.restore()

    disc_radius = side * shape["disc"]
    disc = QRadialGradient(
        centre - QPointF(disc_radius * 0.3, disc_radius * 0.35), disc_radius * 1.4
    )
    disc.setColorAt(0.0, QColor("#141a45"))
    disc.setColorAt(1.0, QColor("#03040f"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(disc)
    painter.drawEllipse(centre, disc_radius, disc_radius)
    mark = disc_radius * 0.42
    play = QPainterPath()
    play.moveTo(centre + QPointF(-mark * 0.55, -mark))
    play.lineTo(centre + QPointF(mark * 1.0, 0))
    play.lineTo(centre + QPointF(-mark * 0.55, mark))
    play.closeSubpath()
    painter.setBrush(QColor(29, 37, 96, 210))
    painter.setPen(
        QPen(QColor(29, 37, 96, 210), mark * 0.28, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    )
    painter.drawPath(play)

    sparkle = _layer("sparkle.png", pixels)
    if sparkle is not None:
        angle = math.radians(shape["sparkle_angle"])
        rim = side * shape["rim"]
        point = centre + QPointF(rim * math.cos(angle), rim * math.sin(angle))
        pulse = 0.55 + 0.12 * math.sin(phase * 3) + 0.08 * math.sin(phase * 7)
        size = side * pulse
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.translate(point)
        painter.rotate(8.0 * math.sin(phase))
        painter.drawPixmap(
            QRectF(-size / 2, -size / 2, size, size), sparkle, QRectF(sparkle.rect())
        )
        painter.restore()

    if tile:
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, side / 128)))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(tile_path(box.adjusted(0.5, 0.5, -0.5, -0.5)))
    painter.restore()
