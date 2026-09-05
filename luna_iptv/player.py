"""Native Qt OpenGL/libmpv rendering, including on Wayland (no foreign window)."""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget


class Player(QObject):
    ready = Signal()
    property_changed = Signal(str, object)
    error = Signal(str)
    file_loaded = Signal()
    ended = Signal()

    OBSERVED = (
        "time-pos",
        "duration",
        "pause",
        "volume",
        "mute",
        "track-list",
        "aid",
        "sid",
        "idle-active",
        "paused-for-cache",
        "cache-buffering-state",
        "media-title",
        "seekable",
    )
    OPTIONAL_OBSERVED = (
        "video-dec-params",
        "video-params",
        "video-frame-info/interlaced",
        "container-fps",
        "video-bitrate",
        "audio-bitrate",
        "audio-params",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mpv = None
        self._binding = None
        self._video = None
        self._render_ready = False
        self._pending_load = None
        self._closed = False
        self._termination = None
        try:
            import mpv

            self._binding = mpv
            self._mpv = mpv.MPV(
                vo="libmpv",
                hwdec="auto-safe",
                idle=True,
                keep_open=False,
                input_default_bindings=False,
                input_vo_keyboard=False,
                osc=False,
                ytdl=False,
                config=False,
                terminal=False,
            )
            for name in self.OBSERVED:
                self._mpv.observe_property(name, self._on_property)
            for name in self.OPTIONAL_OBSERVED:
                try:
                    self._mpv.observe_property(name, self._on_property)
                except (AttributeError, OSError, ValueError, RuntimeError):
                    # Older supported mpv builds may not expose every detail.
                    # Missing metadata must not disable playback.
                    continue
            self._mpv.event_callback("file-loaded")(self._on_loaded)
            self._mpv.event_callback("end-file")(self._on_end)
        except (ImportError, OSError, ValueError, RuntimeError):
            # Do not include provider URLs or credentials in player diagnostics.
            self._mpv = None
            QTimer.singleShot(
                0,
                lambda: (
                    None
                    if self._closed
                    else self.error.emit(
                        "Video motoru açılamadı. python-mpv ve libmpv2 paketlerini kurup uygulamayı yeniden açın."
                    )
                ),
            )

    def _on_property(self, name, value):
        if not self._closed:
            self.property_changed.emit(name, value)

    def _on_loaded(self, _event):
        if not self._closed:
            self.file_loaded.emit()

    def _on_end(self, event):
        if self._closed:
            return
        detail = event.data
        if detail is not None and (detail.reason == detail.ERROR or detail.error < 0):
            self.error.emit(
                "Yayın açılamadı veya bağlantı kesildi. Kaynak adresini ve erişim bilgilerini kontrol edin."
            )
        self.ended.emit()

    def _ready(self):
        self._render_ready = True
        self.ready.emit()
        if self._pending_load:
            args, self._pending_load = self._pending_load, None
            self.load(*args)

    def load(self, url: str, headers: dict | None = None, start: float = 0):
        if self._closed:
            return
        if not url or "\x00" in url:
            self.error.emit("Geçerli bir yayın adresi seçin.")
            return
        fields = []
        for name, value in (headers or {}).items():
            if any(c in str(name) + str(value) for c in "\r\n\x00") or ":" in str(name):
                self.error.emit("Yayın HTTP başlıkları geçersiz.")
                return
            fields.append(f"{name}: {value}")
        if not self._render_ready:
            self._pending_load = (url, headers, start)
            return
        # Reset per-source headers for every load; escape mpv string-list separators.
        encoded = ",".join(v.replace("\\", "\\\\").replace(",", "\\,") for v in fields)
        options = (
            f"start={max(0, start)},http-header-fields=%{len(encoded.encode('utf-8'))}%{encoded}"
        )
        self.set_property("pause", False)
        self.command(["loadfile", url, "replace", -1, options])

    def command(self, args: list[Any]):
        if self._closed or self._mpv is None or not args:
            return
        try:
            future = self._mpv.command_async(*args)
            future.add_done_callback(self._command_done)
        except (ValueError, RuntimeError, OSError):
            self.error.emit("Oynatıcı komutu uygulanamadı.")

    def _command_done(self, future):
        if not self._closed and future.exception():
            self.error.emit("Oynatıcı komutu uygulanamadı; yayın bu işlemi desteklemiyor olabilir.")

    def set_property(self, name: str, value: Any):
        if isinstance(value, bool):
            value = "yes" if value else "no"
        self.command(["set", name, str(value)])

    def pause_toggle(self):
        self.command(["cycle", "pause"])

    def stop(self):
        self._pending_load = None
        self.command(["stop"])

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._pending_load = None
        if self._video is not None:
            self._video.release_render_context()
        backend, self._mpv = self._mpv, None
        if backend is not None:
            # Network teardown can be slow: never wait for it on the GUI thread.
            self._termination = threading.Thread(target=backend.terminate, name="mpv-shutdown")
            self._termination.start()


class VideoWidget(QOpenGLWidget):
    """One widget per Player. Call player.shutdown() before destroying the window."""

    _frame_requested = Signal()

    def __init__(self, player: Player, parent=None):
        super().__init__(parent)
        self.player = player
        player._video = self
        self._render = None
        self._get_proc = None
        self._failed = False
        # Preserve Qt's negotiated EGL format; forcing a core profile after
        # QApplication exists can mismatch the window's shared context.
        self.setMinimumSize(160, 90)
        self._frame_requested.connect(self.update, Qt.ConnectionType.QueuedConnection)

    def showEvent(self, event):
        super().showEvent(event)
        # Qt may fail before initializeGL is invoked; surface that failure too.
        QTimer.singleShot(0, self._check_context)

    def _check_context(self):
        if self.player._closed:
            return
        if (
            self.isVisible()
            and self.player._mpv is not None
            and not self.isValid()
            and not self._failed
        ):
            self._failed = True
            self.player.error.emit(
                "OpenGL bağlamı açılamadı. Grafik sürücünüzü ve Qt Wayland desteğini kontrol edin."
            )

    def initializeGL(self):
        if self.player._mpv is None or self.player._closed:
            return
        try:
            binding = self.player._binding
            # Qt owns EGL/GLX resolution; this also works in a native Wayland surface.
            self._get_proc = binding.MpvGlGetProcAddressFn(
                lambda _ctx, name: QOpenGLContext.currentContext().getProcAddress(QByteArray(name))
            )
            self._render = binding.MpvRenderContext(
                self.player._mpv, "opengl", opengl_init_params={"get_proc_address": self._get_proc}
            )
            self._render.update_cb = self._frame_requested.emit
            self.context().aboutToBeDestroyed.connect(self.release_render_context)
            self.player._ready()
        except Exception:
            self._failed = True
            self.player.error.emit(
                "OpenGL video yüzeyi oluşturulamadı. Grafik sürücünüzü ve Qt Wayland desteğini kontrol edin."
            )

    def paintGL(self):
        if self._render is None:
            return
        try:
            self._render.update()
            ratio = self.devicePixelRatioF()
            self._render.render(
                opengl_fbo={
                    "fbo": self.defaultFramebufferObject(),
                    "w": max(1, round(self.width() * ratio)),
                    "h": max(1, round(self.height() * ratio)),
                },
                flip_y=True,
            )
        except Exception:
            if not self._failed:
                self._failed = True
                self.player.error.emit("Video karesi çizilemedi. Grafik sürücünüzü kontrol edin.")

    @Slot()
    def release_render_context(self):
        if self._render is None:
            return
        self.makeCurrent()
        render, self._render = self._render, None
        render.update_cb = None
        render.free()
        self.doneCurrent()
        self.player._render_ready = False
