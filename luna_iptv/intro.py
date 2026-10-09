"""A short opening: the eclipse lights up over the night sky, then the window appears."""

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import brand, theme
from .motion import motion_level

DURATION_MS = 1500
FADE_FROM = 0.72  # the last part of the opening fades the cover away


class IntroOverlay(QWidget):
    """Covers its parent for a moment; painting only, no input, gone after one run."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(DURATION_MS)
        self.animation.setEasingCurve(QEasingCurve.Linear)
        self.animation.valueChanged.connect(self._step)
        self.animation.finished.connect(self.close)
        parent.installEventFilter(self)

    @classmethod
    def play(cls, window):
        """Only with full motion; otherwise the window simply appears."""
        if motion_level() != "full":
            return None
        overlay = cls(window)
        overlay.setGeometry(window.rect())
        overlay.show()
        overlay.raise_()
        overlay.animation.start()
        return overlay

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == event.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def _step(self, value):
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        p = self.progress
        cover = 1.0 if p < FADE_FROM else max(0.0, 1.0 - (p - FADE_FROM) / (1 - FADE_FROM))
        if cover <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(cover)
        painter.fillRect(self.rect(), QColor(theme.NIGHT))
        centre = QPointF(self.width() / 2, self.height() / 2 - 18)
        light = _ease(min(1.0, p / 0.55))
        # A halo that widens as the corona "ignites".
        halo = QRadialGradient(centre, 60 + 140 * light)
        glow = QColor(theme.ACCENT)
        glow.setAlpha(round(70 * light))
        halo.setColorAt(0.0, glow)
        glow.setAlpha(0)
        halo.setColorAt(1.0, glow)
        painter.setBrush(halo)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(centre, 60 + 140 * light, 60 + 140 * light)
        side = 300 * (0.86 + 0.14 * light)
        box = QRectF(0, 0, side, side)
        box.moveCenter(centre)
        painter.setOpacity(cover * (0.25 + 0.75 * light))
        brand.paint(painter, box, seconds=p * 2.0, tile=False)
        word = _ease(max(0.0, min(1.0, (p - 0.3) / 0.35)))
        if word:
            font = QFont(self.font())
            font.setPixelSize(15)
            font.setWeight(QFont.Bold)
            font.setLetterSpacing(QFont.AbsoluteSpacing, 6 + 6 * (1 - word))
            painter.setFont(font)
            painter.setOpacity(cover * word)
            painter.setPen(QColor(theme.TEXT_SOFT))
            text = QRectF(0, centre.y() + side * 0.33, self.width(), 24)
            painter.drawText(text, Qt.AlignHCenter | Qt.AlignTop, "LUNA")
        painter.end()


def _ease(t):
    return 1 - math.pow(1 - t, 3)
