"""Compact mode for the existing native window; never replaces its video surface."""

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QApplication, QSizePolicy
from shiboken6 import isValid


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
        self._pending_snapshot = None
        self._native_window = None
        self._request_generation = 0

    @property
    def pending(self):
        return self._pending_snapshot is not None

    def enter_after_fullscreen(self, normal_geometry, normal_maximized):
        """Wait for Wayland's windowed configure before requesting a smaller size."""
        self.cancel_pending()
        if self._closed:
            return
        self._pending_snapshot = (QRect(normal_geometry), bool(normal_maximized))
        generation = self._request_generation
        native = self._window.windowHandle()
        if QApplication.platformName() == "wayland" and native is not None:
            self._watch_native_window()
            QTimer.singleShot(2000, lambda: self._expire_pending(generation))
        else:
            QTimer.singleShot(0, lambda: self._complete_pending(generation))

    def cancel_pending(self):
        self._request_generation += 1
        self._pending_snapshot = None

    def _watch_native_window(self):
        native = self._window.windowHandle()
        if native is not None and native is not self._native_window:
            self._native_window = native
            native.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            watched is self._native_window
            and event.type() == QEvent.WindowStateChange
            and event.spontaneous()
            and (self.pending or event.oldState() & Qt.WindowFullScreen)
            and not watched.windowState() & Qt.WindowFullScreen
        ):
            generation = self._request_generation
            QTimer.singleShot(0, lambda: self._windowed_acknowledged(generation))
        return False

    def _windowed_acknowledged(self, generation):
        if self._closed or self._window._fullscreen or self._window.isFullScreen():
            return
        if self.pending:
            self._complete_pending(generation)
        elif self._normal_geometry.isValid():
            # A configure may arrive after a rapid F/Esc/return sequence. Restore
            # the latest desired windowed mode, never geometry captured by it.
            self._window._restore_windowed_state()

    def _complete_pending(self, generation):
        if self._closed or generation != self._request_generation or not self.pending:
            return
        if self._window._fullscreen or self._window.isFullScreen():
            return
        if self._window.isMaximized():
            self._window.showNormal()
            return
        geometry, maximized = self._pending_snapshot
        self.cancel_pending()
        self.enter(normal_geometry=geometry, normal_maximized=maximized)

    def _expire_pending(self, generation):
        if self._closed or generation != self._request_generation or not self.pending:
            return
        self.cancel_pending()
        self._window.status("Mini oynatıcıya geçilemedi. Yeniden dene.")

    def enter(self, *, normal_geometry=None, normal_maximized=None):
        if self._closed or self.active:
            return
        window = self._window
        if window.isMaximized():
            self.enter_after_fullscreen(window.normalGeometry(), True)
            window.showNormal()
            return
        self._normal_maximized = (
            window.isMaximized() if normal_maximized is None else normal_maximized
        )
        self._normal_geometry = QRect(
            normal_geometry
            if normal_geometry is not None
            else (window.normalGeometry() if self._normal_maximized else window.geometry())
        )
        self._normal_minimum = QSize(window.minimumSize())
        self._video_minimum = QSize(window.video_stack.minimumSize())
        self._video_policy = window.video_stack.sizePolicy()
        self._watch_native_window()
        self.active = True
        window.fullscreen.set_active(True)
        window.fullscreen.set_compact(True)
        window.video_stack.setMinimumSize(0, 0)
        window.video_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        window.setMinimumSize(self.MINIMUM_SIZE)
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
        self.cancel_pending()
        if self._closed or not self.active:
            return
        window = self._window
        window.fullscreen.set_compact(False)
        window.fullscreen.set_active(False)
        self._restore_constraints()
        self.active = False
        window.setGeometry(self._normal_geometry)
        if self._normal_maximized:
            window.showMaximized()
        self._update_button()

    def close(self):
        self.cancel_pending()
        if self._closed:
            return
        self._closed = True
        native, self._native_window = self._native_window, None
        if native is not None and isValid(native):
            native.removeEventFilter(self)
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
