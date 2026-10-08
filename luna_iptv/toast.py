"""Accessible, transient status messages above the main window's content."""

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QColor,
    QPainter,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .motion import animation_ms, motion_level, on_motion_changed


class Toast(QWidget):
    """One replaceable message, with a paused lifetime while the pointer rests on it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.text = ""
        self._remaining = 0
        self._hovered = False
        self._dismissing = False
        self._replacement = None
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.pill = QFrame()
        self.pill.setObjectName("toastPill")
        self.pill.setStyleSheet(
            f"QFrame#toastPill {{ background: {theme.RAISED}; border: 1px solid {theme.LINE};"
            f" border-radius: 18px; }} QLabel {{ color: {theme.TEXT}; background: transparent; }}"
        )
        outer.addWidget(self.pill)
        row = QHBoxLayout(self.pill)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        self.icon.hide()
        row.addWidget(self.icon)
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        row.addWidget(self.label)
        for child in (self.pill, self.icon, self.label):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(1)
        self.setGraphicsEffect(self.opacity)
        self.animation = QParallelAnimationGroup(self)
        self.animation.finished.connect(self._finished)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        parent.installEventFilter(self)
        on_motion_changed(self._motion_changed)
        self.hide()

    @staticmethod
    def duration_for(text):
        return min(7000, max(3200, 2400 + 45 * len(text)))

    def show_message(self, text, *, icon=None, duration=None):
        replacing = self.isVisible()
        self._clear_animation()
        if replacing and animation_ms(100, 80):
            self._replacement = QLabel(self.parentWidget())
            self._replacement.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._replacement.setPixmap(self.grab())
            self._replacement.setGeometry(self.geometry())
            self._replacement.show()
            effect = QGraphicsOpacityEffect(self._replacement)
            self._replacement.setGraphicsEffect(effect)
            self._animate(effect, b"opacity", 1.0, 0.0, animation_ms(100, 80))
        self._dismissing = False
        self.text = text
        self.setAccessibleName(text)
        self.setAccessibleDescription(text)
        self.label.setAccessibleName(text)
        self.icon.setVisible(icon is not None)
        if icon is not None:
            self.icon.setPixmap(icons.pixmap(icon, theme.TEXT, 20, self.devicePixelRatioF()))
        self.reposition()
        self.show()
        self.raise_()
        self.opacity.setOpacity(1)
        fade_ms = animation_ms(100, 80) if replacing else animation_ms(180, 100)
        if fade_ms:
            self.opacity.setOpacity(0)
            self._animate(self.opacity, b"opacity", 0.0, 1.0, fade_ms)
        if not replacing and motion_level() == "full":
            destination = self.pos()
            self._animate(
                self, b"pos", destination + QPoint(0, 12), destination, animation_ms(180, 0)
            )
        if self.animation.animationCount():
            self.animation.start()
        self.timer.stop()
        self._remaining = self.duration_for(text) if duration is None else max(0, duration)
        self.timer.setInterval(self._remaining)
        if not self._hovered:
            self.timer.start()
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self, text))

    def _animate(self, target, property_name, start, end, duration):
        animation = QPropertyAnimation(target, property_name)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.addAnimation(animation)

    def reposition(self):
        """Wrap once, elide the second line, and stay inside the window."""
        available = max(80, min(520, self.parentWidget().width() - 32))
        padding = 52 + (30 if not self.icon.isHidden() else 0)
        metrics = self.label.fontMetrics()
        plain = " ".join(self.text.split())
        width = min(available, max(90, metrics.horizontalAdvance(plain) + padding))
        text_width = max(1, width - padding)
        layout = QTextLayout(plain, self.label.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        first = layout.createLine()
        if first.isValid():
            first.setLineWidth(text_width)
            split = first.textLength()
        else:
            split = len(plain)
        layout.endLayout()
        lines = [plain[:split].rstrip()]
        if split < len(plain):
            lines.append(metrics.elidedText(plain[split:].lstrip(), Qt.ElideRight, text_width))
        self.label.setText("\n".join(lines))
        self.label.setFixedSize(text_width, metrics.lineSpacing() * len(lines))
        self.setFixedSize(width, max(20, self.label.height()) + 40)
        self.move(
            (self.parentWidget().width() - width) // 2,
            self.parentWidget().height() - self.height() - 20,
        )

    def dismiss(self, *, immediate=False):
        self.timer.stop()
        self._clear_animation()
        self._dismissing = True
        duration = 0 if immediate else animation_ms(140, 100)
        if not duration or not self.isVisible():
            self.hide()
            return
        self._animate(self.opacity, b"opacity", self.opacity.opacity(), 0.0, duration)
        self.animation.start()

    def _clear_animation(self):
        self.animation.stop()
        self.animation.clear()
        if self._replacement is not None:
            self._replacement.hide()
            self._replacement.deleteLater()
            self._replacement = None

    def _finished(self):
        if self._dismissing:
            self.hide()
        self._clear_animation()

    def _motion_changed(self):
        if motion_level() != "full":
            self._clear_animation()
            self.opacity.setOpacity(1)
            self.reposition()
            if self._dismissing:
                self.hide()

    def enterEvent(self, event):
        self._hovered = True
        if self.timer.isActive():
            self._remaining = max(1, self.timer.remainingTime())
            self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if self.isVisible() and not self._dismissing:
            self.timer.start(self._remaining)
        super().leaveEvent(event)

    def paintEvent(self, event):
        # A tiny painted shadow avoids nesting graphics effects under the fade.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for spread in range(6, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 5 + 2 * (6 - spread)))
            rect = self.rect().adjusted(8 - spread, 11 - spread, spread - 8, spread - 5)
            painter.drawRoundedRect(rect, 18 + spread, 18 + spread)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dismiss()
            event.accept()
        else:
            super().mousePressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.text and not self._dismissing and self._remaining > 0:
            self.timer.start(self._remaining)

    def hideEvent(self, event):
        if self.timer.isActive():
            self._remaining = max(1, self.timer.remainingTime())
        self.timer.stop()
        self._hovered = False
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and self.isVisible():
            self._clear_animation()
            self.opacity.setOpacity(1)
            self.reposition()
            if self._dismissing:
                self.hide()
        return False
