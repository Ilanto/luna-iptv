"""Short shell transitions that leave live views and video surfaces alone."""

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QVariantAnimation,
)
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QSizePolicy

from .motion import animation_ms, motion_level, on_motion_changed


class WatchPanelController(QObject):
    """Remember the splitter width independently of playback and window modes."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.saved_width = None
        self._open = False
        self._minimum = window.watch.minimumWidth()
        self._policy = window.watch.sizePolicy()
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._set_width)
        self.animation.finished.connect(self._settle)
        window.splitter.splitterMoved.connect(self._dragged)
        on_motion_changed(self._motion_changed)
        self.sync(animate=False)

    def suspended(self):
        w = self.window
        return (
            w._fullscreen
            or w.fullscreen.active
            or w.mini_player.active
            or getattr(w, "tv_mode", None) is not None
        )

    def _dragged(self, _position, _index):
        if self.suspended() or not self._open:
            return
        self.animation.stop()
        self.saved_width = self.window.splitter.sizes()[1]
        self._settle()

    def _available(self):
        return max(0, self.window.splitter.width() - self.window.splitter.handleWidth())

    def _target_width(self):
        available = self._available()
        preferred = self.saved_width if self.saved_width is not None else available * 480 / 1380
        return max(self._minimum, min(round(preferred), available - 300))

    def sync(self, *, animate=True):
        w = self.window
        if w._closed or self.suspended():
            return
        opened = w.current is not None
        if opened == self._open and self.animation.state() == QAbstractAnimation.Running:
            return
        self.animation.stop()
        self._open = opened
        if not opened:
            focus = QApplication.focusWidget()
            if focus is w.watch or (focus is not None and w.watch.isAncestorOf(focus)):
                next(button for button in w.nav_buttons.values() if button.isChecked()).setFocus()
        start = 0 if w.watch.isHidden() else w.splitter.sizes()[1]
        end = self._target_width() if opened else 0
        duration = animation_ms(220, 0) if animate and w.isVisible() else 0
        if not duration or start == end:
            self._settle()
            return
        # Ignore the controls' minimum-size hints only during the slide.
        w.watch.setMinimumWidth(0)
        w.watch.setSizePolicy(QSizePolicy.Ignored, self._policy.verticalPolicy())
        w.watch.setEnabled(opened)
        w.watch.show()
        self.animation.setDuration(duration)
        self.animation.setStartValue(start)
        self.animation.setEndValue(end)
        self.animation.start()

    def _set_width(self, width):
        if self.suspended():
            self.animation.stop()
            return
        width = round(width)
        self.window.splitter.setSizes([max(0, self._available() - width), width])

    def _settle(self):
        if self.suspended():
            return
        w = self.window
        w.watch.setMinimumWidth(self._minimum)
        w.watch.setSizePolicy(self._policy)
        w.watch.setEnabled(True)
        w.watch.setVisible(self._open)
        self._set_width(self._target_width() if self._open else 0)

    def suspend(self):
        """Yield geometry ownership before fullscreen takes its snapshot."""
        self.animation.stop()
        w = self.window
        w.watch.setMinimumWidth(self._minimum)
        w.watch.setSizePolicy(self._policy)
        w.watch.setEnabled(True)
        w.watch.show()

    def _motion_changed(self):
        if motion_level() != "full":
            self.animation.stop()
            self.sync(animate=False)


class PageTransition(QObject):
    """Cross-fade cached page images; never animate a model or its paint effect."""

    def __init__(self, stack):
        super().__init__(stack)
        self.stack = stack
        self.overlay = None
        self.incoming = None
        self.animation = QParallelAnimationGroup(self)
        self.animation.finished.connect(self.finish)
        stack.installEventFilter(self)
        on_motion_changed(self.finish)

    def begin(self, changed):
        self.finish()
        if not changed or not animation_ms(160, 120) or not self.stack.isVisible():
            return
        self.overlay = self._snapshot()

    def _snapshot(self):
        label = QLabel(self.stack)
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
        label.setPixmap(self.stack.currentWidget().grab())
        label.setGeometry(self.stack.rect())
        label.show()
        label.raise_()
        return label

    def end(self):
        """Fade the old page's image out over the new page, which is already live.

        One snapshot, not two: grabbing a large grid costs about as much as the fade lasts.
        """
        if self.overlay is None:
            return
        self.overlay.raise_()
        duration = animation_ms(160, 120)
        effect = QGraphicsOpacityEffect(self.overlay)
        self.overlay.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(duration)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.addAnimation(fade)
        if motion_level() == "full":
            drift = QPropertyAnimation(self.overlay, b"pos")
            drift.setDuration(duration)
            drift.setStartValue(QPoint(0, 0))
            drift.setEndValue(QPoint(0, -8))
            drift.setEasingCurve(QEasingCurve.OutCubic)
            self.animation.addAnimation(drift)
        self.animation.start()

    def finish(self):
        self.animation.stop()
        # clear() makes Qt index animations it is deleting; take them out one by one.
        while self.animation.animationCount():
            self.animation.takeAnimation(0).deleteLater()
        for widget in (self.overlay, self.incoming):
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.overlay = self.incoming = None

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Hide):
            self.finish()
        return False
