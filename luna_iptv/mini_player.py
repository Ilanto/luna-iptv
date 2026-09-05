"""Compact mode for the existing native window; never replaces its video surface."""

from PySide6.QtCore import QObject, QRect, QSize
from PySide6.QtWidgets import QSizePolicy


class MiniPlayerController(QObject):
    """Keep the normal window snapshot while sharing the existing overlay."""

    INITIAL_SIZE = QSize(560, 390)
    MINIMUM_SIZE = QSize(480, 300)

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.active = False
        self._closed = False
        self._normal_geometry = QRect()
        self._normal_minimum = QSize()
        self._normal_maximized = False
        self._mini_geometry = QRect()
        self._video_minimum = QSize()
        self._video_policy = None

    def enter(self):
        if self._closed or self.active:
            return
        window = self._window
        self._normal_maximized = window.isMaximized()
        self._normal_geometry = QRect(
            window.normalGeometry() if self._normal_maximized else window.geometry()
        )
        self._normal_minimum = QSize(window.minimumSize())
        self._video_minimum = QSize(window.video_stack.minimumSize())
        self._video_policy = window.video_stack.sizePolicy()
        self.active = True
        window.fullscreen.set_active(True)
        window.fullscreen.set_compact(True)
        window.video_stack.setMinimumSize(0, 0)
        window.video_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        window.setMinimumSize(self.MINIMUM_SIZE)
        window.showNormal()
        window.resize(self.INITIAL_SIZE)
        self._mini_geometry = QRect(window.geometry())
        self._update_button()

    def set_fullscreen(self, fullscreen):
        if self._closed or not self.active:
            return
        if fullscreen:
            self._mini_geometry = QRect(self._window.geometry())
        self._window.fullscreen.set_compact(not fullscreen)

    def restore_mini_geometry(self):
        if self.active and not self._closed:
            self._window.setGeometry(self._mini_geometry)

    def leave(self):
        if self._closed or not self.active:
            return
        window = self._window
        window.fullscreen.set_compact(False)
        window.fullscreen.set_active(False)
        self._restore_constraints()
        self.active = False
        window.showNormal()
        window.setGeometry(self._normal_geometry)
        if self._normal_maximized:
            window.showMaximized()
        self._update_button()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.active:
            self._window.fullscreen.set_compact(False)
            self._restore_constraints()
        self.active = False

    def _restore_constraints(self):
        window = self._window
        window.video_stack.setMinimumSize(self._video_minimum)
        window.video_stack.setSizePolicy(self._video_policy)
        window.setMinimumSize(self._normal_minimum)

    def _update_button(self):
        button = self._window.mini_button
        button.setText("Geri dön" if self.active else "Mini")
        title = "Normal pencereye dön (Esc)" if self.active else "Mini oynatıcıya geç"
        button.setToolTip(title)
        button.setAccessibleName(title)
