"""Native Qt OpenGL/libmpv rendering, including on Wayland (no foreign window)."""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

_GL_BLEND = 0x0BE2


class Player(QObject):
    ready = Signal()
    property_changed = Signal(str, object)
    error = Signal(str)
    file_loaded = Signal()
    ended = Signal()
    playback_loaded = Signal(int)
    playback_property_changed = Signal(int, str, object)
    playback_finished = Signal(int, str, str)
    playback_tracking_lost = Signal(int)

    _HEALTH_PROPERTIES = {"time-pos", "pause", "paused-for-cache"}
    _MAX_TRACKED_ENTRIES = 16
    _MAX_PENDING_EVENTS = 64

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
        "seeking",
    )
    OPTIONAL_OBSERVED = (
        "partially-seekable",
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
        self._load_generation = 0
        self._latest_load_token = 0
        self._reserved_load_token: int | None = None
        self._entry_tokens: dict[int, int] = {}
        self._pending_entry_events: dict[int, list[tuple[str, tuple[Any, ...]]]] = {}
        self._current_entry_id: int | None = None
        self._untracked_token: int | None = None
        self._untracked_entry_id: int | None = None
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
            self._mpv.event_callback("start-file")(self._on_start)
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
        if self._closed:
            return
        name = str(name)
        self.property_changed.emit(name, value)
        if name in self._HEALTH_PROPERTIES and self._current_entry_id is not None:
            self._dispatch_entry_event(self._current_entry_id, "property", name, value)

    def _on_start(self, event):
        if self._closed:
            return
        detail = event.data
        entry_id = int(detail.playlist_entry_id)
        self._current_entry_id = entry_id
        if self._untracked_token == self._latest_load_token:
            self._untracked_entry_id = entry_id

    def _on_loaded(self, _event):
        if not self._closed and self._current_entry_id is not None:
            self._dispatch_entry_event(self._current_entry_id, "loaded")

    def _on_end(self, event):
        if self._closed:
            return
        detail = event.data
        entry_id = int(detail.playlist_entry_id) if detail is not None else self._current_entry_id
        if entry_id is None:
            return
        reason_value = int(detail.reason) if detail is not None else -1
        error_value = int(detail.error) if detail is not None else 0
        reasons = {0: "eof", 1: "restarted", 2: "stop", 3: "quit", 4: "error", 5: "redirect"}
        reason = "error" if error_value < 0 else reasons.get(reason_value, "unknown")
        message = "Yayın açılamadı veya bağlantı kesildi." if reason == "error" else ""
        self._dispatch_entry_event(entry_id, "finished", reason, message)
        if self._current_entry_id == entry_id:
            self._current_entry_id = None

    def _ready(self):
        self._render_ready = True
        self.ready.emit()
        if self._pending_load:
            args, self._pending_load = self._pending_load, None
            self._issue_load(*args)

    def reserve_load(self) -> int | None:
        if self._closed:
            return
        self._load_generation += 1
        token = self._load_generation
        self._latest_load_token = token
        # The old entry may finish before mpv starts this request. Do not let a
        # missing result ID attach the new request to that superseded entry.
        self._current_entry_id = None
        self._untracked_token = None
        self._untracked_entry_id = None
        self._pending_load = None
        self._reserved_load_token = token
        return token

    def load(
        self,
        url: str,
        headers: dict | None = None,
        start: float = 0,
        *,
        token: int | None = None,
    ):
        if token is None:
            token = self._reserved_load_token
            if token is None:
                token = self.reserve_load()
        self._reserved_load_token = None
        if self._closed or token is None or token != self._latest_load_token:
            return token
        if not url or "\x00" in url:
            self.error.emit("Geçerli bir yayın adresi seçin.")
            return token
        fields = []
        for name, value in (headers or {}).items():
            if any(c in str(name) + str(value) for c in "\r\n\x00") or ":" in str(name):
                self.error.emit("Yayın HTTP başlıkları geçersiz.")
                return token
            fields.append(f"{name}: {value}")
        if self._mpv is None:
            self._emit_entry_event(
                token,
                "finished",
                "engine",
                "Video motoru açılamadı.",
            )
            return token
        if not self._render_ready:
            self._pending_load = (url, fields, start, token)
            return token
        self._issue_load(url, fields, start, token)
        return token

    def _issue_load(self, url: str, fields: list[str], start: float, token: int):
        # Reset per-source headers for every load; escape mpv string-list separators.
        encoded = ",".join(v.replace("\\", "\\\\").replace(",", "\\,") for v in fields)
        options = (
            f"start={max(0, start)},http-header-fields=%{len(encoded.encode('utf-8'))}%{encoded}"
        )
        self.set_property("pause", False)
        if self._closed:
            return
        if self._mpv is None:
            self._emit_entry_event(
                token,
                "finished",
                "engine",
                "Video motoru açılamadı.",
            )
            return
        try:
            future = self._mpv.command_async("loadfile", url, "replace", -1, options)
            future.add_done_callback(lambda done: self._load_command_done(token, done))
        except (ValueError, RuntimeError, OSError):
            self._emit_entry_event(token, "finished", "error", "Yayın açılamadı.")

    def _load_command_done(self, token: int, future) -> None:
        if self._closed:
            return
        try:
            result = future.result()
        except Exception:
            self._emit_entry_event(token, "finished", "error", "Yayın açılamadı.")
            return
        try:
            entry_id = int(result["playlist_entry_id"])
            if entry_id <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, RuntimeError, OSError):
            self.playback_tracking_lost.emit(token)
            if token == self._latest_load_token:
                self._untracked_token = token
                self._untracked_entry_id = self._current_entry_id
                if self._untracked_entry_id is not None:
                    for kind, values in self._pending_entry_events.pop(
                        self._untracked_entry_id, []
                    ):
                        self._emit_untracked_event(kind, *values)
            return
        self._entry_tokens[entry_id] = token
        while len(self._entry_tokens) > self._MAX_TRACKED_ENTRIES:
            self._entry_tokens.pop(next(iter(self._entry_tokens)))
        for kind, values in self._pending_entry_events.pop(entry_id, []):
            self._emit_entry_event(token, kind, *values)

    def _dispatch_entry_event(self, entry_id: int, kind: str, *values: Any) -> None:
        token = self._entry_tokens.get(entry_id)
        if token is not None:
            self._emit_entry_event(token, kind, *values)
            if kind == "finished":
                self._entry_tokens.pop(entry_id, None)
            return
        if (
            entry_id == self._untracked_entry_id
            and self._untracked_token == self._latest_load_token
        ):
            self._emit_untracked_event(kind, *values)
            return
        events = self._pending_entry_events.setdefault(entry_id, [])
        events.append((kind, tuple(values)))
        while (
            sum(len(items) for items in self._pending_entry_events.values())
            > self._MAX_PENDING_EVENTS
        ):
            oldest = next(iter(self._pending_entry_events))
            self._pending_entry_events[oldest].pop(0)
            if not self._pending_entry_events[oldest]:
                self._pending_entry_events.pop(oldest)

    def _emit_entry_event(self, token: int, kind: str, *values: Any) -> None:
        if self._closed:
            return
        current = token == self._latest_load_token
        if kind == "property":
            self.playback_property_changed.emit(token, values[0], values[1])
        elif kind == "loaded":
            self.playback_loaded.emit(token)
            if current:
                self.file_loaded.emit()
        elif kind == "finished":
            reason, message = values
            if current:
                if message:
                    self.error.emit(
                        "Yayın açılamadı veya bağlantı kesildi. Kaynak adresini ve erişim bilgilerini kontrol edin."
                    )
                self.ended.emit()
            self.playback_finished.emit(token, reason, message)

    def _emit_untracked_event(self, kind: str, *values: Any) -> None:
        if self._closed:
            return
        if kind == "loaded":
            self.file_loaded.emit()
        elif kind == "finished":
            _reason, message = values
            if message:
                self.error.emit(
                    "Yayın açılamadı veya bağlantı kesildi. Kaynak adresini ve erişim bilgilerini kontrol edin."
                )
            self.ended.emit()

    def command(self, args: list[Any]):
        if self._closed or self._mpv is None or not args:
            return
        try:
            future = self._mpv.command_async(*args)
            future.add_done_callback(self._command_done)
            return future
        except (ValueError, RuntimeError, OSError):
            self.error.emit("Oynatıcı komutu uygulanamadı.")

    def _command_done(self, future):
        if not self._closed and future.exception():
            self.error.emit("Oynatıcı komutu uygulanamadı; yayın bu işlemi desteklemiyor olabilir.")

    def set_property(self, name: str, value: Any):
        if isinstance(value, bool):
            value = "yes" if value else "no"
        return self.command(["set", name, str(value)])

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
        self._entry_tokens.clear()
        self._pending_entry_events.clear()
        self._reserved_load_token = None
        self._current_entry_id = None
        self._untracked_token = None
        self._untracked_entry_id = None
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
            # Qt enables blending before paintGL; libmpv expects it disabled.
            # Otherwise subtitle blend factors leak into the next video frame.
            self.context().functions().glDisable(_GL_BLEND)
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
