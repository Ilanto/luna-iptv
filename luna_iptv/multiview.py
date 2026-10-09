"""Independent live tiles, each owning its video surface and mpv lifetime."""

from functools import partial

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .dialogs import text_label
from .idle_inhibit import IdleInhibit
from .layout import button
from .player import Player, VideoWidget


class MultiViewTile(QFrame):
    """Create the backend only when a channel arrives; release it before its surface."""

    _backend_event = Signal(object, int, str)

    def __init__(self, view, index):
        super().__init__(view)
        self.view = view
        self.index = index
        self.channel = None
        self.player = None
        self.video = None
        self.running = False
        self._generation = 0
        self._backend_event.connect(self._handle_event)
        self.setObjectName("multiTile")
        self.setMinimumSize(220, 150)
        self.setFocusPolicy(Qt.ClickFocus)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(3, 3, 3, 3)
        self.empty = text_label("Canlı kanalın menüsünden\nÇoklu izlemeye ekle", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.body.addWidget(self.empty)
        self.strip = QFrame(self)
        self.strip.setObjectName("channelBanner")
        row = QHBoxLayout(self.strip)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(8)
        labels = QVBoxLayout()
        labels.setSpacing(1)
        self.name_label = text_label("", "multiName")
        self.programme_label = text_label("", "muted")
        for label in (self.name_label, self.programme_label):
            label.setTextFormat(Qt.PlainText)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            labels.addWidget(label)
        row.addLayout(labels, 1)
        self.clear_button = button("×", self.clear, tip="Bu kutucuğu boşalt")
        self.clear_button.setFixedSize(28, 28)
        row.addWidget(self.clear_button)
        self.strip.installEventFilter(self)
        self.strip.hide()

    def set_channel(self, channel):
        self._generation += 1
        if self.player is None:
            self.player = Player(self)
            # Set before load, including mute: a new stream must never burst into sound.
            for name, value in (
                ("mute", True),
                ("hwdec", "auto-safe"),
                ("vd-lavc-threads", 2),
                ("cache-secs", 3),
                ("demuxer-max-bytes", 16 * 1024 * 1024),
            ):
                self.player.set_property(name, value)
            for signal, kind in (
                (self.player.file_loaded, "loaded"),
                (self.player.ended, "ended"),
                (self.player.error, "error"),
            ):
                # Stamp at emission, before Qt queues delivery to the GUI. Player
                # already filters superseded loads; the stamp also covers events
                # it emitted just before a replacement reused this handle.
                signal.connect(partial(self._queue_event, self.player, kind), Qt.DirectConnection)
            self.video = VideoWidget(self.player, self)
            self.video.installEventFilter(self)
            self.body.addWidget(self.video)
        self.channel = channel
        self.running = False
        self.empty.hide()
        self.setAccessibleName(f"{self.index + 1} · {channel.name}")
        self.refresh_programme()
        self.strip.show()
        self._place_strip()
        self.player.load(channel.url, channel.headers)
        self.view.sync_idle()

    def refresh_programme(self):
        if self.channel is None:
            return
        self.name_label.setText(self.channel.name)
        programme = self.view.host.programme_now(self.channel)
        self.programme_label.setText(programme.title if programme else "Program bilgisi yok")
        self.strip.setToolTip(f"{self.channel.name}\n{self.programme_label.text()}")

    def _queue_event(self, player, kind, *_):
        generation = self._generation
        if player is self.player and not self.view._closed:
            self._backend_event.emit(player, generation, kind)

    def _handle_event(self, player, generation, kind):
        if player is not self.player or generation != self._generation or self.channel is None:
            return
        self.running = kind == "loaded"
        if kind == "loaded":
            self.refresh_programme()
        elif kind == "error":
            # Backend messages may contain source details; keep the overlay generic.
            self.programme_label.setText("Yayın açılamadı. Kanalı yeniden ekleyebilirsin.")
        self.view.sync_idle()

    def clear(self):
        player, self.player = self.player, None
        video, self.video = self.video, None
        self.channel = None
        self.running = False
        if player is not None:
            # shutdown releases the GL render context synchronously, then terminates
            # mpv on its existing non-daemon worker. Never join it on the GUI thread.
            player.shutdown()
        if video is not None:
            self.body.removeWidget(video)
            video.hide()
            video.deleteLater()
        if player is not None:
            player.deleteLater()
        self.strip.hide()
        self.empty.show()
        self.setAccessibleName(f"{self.index + 1} · Boş kutucuk")
        self.view.sync_idle()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            self.view.focus_tile(self.index)
        return False

    def mousePressEvent(self, event):
        self.view.focus_tile(self.index)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_strip()

    def _place_strip(self):
        height = self.strip.sizeHint().height()
        self.strip.setGeometry(9, max(3, self.height() - height - 9), self.width() - 18, height)
        self.strip.raise_()


class MultiViewWindow(QWidget):
    """Two or four live channels with one audible focus and a separate fullscreen state."""

    closed = Signal()
    _mute_completed = Signal(int, bool)

    def __init__(self, host):
        super().__init__(host, Qt.Window)
        self.host = host
        self._closed = False
        self.tile_count = 2
        self.focused_index = 0
        self.muted = False
        self._audio_generation = 0
        self._pending_mutes = 0
        self._mute_failed = False
        self._mute_completed.connect(self._finish_mute)
        self.idle_inhibit = IdleInhibit()
        self.setWindowTitle("Çoklu izleme")
        self.resize(1000, 600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(text_label("Çoklu izleme", "title"))
        bar.addStretch()
        self.layout_buttons = {}
        for count, title in ((2, "1×2"), (4, "2×2")):
            control = button(title, lambda checked=False, n=count: self.set_layout(n))
            control.setCheckable(True)
            control.setAccessibleName(f"{count} kanal düzeni")
            self.layout_buttons[count] = control
            bar.addWidget(control)
        self.mute_button = button("Sessiz", self.toggle_mute, tip="Tüm sesi aç / kapat (M)")
        self.mute_button.setCheckable(True)
        bar.addWidget(self.mute_button)
        bar.addWidget(button("⛶", self.toggle_fullscreen, tip="Tam ekran (F)"))
        outer.addLayout(bar)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        outer.addLayout(self.grid, 1)
        self.tiles = [MultiViewTile(self, index) for index in range(4)]
        self.set_layout(2)
        for key, callback in (
            *[(str(i + 1), lambda i=i: self.focus_tile(i)) for i in range(4)],
            ("F", self.toggle_fullscreen),
            ("M", self.toggle_mute),
            ("Escape", self.leave_fullscreen),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self._programme_timer = QTimer(self)
        self._programme_timer.setInterval(15000)
        self._programme_timer.timeout.connect(self.refresh_programmes)
        self._programme_timer.start()

    def add_channel(self, channel):
        """Authorize every addition, including replacements and repeated channels."""
        if self._closed or self.host._closed or channel.kind != "live" or not channel.url:
            return False
        profile_id = self.host.store.profile_id
        if not self.host.unlock_channel(channel):
            return False
        # A modal PIN dialog can process a profile switch or close in its event loop.
        if self._closed or self.host._closed or profile_id != self.host.store.profile_id:
            return False
        index = next(
            (i for i, tile in enumerate(self.tiles[: self.tile_count]) if tile.channel is None),
            self.focused_index,
        )
        self.tiles[index].set_channel(channel)
        self.focus_tile(index)
        source = self.host.source_for(channel)
        if source and source.get("epg_url") and source["id"] not in self.host._guide_data:
            self.host.load_guide(source)
        return True

    def set_layout(self, count):
        if self._closed or count not in (2, 4):
            return
        self.tile_count = count
        for index, tile in enumerate(self.tiles):
            if index >= count:
                tile.clear()
            self.grid.removeWidget(tile)
            if index < count:
                self.grid.addWidget(tile, index // 2, index % 2)
            tile.setVisible(index < count)
        for index in range(2):
            self.grid.setColumnStretch(index, 1)
            self.grid.setRowStretch(index, 1 if index == 0 or count == 4 else 0)
        for value, control in self.layout_buttons.items():
            control.setChecked(value == count)
        self.focus_tile(min(self.focused_index, count - 1))

    def focus_tile(self, index):
        if self._closed or not 0 <= index < self.tile_count:
            return
        self.focused_index = index
        for i, tile in enumerate(self.tiles):
            tile.setProperty("focused", i == index)
            tile.style().unpolish(tile)
            tile.style().polish(tile)
            tile.update()
        self._route_audio()

    def _route_audio(self):
        # Handles run independently: wait for every mute acknowledgement before
        # opening the chosen audio, without ever blocking the GUI thread.
        self._audio_generation += 1
        generation = self._audio_generation
        pending = [
            tile.player.set_property("mute", True) for tile in self.tiles if tile.player is not None
        ]
        pending = [future for future in pending if future is not None]
        self._pending_mutes = len(pending)
        self._mute_failed = False
        for future in pending:
            if future.done():
                self._finish_mute(generation, future.exception() is None)
            else:
                future.add_done_callback(partial(self._mute_done, generation))

    def _mute_done(self, generation, future):
        if not self._closed:
            self._mute_completed.emit(
                generation, not future.cancelled() and future.exception() is None
            )

    def _finish_mute(self, generation, success):
        if self._closed or generation != self._audio_generation:
            return
        self._pending_mutes -= 1
        self._mute_failed = self._mute_failed or not success
        if self._pending_mutes or self._mute_failed or self.muted:
            return
        focused = self.tiles[self.focused_index]
        if focused.player is not None:
            focused.player.set_property("mute", False)

    def toggle_mute(self):
        self.muted = not self.muted
        self.mute_button.setChecked(self.muted)
        self._route_audio()

    def refresh_programmes(self):
        if not self._closed:
            for tile in self.tiles:
                tile.refresh_programme()

    def sync_idle(self):
        self.idle_inhibit.set_active(not self._closed and any(t.running for t in self.tiles))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def leave_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self._programme_timer.stop()
            for tile in self.tiles:
                tile.clear()
            self.idle_inhibit.close()
            self.closed.emit()
            self.deleteLater()
        event.accept()
