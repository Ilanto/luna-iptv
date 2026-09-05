from __future__ import annotations

import gzip
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
)
from shiboken6 import isValid

from . import __version__
from .accounts import sanitize_profile
from .dialogs import AccountDialog, GuideDialog, SourceDialog
from .epg import now_next, parse_xmltv
from .fullscreen import FullscreenController
from .layout import build_window
from .library import ChannelFilter, ChannelModel
from .media_info import MediaInfo
from .models import Channel, Playlist
from .network import LIMIT, NetworkError, XtreamClient, channel_id, fetch, load_m3u
from .player import Player
from .tasks import Task
from .transport import TransportController


def clock_text(seconds):
    seconds = int(max(0, seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class MainWindow(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.store = store
        self.current = None
        self._position = 0.0
        self._duration = 0.0
        self._seekable = False
        self._closed = False
        self._busy = False
        self._tasks = set()
        self._retry = None
        self._guide_data = {}
        self._episodes_title = ""
        self._tracks = []
        self._fullscreen = False
        self._last_saved = 0.0
        self._loading = False
        self._idle = True
        self._account_dialog = None
        self.media_info = MediaInfo()
        self.setWindowTitle("Luna IPTV")
        self.resize(1340, 850)
        self.setMinimumSize(1040, 690)
        self.setAcceptDrops(True)
        self.model = ChannelModel(self)
        self.proxy = ChannelFilter(self)
        self.proxy.setSourceModel(self.model)
        self.player = Player(self)
        self.transport = TransportController(self.player, self)
        build_window(self)
        self.fullscreen = FullscreenController(self, self.view_layout, self.player_header)
        self.transport.changed.connect(self.refresh_transport)
        self.refresh_transport()
        self.player.property_changed.connect(self.player_property)
        self.player.error.connect(self.playback_error)
        self.player.file_loaded.connect(self.loaded)
        self.player.ended.connect(self.ended)
        self.player.ready.connect(
            lambda: self.engine_label.setText("mpv  ·  " + QApplication.platformName())
        )
        self.refresh_library()
        self._guide_timer = QTimer(self)
        self._guide_timer.setInterval(30000)
        self._guide_timer.timeout.connect(self.update_guide)
        self._guide_timer.start()
        QTimer.singleShot(0, self.load_cached_guides)

    def status(self, message, retry=None):
        self.message.setText(message)
        self._retry = retry
        self.retry_button.setVisible(retry is not None)

    def retry(self):
        if self._retry:
            self._retry()

    def run_task(self, function, success, message, retry=None, busy=True, failure=None):
        if busy and self._busy:
            return
        if busy:
            self._busy = True
            self.add_button.setEnabled(False)
        if message:
            self.status(message)
        task = Task(function)
        self._tasks.add(task)

        def finish():
            self._tasks.discard(task)
            # Release Qt connection closures on their owning GUI thread, even
            # when the window closed while its network request was in flight.
            task.signals.deleteLater()
            if busy:
                self._busy = False
                if not self._closed:
                    self.add_button.setEnabled(True)

        def done(result):
            finish()
            if self._closed:
                return
            try:
                success(result)
            except Exception:
                self.status(
                    "Veri kaydedilemedi. Disk alanını ve dosya izinlerini kontrol edin.", retry
                )

        def failed(error):
            finish()
            if self._closed:
                return
            if failure is None:
                self.status(error, retry)
            else:
                failure(error)

        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        QThreadPool.globalInstance().start(task)

    def add_source(self, checked=False, location=""):
        if self._busy:
            return
        dialog = SourceDialog(self, location)
        if dialog.exec() == QDialog.Accepted:
            self.import_source(dialog.source())

    def import_source(self, source):
        source = dict(source)

        def load():
            if source["type"] == "xtream":
                return XtreamClient(
                    source["location"], source["username"], source["password"]
                ).catalog()
            if source["type"] == "direct":
                location = source["location"]
                scheme = urlsplit(location).scheme
                if scheme not in ("http", "https", "rtsp", "rtp", "udp", "file", ""):
                    raise NetworkError("Bu yayın protokolü desteklenmiyor.")
                if not scheme:
                    path = Path(location).expanduser().resolve()
                    if not path.is_file():
                        raise NetworkError("Video dosyası bulunamadı.")
                    location = path.as_uri()
                kind = (
                    "movie"
                    if scheme in ("", "file")
                    or urlsplit(location)
                    .path.lower()
                    .endswith((".mp4", ".mkv", ".webm", ".mov", ".avi"))
                    else "live"
                )
                return Playlist(
                    [
                        Channel(
                            channel_id(location),
                            source["name"],
                            location,
                            group="Tek yayın",
                            kind=kind,
                        )
                    ],
                    [],
                    [],
                )
            return load_m3u(source["location"])

        self.run_task(
            load,
            lambda result: self.accept_import(source, result),
            "Kaynak okunuyor…",
            lambda: self.import_source(source),
        )

    def accept_import(self, source, playlist):
        if not playlist.channels:
            self.status("Bu kaynakta oynatılabilir yayın bulunamadı. Önceki liste korundu.")
            return
        source = dict(source)
        if not source.get("epg_url") and playlist.epg_urls:
            source["epg_url"] = playlist.epg_urls[0]
        source_id = self.store.save_source(source)
        source["id"] = source_id
        if playlist.account_profile is not None:
            self.store.save_account_profile(source_id, playlist.account_profile)
        channels = list(playlist.channels)
        # A catalog has series parents only: retain cached episodes of surviving series.
        if source["type"] == "xtream":
            series_ids = {c.series_id for c in channels if c.kind == "series"}
            channels.extend(
                c
                for c in self.store.channels(source_id)
                if c.kind != "series" and c.series_id in series_ids
            )
        incoming = {
            c.id if c.id.startswith(source_id + ":") else source_id + ":" + c.id for c in channels
        }
        if self.current and self.current.id.startswith(source_id + ":"):
            self.save_progress()
            if self.current.id not in incoming:
                self.player.stop()
                self.current = None
                self._loading = False
                self.favorite_button.setEnabled(False)
                self.video_stack.setCurrentIndex(0)
                self.video_title.setText("İyi bir yayına yer aç.")
        self.store.replace_channels(source_id, channels)
        self.refresh_library(select_source=source_id)
        kind = playlist.channels[0].kind
        self.set_section(kind if kind in ("live", "movie", "series") else "live")
        detail = f"{len(playlist.channels)} yayın hazır."
        if playlist.warnings:
            detail += f" {len(playlist.warnings)} geçersiz satır atlandı."
        self.status(detail)
        if source.get("epg_url"):
            self.load_guide(source)

    def refresh_library(self, select_source=None):
        previous = select_source if select_source is not None else self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("Tüm kaynaklar", "")
        for source in self.store.sources():
            self.source_combo.addItem(source["name"], source["id"])
        index = self.source_combo.findData(previous)
        self.source_combo.setCurrentIndex(max(index, 0))
        self.source_combo.blockSignals(False)
        self.proxy.source = self.source_combo.currentData() or ""
        self.model.reset(self.store.channels(), self.store.favorites())
        self.proxy.set_recent_ids(self.store.recent_ids())
        if self.current:
            self.current = next(
                (c for c in self.model.channels if c.id == self.current.id), self.current
            )
            self.video_title.setText(self.current.name)
            self.update_guide()
        self.refresh_categories()
        self.filter_changed()
        has_channels = bool(self.model.channels)
        self.welcome_title.setText("Yayının hazır." if has_channels else "Ekran senin.")
        self.welcome_subtitle.setText(
            "Soldan bir yayın seç.\nİzleme alanın burada."
            if has_channels
            else "Kendi listeni ekle. Sevdiğin yayını seç.\nGerisini Luna’ya bırak."
        )
        self.welcome_action.setText("Başka kaynak ekle" if has_channels else "İlk kaynağını ekle")

    def set_section(self, section):
        self.proxy.section = section
        self.proxy.episode_ids = None
        self.back_button.hide()
        self._episodes_title = ""
        for key, b in self.nav_buttons.items():
            b.setChecked(key == section)
        self.section_title.setText(
            {
                "live": "Canlı TV",
                "movie": "Filmler",
                "series": "Diziler",
                "favorites": "Favoriler",
                "recent": "Son izlenenler",
            }[section]
        )
        self.search.clear()
        self.proxy.set_recent_ids(self.store.recent_ids())
        self.refresh_categories()
        self.filter_changed()

    def source_changed(self):
        self.proxy.source = self.source_combo.currentData() or ""
        self.proxy.episode_ids = None
        self.back_button.hide()
        self.refresh_categories()
        self.filter_changed()

    def refresh_categories(self):
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("Tüm kategoriler", "")
        groups = {
            c.group
            for c in self.model.channels
            if c.group
            and (not self.proxy.source or c.id.startswith(self.proxy.source + ":"))
            and (
                (self.proxy.episode_ids is not None and c.id in self.proxy.episode_ids)
                or (
                    self.proxy.episode_ids is None
                    and (
                        self.proxy.section in ("favorites", "recent")
                        or c.kind == self.proxy.section
                    )
                )
            )
        }
        for group in sorted(groups, key=str.casefold):
            self.category.addItem(group, group)
        self.category.blockSignals(False)

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().casefold().strip()
        self.proxy.group = self.category.currentData() or ""
        self.proxy.refresh()
        count = self.proxy.rowCount()
        self.count_label.setText(f"{count:,} yayın".replace(",", "."))
        self.no_results.setVisible(count == 0)
        self.channel_list.setVisible(count > 0)
        self.no_results.setText(
            "Aramana uygun yayın yok.\nFiltreleri değiştirebilirsin."
            if self.model.channels
            else "Henüz yayın yok.\nBir kaynak ekleyerek başla."
        )

    def activate_index(self, index):
        channel = index.data(Qt.UserRole)
        if not channel:
            return
        if channel.kind == "series":
            self.open_series(channel)
        else:
            self.play(channel)

    def source_for(self, channel):
        prefix = channel.id.split(":", 1)[0]
        return next((s for s in self.store.sources() if s["id"] == prefix), None)

    def open_series(self, channel):
        if self._busy:
            return
        source = self.source_for(channel)
        if not source or source["type"] != "xtream":
            self.status("Bu kaynağın dizi bölüm bilgisi bulunmuyor.")
            return

        def loaded(episodes):
            if not any(s["id"] == source["id"] for s in self.store.sources()):
                return
            if not episodes:
                self.status("Bu dizide henüz bölüm bulunmuyor.")
                return
            self.store.upsert_channels(source["id"], episodes)
            self.model.reset(self.store.channels(), self.store.favorites())
            self.proxy.episode_ids = {source["id"] + ":" + c.id for c in episodes}
            self._episodes_title = channel.name
            self.section_title.setText("Bölümler")
            self.back_button.show()
            self.search.clear()
            self.refresh_categories()
            self.filter_changed()
            self.status(channel.name)

        self.run_task(
            lambda: XtreamClient(
                source["location"], source["username"], source["password"]
            ).episodes(channel.series_id),
            loaded,
            "Bölümler alınıyor…",
            lambda: self.open_series(channel),
        )

    def play(self, channel):
        if self.current and self.current.id == channel.id and self._loading:
            return
        self.save_progress()
        self.current = channel
        self._position = 0.0
        self._duration = 0.0
        self._seekable = False
        self._last_saved = 0.0
        self._loading = True
        self.transport.prepare(live=channel.kind == "live")
        self.media_info.begin_load()
        self.refresh_media_info()
        self.info_button.setEnabled(True)
        self.video_title.setText(channel.name)
        self.video_badge.setText(
            "CANLI YAYIN" if channel.kind == "live" else channel.group.upper() or "FİLM / VİDEO"
        )
        self.favorite_button.setEnabled(True)
        self.favorite_button.setText("★" if channel.id in self.store.favorites() else "☆")
        self.video_stack.setCurrentIndex(1)
        self.seek.setEnabled(False)
        self.time_label.setText("Bağlanıyor…")
        self.update_guide()
        position, duration = self.store.progress(channel.id)
        start = (
            position
            if channel.kind != "live" and position > 5 and duration > 0 and position < duration - 10
            else 0
        )
        self.player.load(channel.url, channel.headers, start=start)
        self.status("Yayın açılıyor…", lambda: self.play(self.current) if self.current else None)
        source = self.source_for(channel)
        if source and source.get("epg_url") and source["id"] not in self._guide_data:
            self.load_guide(source)

    def loaded(self):
        if self._closed:
            return
        self._idle = False
        self._loading = False
        self.transport.loaded()
        self.media_info.mark_loaded()
        self.refresh_media_info()
        self.info_button.setEnabled(self.current is not None)
        self.status("Yayın oynatılıyor.")
        self.player.set_property("volume", self.volume.value())
        if self.current:
            self.store.save_progress(self.current.id, self._position, self._duration)

    def playback_error(self, message):
        if self._closed:
            return
        self._idle = True
        self._loading = False
        self.transport.finished()
        self.media_info.reset()
        self.refresh_media_info()
        self.fullscreen.set_info_visible(False)
        self.info_button.setEnabled(False)
        self.status(message, lambda: self.play(self.current) if self.current else None)

    def ended(self):
        if self._closed:
            return
        if not self._loading:
            self.transport.finished()
            self.play_button.setText("▶")
            self.save_progress()
            self.media_info.reset()
            self.refresh_media_info()
            self.fullscreen.set_info_visible(False)
            self.info_button.setEnabled(False)

    def player_property(self, name, value):
        if self._closed:
            return
        self.transport.observe(name, value)
        if self.media_info.update(name, value):
            self.refresh_media_info()
        if name == "idle-active" and value and not self._loading:
            self.fullscreen.set_info_visible(False)
            self.info_button.setEnabled(False)
        if name == "time-pos" and value is not None:
            self._position = float(value)
            if not self.seek.isSliderDown() and self._duration > 0:
                self.seek.setValue(round(1000 * self._position / self._duration))
            self.time_label.setText(
                clock_text(self._position)
                + (f" / {clock_text(self._duration)}" if self._duration > 0 else "  ·  CANLI")
            )
            if abs(self._position - self._last_saved) >= 5:
                self.save_progress()
                self._last_saved = self._position
        elif name == "duration" and value is not None and float(value) > 0:
            self._duration = float(value)
            self.seek.setEnabled(self._seekable and self._duration > 0)
        elif name == "seekable":
            self._seekable = bool(value)
            self.seek.setEnabled(self._seekable and self._duration > 0)
        elif name == "pause":
            self.play_button.setText("▶" if value else "Ⅱ")
        elif name == "mute":
            self.mute_button.setText("Sessiz" if value else "Ses")
        elif name == "volume" and value is not None:
            self.volume.blockSignals(True)
            self.volume.setValue(round(value))
            self.volume.blockSignals(False)
        elif name == "track-list":
            self._tracks = value or []
        elif name == "idle-active":
            self._idle = bool(value)
        elif name == "paused-for-cache" and value:
            self.status("Yayın arabelleğe alınıyor…")
        elif (
            name == "paused-for-cache"
            and value is False
            and self.current
            and not self._loading
            and not self._idle
        ):
            self.status("Yayın oynatılıyor.")

    def save_progress(self):
        if self.current and not self._closed:
            self.store.save_progress(self.current.id, self._position, self._duration)

    def seek_to_slider(self):
        if self._duration > 0 and self._seekable:
            self.transport.cancel(restore_pause=True)
            self.player.command(["seek", self.seek.value() * self._duration / 1000, "absolute"])

    def toggle_play(self):
        if self.current and self._idle:
            self.play(self.current)
        elif self.current and self.transport.rate:
            self.transport.normal_play()
        elif self.current:
            self.player.pause_toggle()

    def refresh_transport(self):
        if self._closed:
            return
        for widget in (self.seek_back_button, self.seek_forward_button):
            widget.setEnabled(self.transport.can_seek)
        for widget in (self.rewind_button, self.forward_button):
            widget.setEnabled(self.transport.can_scan)
        self.rewind_button.setChecked(self.transport.rate < 0)
        self.forward_button.setChecked(self.transport.rate > 0)
        self.rate_button.setText(self.transport.label)
        self.rate_button.setEnabled(bool(self.transport.rate))
        if self.transport.rate:
            self.play_button.setText("▶")

    def stop_playback(self):
        self.save_progress()
        self.transport.finished()
        self._idle = True
        self._loading = False
        self.media_info.reset()
        self.refresh_media_info()
        self.fullscreen.set_info_visible(False)
        self.info_button.setEnabled(False)
        self.player.stop()
        self.status("Yayın durduruldu.")
        self.seek.setEnabled(False)

    def toggle_info_panel(self):
        self.fullscreen.toggle_info()

    def refresh_media_info(self):
        fields = {
            self.info_dimensions: self.media_info.dimensions,
            self.info_quality: self.media_info.quality,
            self.info_video_codec: self.media_info.video_codec,
            self.info_audio_codec: self.media_info.audio_codec,
            self.info_audio_layout: self.media_info.audio_layout,
            self.info_fps: self.media_info.fps,
            self.info_bitrate: self.media_info.bitrate,
            self.info_dynamic_range: self.media_info.dynamic_range,
        }
        for label, value in fields.items():
            if label.text() != value:
                label.setText(value)
        for label, kind in ((self.info_video_codec, "video"), (self.info_audio_codec, "audio")):
            description = self.media_info.codec_description(kind)
            label.setToolTip(description)
            label.setAccessibleDescription(description)
        buffer_text = self.media_info.buffer_text
        self.buffer_label.setText(buffer_text)
        self.buffer_label.setVisible(bool(buffer_text))

    def toggle_favorite(self):
        if not self.current:
            return
        favorite = self.current.id not in self.store.favorites()
        self.store.set_favorite(self.current.id, favorite)
        self.model.favorites = self.store.favorites()
        if self.model.rowCount():
            self.model.dataChanged.emit(
                self.model.index(0), self.model.index(self.model.rowCount() - 1)
            )
        self.favorite_button.setText("★" if favorite else "☆")
        self.filter_changed()

    def track_menu(self):
        menu = QMenu(self)
        for mode, title in [("audio", "Ses parçaları"), ("sub", "Altyazılar")]:
            sub = menu.addMenu(title)
            off = sub.addAction("Kapalı")
            off.triggered.connect(
                lambda checked=False, m=mode: self.player.set_property(
                    "aid" if m == "audio" else "sid", "no"
                )
            )
            for track in self._tracks:
                if track.get("type") != mode:
                    continue
                label = track.get("title") or track.get("lang") or f"Parça {track.get('id', '')}"
                action = sub.addAction(str(label))
                action.setCheckable(True)
                action.setChecked(bool(track.get("selected")))
                action.triggered.connect(
                    lambda checked=False, m=mode, i=track["id"]: self.player.set_property(
                        "aid" if m == "audio" else "sid", i
                    )
                )
        menu.exec(self.cursor().pos())

    def source_menu(self):
        menu = self.build_source_menu()
        menu.exec(self.cursor().pos())

    def build_source_menu(self):
        source_id = self.source_combo.currentData()
        source = next((s for s in self.store.sources() if s["id"] == source_id), None)
        menu = QMenu(self)
        rename = menu.addAction("Seçili kaynağı yeniden adlandır")
        rename.setEnabled(source is not None and not self._busy)
        rename.triggered.connect(lambda: self.rename_source(source))
        refresh = menu.addAction("Seçili kaynağı yenile")
        refresh.setEnabled(source is not None and not self._busy)
        refresh.triggered.connect(lambda: self.import_source(source))
        if source is not None and source["type"] == "xtream":
            account = menu.addAction("Hesap durumu")
            account.triggered.connect(lambda: self.open_account(source))
        remove = menu.addAction("Seçili kaynağı kaldır")
        remove.setEnabled(source is not None and not self._busy)
        remove.triggered.connect(lambda: self.remove_source(source))
        menu.addSeparator()
        menu.addAction("Kısayollar ve hakkında", self.about)
        return menu

    def open_account(self, source):
        if source is None or source.get("type") != "xtream":
            return None
        if self._account_dialog is not None:
            if isValid(self._account_dialog):
                self._account_dialog.close()
            self._account_dialog = None
        dialog = AccountDialog(source["name"], self.store.account_profile(source["id"]), self)
        dialog.source_id = source["id"]
        self._account_dialog = dialog

        def closed(*_args):
            if self._account_dialog is dialog:
                self._account_dialog = None

        dialog.closed.connect(closed)
        dialog.destroyed.connect(closed)
        dialog.refresh_requested.connect(lambda: self.refresh_account(source, dialog))
        dialog.show()
        self.refresh_account(source, dialog)
        return dialog

    def refresh_account(self, source, dialog):
        if dialog.is_refreshing or not dialog.accepts_updates:
            return
        dialog.set_refreshing(True)

        def current_dialog():
            if self._account_dialog is not dialog or not isValid(dialog):
                return False
            return dialog.accepts_updates and any(
                item["id"] == source["id"] for item in self.store.sources()
            )

        def refreshed(profile):
            if not current_dialog():
                return
            try:
                profile = sanitize_profile(profile)
                self.store.save_account_profile(source["id"], profile)
                dialog.render(profile)
            except Exception:
                if current_dialog():
                    dialog.show_error("Hesap profili güvenli biçimde işlenemedi.")
            finally:
                if current_dialog():
                    dialog.set_refreshing(False)

        def failed(message):
            if not current_dialog():
                return
            try:
                dialog.show_error(message)
            finally:
                if current_dialog():
                    dialog.set_refreshing(False)

        self.run_task(
            lambda: XtreamClient(
                source["location"], source["username"], source["password"]
            ).account_info(),
            refreshed,
            None,
            busy=False,
            failure=failed,
        )

    def rename_source(self, source):
        if source is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Kaynağı yeniden adlandır", "Kaynak adı", QLineEdit.Normal, source["name"]
        )
        if not accepted:
            return
        try:
            renamed = self.store.rename_source(source["id"], name)
        except ValueError as error:
            self.status(str(error))
            return
        if not renamed:
            self.status("Kaynak artık mevcut değil.")
            return
        index = self.source_combo.findData(source["id"])
        if index >= 0:
            self.source_combo.setItemText(index, name.strip())
        self.status("Kaynak adı güncellendi.")

    def remove_source(self, source):
        if (
            QMessageBox.question(
                self,
                "Kaynağı kaldır",
                f"“{source['name']}” ve bu kaynağın favorileri kaldırılsın mı?",
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        if self.current and self.current.id.startswith(source["id"] + ":"):
            self.stop_playback()
            self.current = None
            self.favorite_button.setEnabled(False)
            self.video_stack.setCurrentIndex(0)
            self.video_title.setText("İyi bir yayına yer aç.")
        account_dialog = self._account_dialog
        if account_dialog is not None:
            if not isValid(account_dialog):
                self._account_dialog = None
            elif getattr(account_dialog, "source_id", None) == source["id"]:
                account_dialog.close()
        self.store.remove_source(source["id"])
        self._guide_data.pop(source["id"], None)
        (self.store.path.parent / f"epg-{source['id']}.xml").unlink(missing_ok=True)
        self.refresh_library()
        self.status("Kaynak kaldırıldı.")

    def configure_guide(self):
        source_id = self.source_combo.currentData()
        source = next((s for s in self.store.sources() if s["id"] == source_id), None)
        if source is None and self.current:
            source = self.source_for(self.current)
        if source is None:
            self.status("Önce soldan bir kaynak seç. Rehber o kaynağa bağlanacak.")
            return
        dialog = GuideDialog(source.get("epg_url", ""), self)
        if dialog.exec() == QDialog.Accepted:
            source["epg_url"] = dialog.location.text().strip()
            self.store.save_source(source)
            if source["epg_url"]:
                self.load_guide(source)

    def load_cached_guides(self):
        if self._closed:
            return
        for source in self.store.sources():
            path = self.store.path.parent / f"epg-{source['id']}.xml"
            if path.exists():
                self.load_guide(source, cached=True)

    def load_guide(self, source, cached=False):
        path = self.store.path.parent / f"epg-{source['id']}.xml"

        def read():
            location = str(path) if cached else source.get("epg_url", "")
            if urlsplit(location).scheme in ("http", "https"):
                raw = fetch(location)
            else:
                from urllib.request import url2pathname

                filename = (
                    url2pathname(urlsplit(location).path)
                    if location.startswith("file:")
                    else location
                )
                with Path(filename).expanduser().open("rb") as stream:
                    raw = stream.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise NetworkError("Rehber boyut sınırını aşıyor.")
                if raw.startswith(b"\x1f\x8b"):
                    import io

                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(LIMIT + 1)
                    if len(raw) > LIMIT:
                        raise NetworkError("Açılmış rehber boyut sınırını aşıyor.")
            return raw, parse_xmltv(raw)

        def done(result):
            if not any(s["id"] == source["id"] for s in self.store.sources()):
                return
            raw, programmes = result
            self._guide_data[source["id"]] = programmes
            if not cached:
                import os

                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
            self.update_guide()
            self.status(f"Program rehberi hazır · {len(programmes):,} program.".replace(",", "."))

        self.run_task(
            read, done, "Program rehberi okunuyor…", lambda: self.load_guide(source), busy=False
        )

    def update_guide(self):
        if self._closed:
            return
        if not self.current:
            return
        if self.current.kind != "live":
            self.now_title.setText("Kaldığın yer hatırlanır.")
            self.next_title.setText("Ses ve altyazı parçalarını A / S menüsünden seçebilirsin.")
            return
        source = self.source_for(self.current)
        programmes = self._guide_data.get(source["id"], []) if source else []
        now, nxt = now_next(programmes, self.current.tvg_id)
        self.now_title.setText(
            f"ŞİMDİ  {now.start.astimezone():%H:%M} — {now.end.astimezone():%H:%M}   {now.title}"
            if now
            else "Bu kanal için güncel program bulunamadı."
        )
        self.next_title.setText(
            f"SIRADAKİ  {nxt.start.astimezone():%H:%M}   {nxt.title}"
            if nxt
            else "Rehber kanal kimliği, listedeki tvg-id ile eşleşmelidir."
        )

    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        self.fullscreen.set_active(self._fullscreen)
        self.showFullScreen() if self._fullscreen else self.showNormal()

    def leave_fullscreen(self):
        if self._fullscreen:
            self.toggle_fullscreen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.WindowStateChange
            and hasattr(self, "_fullscreen")
            and self.isFullScreen() != self._fullscreen
        ):
            # Wayland configure acknowledgements can arrive after a newer F/Esc
            # request. Reconcile on the next event turn, outside Qt's state update.
            QTimer.singleShot(0, self._reconcile_fullscreen)

    def _reconcile_fullscreen(self):
        if self._closed or self.isFullScreen() == self._fullscreen:
            return
        self.showFullScreen() if self._fullscreen else self.showNormal()

    def shortcut_action(self, key, callback):
        if isinstance(QApplication.focusWidget(), QLineEdit) and key not in (
            "Ctrl+O",
            "Ctrl+F",
            "Escape",
        ):
            return
        if key in ("Right", "Left") and not self._seekable:
            return
        self.fullscreen.reveal()
        callback()

    def about(self):
        QMessageBox.information(
            self,
            "Luna IPTV",
            f"Luna IPTV {__version__}\nÖzgün, kişisel Linux IPTV istemcisi.\n\nCtrl+O  Kaynak ekle\nCtrl+F  Ara\nBoşluk  Oynat / duraklat\nF  Tam ekran\nM  Sesi aç / kapat\n← / →  5 saniye sar\nJ / L  Geri / ileri tara: 2×–16×\nK  Normal oynatmaya dön\nEsc  Tam ekrandan çık\n\nQt + libmpv · Native Wayland ve X11\nHesaplar yalnızca yerel diskte saklanır.",
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self.add_source(location=path)
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self.save_progress()
        self._closed = True
        self.fullscreen.close()
        self.transport.close()
        self._guide_timer.stop()
        self.logo_viewport.close()
        self.logos.close()
        self.player.shutdown()
        self.store.close()
        event.accept()
