"""Keyboard-only TV mode sharing the catalogue, player and video stack."""

import time
from collections import OrderedDict

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QWidget

from . import theme
from .channel_banner import ChannelBanner
from .media_controller import source_fingerprint
from .network import XtreamClient
from .tv_browser import TvBrowser
from .watching import NumberEntry

BACK_KEYS = (Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Back)
ENTER_KEYS = (Qt.Key_Return, Qt.Key_Enter)


class TvModeWindow(QWidget):
    """Own the video layout temporarily; never create or shut down a player."""

    closed = Signal()

    def __init__(self, host, *, screen_height=None):
        super().__init__(host, Qt.Window)
        self.host = host
        self._closed = False
        self.scale = (screen_height or host.screen().geometry().height()) / 1080
        self.tab, self.row = 0, -1
        self.rows, self.columns = [], []
        self.series = None
        self.playing = False
        self.background_audio = bool(host._playback_active)
        self.zap_ids = []
        self._requested = None
        self._back_key = None
        self._metadata = OrderedDict()
        self._metadata_pending = set()
        self._generation = 0
        self._series_pending = None
        self._native_window = None
        self.setWindowTitle("Luna · TV modu")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setScreen(host.screen())
        self.resize(round(1920 * self.scale), round(1080 * self.scale))
        self.browser = TvBrowser(self)
        self.banner = ChannelBanner(self)
        self.controls = QLabel("", self)
        self.controls.setTextFormat(Qt.PlainText)
        self.controls.setWordWrap(True)
        self.toast = QLabel("", self)
        self.toast.setTextFormat(Qt.PlainText)
        self.toast.setWordWrap(True)
        self.toast.hide()
        self.controls.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=4500)
        self._toast_timer.timeout.connect(self.toast.hide)
        self._controls_timer = QTimer(self, singleShot=True, interval=4500)
        self._controls_timer.timeout.connect(self.hide_controls)
        self._back_timer = QTimer(self, singleShot=True, interval=1000)
        self._back_timer.timeout.connect(self.close)
        self._clock = QTimer(self, interval=15000)
        self._clock.timeout.connect(self.refresh_programmes)
        self._clock.start()
        self._detail_timer = QTimer(self, singleShot=True, interval=250)
        self._detail_timer.timeout.connect(self.load_focused_details)
        self._refresh_timer = QTimer(self, singleShot=True, interval=0)
        self._refresh_timer.timeout.connect(self.refresh_rows)
        self.number_entry = NumberEntry(self)
        self.number_entry.typing.connect(lambda digits: self.notify(f"Kanal {digits}_"))
        self.number_entry.chosen.connect(self.jump_to_number)
        self._apply_scale()

        # Move the stack, preserving its video index and the welcome page. VideoWidget
        # already releases/recreates its mpv render context on Qt context destruction.
        stack = host.video_stack
        self._stack_parent = stack.parentWidget()
        self._stack_index = host.view_layout.indexOf(stack)
        self._stack_stretch = host.view_layout.stretch(self._stack_index)
        self._stack_minimum = stack.minimumSize()
        self._stack_policy = stack.sizePolicy()
        self._stack_hidden = stack.isHidden()
        self._host_visible = host.isVisible()
        host.watch_panel.suspend()
        host.number_entry.cancel()
        host.channel_banner.hide_banner()
        host.view_layout.removeWidget(stack)
        stack.setParent(self)
        stack.setMinimumSize(0, 0)
        stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        # Keep the surface alive underneath the opaque browser: a hidden, newly
        # reparented QOpenGLWidget cannot initialise the context for ongoing audio.
        stack.show()
        stack.lower()
        self.browser.raise_()
        host.hide()
        host.model.modelReset.connect(self._catalogue_changed)
        host.model.dataChanged.connect(self._queue_refresh)
        host.playback_started.connect(self.show_playback)
        QApplication.instance().installEventFilter(self)
        self.refresh_rows()
        if self.background_audio:
            self.notify("Ses sürüyor. Durdurmak için Geri'ye bas.")

    def _apply_scale(self):
        size = round(26 * self.scale)
        style = f"font-size: {size}px; color: {theme.TEXT}; background: {theme.DUSK};"
        self.controls.setStyleSheet(style + f"padding: {round(18 * self.scale)}px;")
        self.toast.setStyleSheet(style + f"padding: {round(20 * self.scale)}px;")
        self.banner.setStyleSheet(f"QLabel {{ font-size: {size}px; }}")
        self.banner.following.setMaximumWidth(round(600 * self.scale))
        self.banner.following.setWordWrap(True)
        self.banner.name.setWordWrap(True)
        self.banner.title.setWordWrap(True)
        self._layout_children()

    def _layout_children(self):
        if not hasattr(self, "browser"):
            return
        self.browser.setGeometry(self.rect())
        if self.host.video_stack.parentWidget() is self:
            self.host.video_stack.setGeometry(self.rect())
        margin = round(48 * self.scale)
        width = max(1, self.width() - margin * 2)
        control_height = round(96 * self.scale)
        self.controls.setGeometry(
            margin, self.height() - margin - control_height, width, control_height
        )
        self.banner.setFixedWidth(width)
        self.banner.adjustSize()
        self.banner.move(margin, self.controls.y() - self.banner.height() - round(18 * self.scale))
        self.toast.setGeometry(margin, margin, width, round(100 * self.scale))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def showEvent(self, event):
        super().showEvent(event)
        native = self.windowHandle()
        if native is not None and native is not self._native_window:
            self._native_window = native
            native.screenChanged.connect(self._screen_changed)
        self.setFocus()

    def _screen_changed(self, screen):
        if screen:
            self.scale = screen.geometry().height() / 1080
            self._apply_scale()
            self.browser.update()

    def _catalogue_changed(self):
        self._generation += 1
        self._metadata.clear()
        self._metadata_pending.clear()
        self._queue_refresh()

    def _queue_refresh(self, *_):
        if not self._closed:
            self._refresh_timer.start()

    def refresh_rows(self):
        if self._closed:
            return
        previous = self.focused_channel()
        previous_title = self.rows[self.row][0] if previous else None
        host = self.host
        channels = [
            c
            for c in host.model.channels
            if not (host.kids_profile() and c.id in host.model.locked)
            and (not host.proxy.source or c.id.startswith(host.proxy.source + ":"))
        ]
        self.rows = []
        if self.series:
            if host.kids_profile() and self.series.id in host.model.locked:
                self.series = None
            else:
                episodes = [
                    c
                    for c in channels
                    if c.kind == "movie"
                    and c.series_id == self.series.series_id
                    and c.id.split(":")[0] == self.series.id.split(":")[0]
                ]
                if episodes:
                    self.rows = [(self.series.name, episodes)]
        if self.series is None and self.tab < 5:
            by_id = {c.id: c for c in channels}
            if self.tab == 0:
                host.home_view.refresh()
                for key in ("continue", "favorite_live", "recent_live"):
                    home = host.home_view.rows[key]
                    items = [by_id[cid] for cid in home.model.ids if cid in by_id]
                    if items:
                        self.rows.append((home.title, items))
            kinds = ("live", "movie", "series", "live", None)
            groups = OrderedDict()
            for channel in channels:
                if self.tab == 4:
                    if channel.id not in host.model.favorites:
                        continue
                elif channel.kind != kinds[self.tab] or host.proxy.category_hidden(channel):
                    continue
                if channel.kind == "movie" and channel.series_id:
                    continue
                if self.tab == 3:
                    self.rows.append((channel.name, [channel]))
                else:
                    groups.setdefault(channel.group or "Diğer", []).append(channel)
            self.rows.extend(groups.items())
        self.columns = [0] * len(self.rows)
        self.row = min(self.row, len(self.rows) - 1)
        if previous and self.row >= 0:
            for ri, (title, items) in enumerate(self.rows):
                if title != previous_title:
                    continue
                ci = next((i for i, c in enumerate(items) if c.id == previous.id), None)
                if ci is not None:
                    self.row, self.columns[ri] = ri, ci
                    break
        self.focus_changed()

    def focused_channel(self):
        if 0 <= self.row < len(self.rows):
            return self.rows[self.row][1][self.columns[self.row]]
        return None

    def focus_changed(self):
        self.browser.focus_changed()
        if not self.playing:
            self._detail_timer.start()

    def now_next(self, channel):
        now = self.host.programme_now(channel)
        index = self.host._guide_index.get(channel.id.split(":", 1)[0])
        upcoming = index.upcoming(channel.tvg_id, 1) if index else []
        return now, upcoming[0] if upcoming else None

    def description(self, channel):
        if channel.id in self.host.model.locked:
            return "Kilitli içerik · Açmak için Enter ve PIN"
        if channel.kind == "live":
            now, following = self.now_next(channel)
            return "\n".join(
                (
                    f"Şimdi · {now.title}" if now else "Şimdi · Rehber bilgisi yok",
                    f"Sonra · {following.title}" if following else "Sonra · Rehber bilgisi yok",
                )
            )
        details = self._metadata.get(channel.id)
        return (
            details.info.get("description", "Konu bilgisi yok.") if details else "Konu bilgisi yok."
        )

    def load_focused_details(self):
        channel = self.focused_channel()
        if channel and channel.kind != "live" and channel.id not in self.host.model.locked:
            self._load_details(channel)

    def _load_details(self, channel, *, open_series=False):
        host = self.host
        source = host.source_for(channel)
        if not source or source["type"] != "xtream":
            if open_series:
                self.series = channel
                self.refresh_rows()
                self.row = 0 if self.rows else -1
                self.focus_changed()
                if not self.rows:
                    self.notify("Bu dizide henüz bölüm bulunmuyor.")
            return
        if open_series:
            self._series_pending = channel.id
        fingerprint = source_fingerprint(source)
        cached = host.store.media_details(channel.id, fingerprint)
        if channel.id in self._metadata or cached:
            details = self._metadata.get(channel.id) or cached[0]
            self._details_loaded(channel, details)
            return
        # One metadata worker at a time; when it finishes, service the latest focus.
        if self._metadata_pending:
            return
        self._metadata_pending.add(channel.id)
        generation, profile_id = self._generation, host.store.profile_id

        def valid():
            return (
                not self._closed
                and not host._closed
                and generation == self._generation
                and profile_id == host.store.profile_id
                and (fresh_source := host.source_for(channel)) is not None
                and source_fingerprint(fresh_source) == fingerprint
            )

        def loaded(details):
            if not self._closed and generation == self._generation:
                self._metadata_pending.discard(channel.id)
            if not valid():
                return
            host.store.save_media_details(channel.id, fingerprint, details, int(time.time()))
            self._details_loaded(channel, details)
            next_focus()

        def failed(_error):
            if not self._closed and generation == self._generation:
                self._metadata_pending.discard(channel.id)
            if valid():
                if self._series_pending == channel.id:
                    self._series_pending = None
                    self.notify("Bölümler alınamadı. Yeniden denemek için Enter.")
                next_focus()

        def next_focus():
            focused = self.focused_channel()
            if focused is not None and focused.id != channel.id:
                if focused.id == self._series_pending:
                    self._load_details(focused, open_series=True)
                else:
                    self._detail_timer.start()

        host.run_task(
            lambda: XtreamClient(
                source["location"], source["username"], source["password"]
            ).media_details(channel),
            loaded,
            "",
            busy=False,
            failure=failed,
        )
        if open_series:
            self.notify("Bölümler alınıyor…")

    def _details_loaded(self, channel, details):
        self._metadata[channel.id] = details
        self._metadata.move_to_end(channel.id)
        while len(self._metadata) > 128:
            self._metadata.popitem(last=False)
        if self._series_pending == channel.id:
            self._series_pending = None
            if self.host.kids_profile() and channel.id in self.host.model.locked:
                return
            self.series = channel
            if details.episodes:
                self.host.store.upsert_channels(channel.id.split(":", 1)[0], details.episodes)
                self.host.model.reset(
                    self.host.store.channels(), self.host.model.favorites, self.host.model.progress
                )
                self.host.apply_locks()
            self.refresh_rows()
            self.row = 0 if self.rows else -1
            self.focus_changed()
            if not self.rows:
                self.notify("Bu dizide henüz bölüm bulunmuyor.")
        self.browser.update()

    def activate(self):
        if self.row == -1:
            if self.tab == 5:
                self.close()
            elif self.rows:
                self.row = 0
                self.focus_changed()
            return
        channel = self.focused_channel()
        if channel is None:
            return
        if channel.kind == "series":
            if self.host.unlock_channel(channel) and not self._closed:
                self._load_details(channel, open_series=True)
            return
        self.play_channel(channel)

    def play_channel(self, channel):
        if self._closed:
            return
        if self.series and not self.host.unlock_channel(self.series):
            return
        if self._closed:
            return
        if self.tab == 3:
            self.zap_ids = [items[0].id for _, items in self.rows]
        elif self.row >= 0:
            self.zap_ids = [c.id for c in self.rows[self.row][1] if c.kind == "live"]
        self._request_play(channel)

    def _request_play(self, channel):
        self._requested = channel.id
        try:
            self.host.request_play(channel)
        finally:
            self._requested = None

    def show_playback(self, channel):
        if self._closed or (not self.playing and self._requested != channel.id):
            return
        self.playing = True
        self.background_audio = False
        self.browser.animation.stop()
        self._detail_timer.stop()
        self.browser.hide()
        self.host.video_stack.show()
        self.host.video_stack.lower()
        self.show_controls()
        self.setFocus()

    def show_controls(self):
        channel = self.host.current
        if not self.playing or channel is None:
            return
        now, following = self.now_next(channel)
        ids = [c.id for c in self.host.numbered_channels()]
        number = ids.index(channel.id) + 1 if channel.id in ids else None
        self.banner.set_content(channel, number, now, following)
        self.banner.show_for(None)
        seek = "←→ 10 sn sar" if channel.kind != "live" else "↑↓ Kanal değiştir"
        self.controls.setText(
            f"{seek}  ·  P Duraklat / Oynat  ·  M Ses  ·  Enter Gizle  ·  Geri Liste"
        )
        self.controls.show()
        self.controls.raise_()
        self._layout_children()
        self._controls_timer.start()

    def hide_controls(self):
        self._controls_timer.stop()
        self.banner.hide_banner()
        self.controls.hide()

    def refresh_programmes(self):
        if self.playing:
            if not self.controls.isHidden():
                self.show_controls()
        else:
            self.browser.update()

    def zap(self, step):
        current = self.host.current
        if not current or current.kind != "live":
            return
        channels = {c.id: c for c in self.host.model.channels}
        ids = [
            cid
            for cid in self.zap_ids
            if cid in channels
            and channels[cid].kind == "live"
            and cid not in self.host.model.locked
        ]
        if not ids:
            return
        start = ids.index(current.id) if current.id in ids else (-1 if step > 0 else 0)
        channel = channels[ids[(start + step) % len(ids)]]
        self._request_play(channel)

    def jump_to_number(self, number):
        if self._closed:
            return
        channels = self.host.numbered_channels()
        if not 1 <= number <= len(channels):
            self.notify(f"{number} numaralı kanal yok.")
            return
        channel = channels[number - 1]
        self.zap_ids = [c.id for c in channels]
        self._request_play(channel)

    def back(self):
        self.number_entry.cancel()
        self._series_pending = None
        if self.playing:
            self.playing = False
            self.background_audio = bool(self.host._playback_active)
            self.hide_controls()
            self.browser.show()
            self.browser.raise_()
            self.notify("Ses sürüyor. Durdurmak için yeniden Geri'ye bas.")
        elif self.background_audio:
            self.host.stop_playback()
            self.background_audio = False
            self.notify("Oynatma durduruldu.")
        elif self.series:
            self.series = None
            self.refresh_rows()
        elif self.row >= 0:
            self.row = -1
            self.focus_changed()
        else:
            self.close()

    def notify(self, text):
        if self._closed:
            return
        self.toast.setText(text)
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start()

    def eventFilter(self, watched, event):
        if self._closed or not isinstance(watched, QWidget) or watched.window() is not self:
            return False
        if event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.KeyPress:
            self.keyPressEvent(event)
            return True
        if event.type() == QEvent.KeyRelease:
            self.keyReleaseEvent(event)
            return True
        if event.type() == QEvent.WindowDeactivate:
            self._back_timer.stop()
            self._back_key = None
            self.number_entry.cancel()
        return False

    def keyPressEvent(self, event):
        key = event.key()
        event.accept()
        if key in BACK_KEYS:
            if not event.isAutoRepeat() and self._back_key is None:
                self._back_key = key
                self._back_timer.start()
            return
        if key == Qt.Key_F11:
            self.close()
        elif Qt.Key_0 <= key <= Qt.Key_9:
            self.number_entry.digit(key - Qt.Key_0)
        elif key in ENTER_KEYS and self.number_entry.digits:
            self.number_entry.commit()
        elif self.playing:
            if key in (Qt.Key_Up, Qt.Key_Down):
                self.zap(-1 if key == Qt.Key_Up else 1)
            elif key in (Qt.Key_Left, Qt.Key_Right) and self.host.current.kind != "live":
                self.host.transport.seek_relative(-10 if key == Qt.Key_Left else 10)
            elif key in ENTER_KEYS:
                self.show_controls() if self.controls.isHidden() else self.hide_controls()
            elif key == Qt.Key_P:
                self.host.toggle_play()
            elif key == Qt.Key_M:
                self.host.player.command(["cycle", "mute"])
        elif key in ENTER_KEYS:
            self.activate()
        elif key in (Qt.Key_Left, Qt.Key_Right):
            self._series_pending = None
            step = -1 if key == Qt.Key_Left else 1
            if self.row == -1:
                self.tab = (self.tab + step) % 6
                self.series = None
                self._series_pending = None
                self.refresh_rows()
            elif self.rows:
                self.columns[self.row] = max(
                    0, min(self.columns[self.row] + step, len(self.rows[self.row][1]) - 1)
                )
            self.focus_changed()
        elif key in (Qt.Key_Up, Qt.Key_Down):
            self._series_pending = None
            step = -1 if key == Qt.Key_Up else 1
            self.row = max(-1, min(self.row + step, len(self.rows) - 1))
            self.focus_changed()

    def keyReleaseEvent(self, event):
        if event.key() in BACK_KEYS and not event.isAutoRepeat():
            if self._back_key == event.key():
                self._back_timer.stop()
                self._back_key = None
                if not self._closed:
                    self.back()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self._closed = True
        QApplication.instance().removeEventFilter(self)
        for timer in (
            self._back_timer,
            self._clock,
            self._detail_timer,
            self._refresh_timer,
            self._toast_timer,
            self._controls_timer,
        ):
            timer.stop()
        self.number_entry.cancel()
        self.browser.animation.stop()
        self.host.model.modelReset.disconnect(self._catalogue_changed)
        self.host.model.dataChanged.disconnect(self._queue_refresh)
        self.host.playback_started.disconnect(self.show_playback)
        for cache in (self.host.logos, self.host.posters):
            cache.request_visible([], owner=self.browser)
            cache.ready.disconnect(self.browser.update)
        self.hide_controls()
        stack = self.host.video_stack
        stack.hide()
        stack.setParent(self._stack_parent)
        self.host.view_layout.insertWidget(self._stack_index, stack, self._stack_stretch)
        stack.setMinimumSize(self._stack_minimum)
        stack.setSizePolicy(self._stack_policy)
        stack.setVisible(not self._stack_hidden)
        self.closed.emit()
        if not self.host._closed:
            self.host.watch_panel.sync(animate=False)
            if self._host_visible:
                self.host.show()
                self.host.activateWindow()
        event.accept()
        self.deleteLater()
