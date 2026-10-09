"""Small animated widgets for Luna's interface.

Every animation here is short and event-driven (hover, press, section change),
except the welcome sky, which runs a low-rate timer only while it is visible.
Nothing animates while video is on screen.
"""

import math
import random

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QWidget,
)

from . import brand, icons, theme


class _MotionSignals(QObject):
    changed = Signal()


_motion_signals = _MotionSignals()
_motion_level = "full"


def set_motion_level(level):
    """Apply the shared motion policy to new and existing widgets immediately."""
    global _motion_level
    if level not in ("full", "reduced", "off"):
        raise ValueError("Unknown motion level")
    if level != _motion_level:
        _motion_level = level
        _motion_signals.changed.emit()


def motion_level():
    """The shared motion policy: "full", "reduced" or "off"."""
    return _motion_level


def animation_ms(full, reduced=0):
    """How long an interface animation lasts under the motion policy (0 = jump)."""
    return {"full": full, "reduced": reduced}.get(_motion_level, 0)


def on_motion_changed(callback):
    _motion_signals.changed.connect(callback)


def blend(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


class IconButton(QPushButton):
    """Push button that draws a Luna icon chosen from its text state.

    The text stays the button's state and accessible value (for example "▶" or
    "Ⅱ"), so behavior code keeps calling setText(); the icon follows it. When
    ``label`` is true the text is also drawn beside the icon.
    """

    def __init__(
        self,
        text,
        icon=None,
        *,
        label=False,
        stacked=False,
        size=20,
        caption="",
        color=None,
        hover_color=None,
        checked_color=None,
        parent=None,
    ):
        super().__init__(text, parent)
        self._icon = icon
        self.caption = caption
        self.show_label = label
        self.stacked = stacked
        self.icon_px = size
        self._colors = (color, hover_color, checked_color)
        self._hover = 0.0
        self._hover_target = 0.0
        self._animation = QVariantAnimation(self, duration=160)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._set_hover)
        self.setCursor(Qt.PointingHandCursor)
        _motion_signals.changed.connect(self._motion_changed)

    def icon_name(self):
        return icons.GLYPHS.get(self.text(), self._icon)

    def set_icon_name(self, name):
        self._icon = name
        self.update()

    def _set_hover(self, value):
        self._hover = float(value)
        self.update()

    def _animate(self, target):
        self._hover_target = target
        self._animation.stop()
        if _motion_level != "full":
            self._set_hover(target)
            return
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(target)
        self._animation.start()

    def _motion_changed(self):
        if _motion_level != "full":
            self._animation.stop()
            self._set_hover(self._hover_target)
        self.update()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(0.0)
        super().leaveEvent(event)

    def sizeHint(self):
        if self.stacked:
            return QSize(72, self.icon_px + 40)
        if not self.show_label:
            side = self.icon_px + 18
            return QSize(side, side)
        hint = super().sizeHint()
        return QSize(hint.width() + self.icon_px + 8, max(hint.height(), self.icon_px + 16))

    def minimumSizeHint(self):
        return self.sizeHint()

    @property
    def colors(self):
        """Explicit overrides stay fixed; defaults follow the live palette."""
        return tuple(
            value if value is not None else default
            for value, default in zip(
                self._colors, (theme.TEXT_SOFT, theme.TEXT, theme.ACCENT_STRONG), strict=True
            )
        )

    def icon_color(self):
        normal, hover, checked = self.colors
        if not self.isEnabled():
            return theme.TEXT_MUTED
        if self.icon_name() == "star-filled":
            return theme.GOLD
        if self.isChecked():
            return checked
        return blend(normal, hover, self._hover)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButtonBevel, option)
        name = self.icon_name()
        color = self.icon_color()
        rect = self.rect()
        scale = 1.0
        if _motion_level == "full":
            scale += 0.08 * self._hover - (0.07 if self.isDown() else 0.0)
        side = self.icon_px * scale
        if self.stacked:
            icon_rect = QRectF(
                (rect.width() - side) / 2, 12 + (self.icon_px - side) / 2, side, side
            )
            font = painter.font()
            font.setPointSizeF(7.5)
            painter.setFont(font)
            painter.setPen(QColor(color))
            label_rect = QRect(
                0, 14 + self.icon_px, rect.width(), rect.height() - 18 - self.icon_px
            )
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, self.text())
        elif self.show_label:
            metrics = self.fontMetrics()
            text_width = metrics.horizontalAdvance(self.text())
            if self.objectName() == "nav":
                left = 14.0
            else:
                left = (rect.width() - self.icon_px - 8 - text_width) / 2
            icon_rect = QRectF(left, (rect.height() - side) / 2, side, side)
            painter.setPen(QColor(color if self.objectName() != "primary" else theme.ACCENT_INK))
            text_rect = QRect(round(left + self.icon_px + 10), 0, rect.width(), rect.height())
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.text())
        else:
            icon_rect = QRectF((rect.width() - side) / 2, (rect.height() - side) / 2, side, side)
        if name:
            ratio = self.devicePixelRatioF()
            if self.objectName() in {"primary", "hero"}:
                color = theme.ACCENT_INK
            image = icons.pixmap(name, color, self.icon_px, ratio)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(icon_rect, image, QRectF(image.rect()))
        if self.caption:
            font = painter.font()
            font.setPointSizeF(6.5)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(icon_rect.translated(0, 0.5), Qt.AlignCenter, self.caption)


class NavIndicator(QFrame):
    """Moonlit pill that glides behind the active sidebar section."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("navIndicator")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._target = None
        self._animation = QPropertyAnimation(self, b"geometry", self, duration=260)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self.lower()
        _motion_signals.changed.connect(self._motion_changed)

    def _motion_changed(self):
        if _motion_level == "off":
            self._animation.stop()
            self.resync()

    def follow(self, button, *, animate=True):
        self._target = button
        geometry = button.geometry()
        if (
            _motion_level == "off"
            or not animate
            or not self.isVisible()
            or self.geometry().isEmpty()
        ):
            self._animation.stop()
            self.setGeometry(geometry)
            self.show()
            return
        self._animation.stop()
        self._animation.setStartValue(self.geometry())
        self._animation.setEndValue(geometry)
        self._animation.start()

    def resync(self):
        if self._target is not None and self._animation.state() != QPropertyAnimation.Running:
            self.setGeometry(self._target.geometry())


class NavFrame(QFrame):
    """Sidebar frame that keeps the nav indicator aligned through layout changes."""

    def __init__(self):
        super().__init__()
        self.indicator = NavIndicator(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resync_indicator()

    def showEvent(self, event):
        super().showEvent(event)
        self._resync_indicator()

    def _resync_indicator(self):
        if _motion_level == "off":
            self.indicator.resync()
        else:
            QTimer.singleShot(0, self.indicator.resync)


class LogoMark(QWidget):
    """The eclipse logo; it moves while hovered and rests otherwise.

    ``set_quiet(True)`` keeps it still regardless of the pointer; the window
    uses that while video is on screen.
    """

    FRAME_MS = 33

    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setToolTip("Luna IPTV")
        self._clock = QElapsedTimer()
        self._seconds = 0.0
        self._quiet = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        _motion_signals.changed.connect(self._motion_changed)

    @property
    def animating(self):
        return self._timer.isActive()

    def set_quiet(self, quiet):
        self._quiet = bool(quiet)
        if self._quiet:
            self._timer.stop()
        elif self.underMouse() and self.isVisible():
            self._start()

    def _start(self):
        if not self._quiet and _motion_level == "full":
            self._clock.start()
            self._timer.start()

    def _motion_changed(self):
        self._timer.stop()
        if _motion_level == "full" and self.underMouse() and self.isVisible():
            self._start()
        self.update()

    def _tick(self):
        self._seconds += self._clock.restart() / 1000.0
        self.update()

    def enterEvent(self, event):
        self._start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._timer.stop()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        brand.paint(painter, QRectF(self.rect()), self._seconds if _motion_level == "full" else 0)


class MoonSky(QFrame):
    """Quiet night sky behind the welcome message: twinkling stars and a breathing moon.

    It repaints at a low rate only while visible; switching to video hides it and
    stops the timer, so playback never shares time with this animation.
    """

    FRAME_MS = 50
    MOON_RADIUS = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sky")
        self.setAttribute(Qt.WA_StyledBackground, False)
        generator = random.Random(7)
        self.stars = [
            (
                generator.random(),
                generator.random(),
                generator.uniform(0.6, 1.7),
                generator.uniform(0, math.tau),
                generator.uniform(0.35, 1.0),
            )
            for _ in range(70)
        ]
        self.moon_anchor = None
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self.update)
        _motion_signals.changed.connect(self._motion_changed)

    @property
    def animating(self):
        return self._timer.isActive()

    def showEvent(self, event):
        super().showEvent(event)
        self._motion_changed()

    def _motion_changed(self):
        if _motion_level == "full" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def moon_center(self):
        if self.moon_anchor is not None and self.moon_anchor.isVisible():
            box = self.moon_anchor.geometry()
            return QPointF(box.center())
        return QPointF(self.width() / 2, self.height() * 0.32)

    def paintEvent(self, event):
        seconds = self._clock.elapsed() / 1000.0 if _motion_level == "full" else 0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 18, 18)
        painter.setClipPath(clip)
        sky = QRadialGradient(QPointF(frame.center().x(), frame.top()), frame.width() * 0.8)
        sky.setColorAt(0.0, QColor("#1a2350"))
        sky.setColorAt(0.55, QColor("#0f1531"))
        sky.setColorAt(1.0, QColor("#0a0e1f"))
        painter.fillRect(frame, sky)
        painter.setPen(Qt.NoPen)
        for x, y, size, phase, depth in self.stars:
            glow = 0.55 + 0.45 * math.sin(seconds * (0.6 + depth) + phase)
            star = QColor(theme.ACCENT_STRONG)
            star.setAlphaF(max(0.08, min(1.0, glow * depth * 0.85)))
            painter.setBrush(star)
            painter.drawEllipse(QPointF(x * frame.width(), y * frame.height()), size, size)
        # The eclipse logo rises in this sky, without its icon tile.
        center = self.moon_center()
        side = self.MOON_RADIUS * 4.8
        brand.paint(
            painter,
            QRectF(center.x() - side / 2, center.y() - side / 2, side, side),
            seconds,
            tile=False,
        )
        painter.setClipping(False)
        painter.setPen(QColor(theme.LINE_SOFT))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(frame, 18, 18)
