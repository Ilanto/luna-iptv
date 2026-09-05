from __future__ import annotations

import gzip
import math
from datetime import datetime
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
from .mini_player import MiniPlayerController
from .models import Channel, Playlist
from .network import LIMIT, NetworkError, XtreamClient, channel_id, fetch, load_m3u
from .playback_dialogs import HistoryDialog, ResumeDialog
from .player import Player
from .preferences import TrackPreferences
from .recovery import RecoveryController
from .source_connections import HealthResult, check_connection, validate_candidate
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
        self._current_persistent = True
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
        self._playback_token = None
        self._untracked_playback_token = None
        self._playback_active = False
        self._account_dialog = None
        self._source_edit_tokens = {}
        self._source_health_tokens = {}
        self._resume_dialog = None
        self._history_dialog = None
        self._record_recent = True
        self._record_progress = True
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
        self.recovery = RecoveryController(self)
        self.track_preferences = TrackPreferences(store, self.player)
        build_window(self)
        self.fullscreen = FullscreenController(self, self.view_layout, self.player_header)
        self.mini_player = MiniPlayerController(self)
        self.transport.changed.connect(self.refresh_transport)
        self.recovery.changed.connect(self.refresh_recovery)
        self.recovery.retry_requested.connect(self._retry_live)
        self.refresh_transport()
        self.player.property_changed.connect(self.player_property)
        self.player.error.connect(self.playback_error)
        self.player.playback_loaded.connect(self.playback_loaded)
        self.player.playback_property_changed.connect(self.playback_property)
        self.player.playback_finished.connect(self.playback_finished)
        self.player.playback_tracking_lost.connect(self.playback_tracking_lost)
        self.player.file_loaded.connect(self._legacy_loaded)
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
        self.mini_status.setText(message)
        self.mini_status.setToolTip(message)
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
        if self.current and self.current.id.startswith(source_id + ":"):
            # Keep the current native request observable, but never reopen it
            # with connection data that this refresh has just superseded.
            if not self._playback_active or self._playback_token is None:
                self.recovery.cancel()
            else:
                self.recovery.suppress_retries(self._playback_token)
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
        if self.current and self.current.id.startswith(source_id + ":"):
            self.save_progress()
        stored_channels = self.store.replace_channels(source_id, channels)
        incoming = {channel.id for channel in stored_channels}
        if self.current and self.current.id.startswith(source_id + ":"):
            if self.current.id not in incoming:
                self._playback_active = False
                self.player.stop()
                self.current = None
                self._loading = False
                self.favorite_button.setEnabled(False)
                self.video_stack.setCurrentIndex(0)
                self.video_title.setText("İyi bir yayına yer aç.")
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
            stored_current = next((c for c in self.model.channels if c.id == self.current.id), None)
            if stored_current is None:
                self._current_persistent = False
                self.favorite_button.setEnabled(False)
            else:
                self.current = stored_current
                self._current_persistent = True
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
        self.history_clear_button.setVisible(section == "recent")
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
            self.request_play(channel)

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
            stored_source = next(
                (item for item in self.store.sources() if item["id"] == source["id"]), None
            )
            if stored_source is None or not self._same_source(stored_source, source):
                return
            if not episodes:
                self.status("Bu dizide henüz bölüm bulunmuyor.")
                return
            stored_episodes = self.store.upsert_channels(source["id"], episodes)
            self.model.reset(self.store.channels(), self.store.favorites())
            self.proxy.episode_ids = {channel.id for channel in stored_episodes}
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

    def resume_position(self, channel):
        position, duration = self.store.progress(channel.id)
        if (
            channel.kind != "live"
            and math.isfinite(position)
            and math.isfinite(duration)
            and position > 5
            and duration > 0
            and position < duration - 10
        ):
            return position
        return 0

    def dismiss_resume(self):
        dialog, self._resume_dialog = self._resume_dialog, None
        if dialog is not None and isValid(dialog):
            dialog.reject()

    def request_play(self, channel):
        self.dismiss_resume()
        position = self.resume_position(channel)
        if not position:
            self.play(channel, start_override=0)
            return
        dialog = ResumeDialog(channel.name, clock_text(position), self)
        self._resume_dialog = dialog

        def finished(result):
            if self._closed or self._resume_dialog is not dialog:
                return
            self._resume_dialog = None
            if result != QDialog.Accepted:
                return
            fresh = next((c for c in self.model.channels if c.id == channel.id), None)
            if fresh is not None:
                self.play(fresh, start_override=position if dialog.choice == "resume" else 0)

        dialog.finished.connect(finished)
        dialog.open()

    def restart_current(self):
        if self.current and self._current_persistent and self.current.kind != "live":
            self.play(self.current, start_override=0)

    def play(self, channel, *, start_override=None, recovering=False):
        if not channel.url:
            self.status("Bu bölüm yeniden alınmalı. Diziyi açıp bölüm listesini yenile.")
            return
        if (
            self.current
            and self.current.id == channel.id
            and self._loading
            and start_override is None
            and not recovering
        ):
            return
        if recovering and (
            self.current is None or self.current.id != channel.id or not self._current_persistent
        ):
            self.recovery.cancel()
            return
        self.dismiss_resume()
        self.save_progress()
        if not recovering:
            self.recovery.begin(channel.id, live=channel.kind == "live")
            self._record_recent = True
            self._record_progress = True
        start = self.resume_position(channel) if start_override is None else start_override
        self.current = channel
        self._current_persistent = True
        self._position = float(start)
        # Retain known duration until mpv publishes metadata; an early close
        # after file-loaded must not erase a valid saved resume position.
        known_duration = self.store.progress(channel.id)[1]
        self._duration = (
            known_duration
            if channel.kind != "live" and math.isfinite(known_duration) and known_duration > 0
            else 0.0
        )
        self._seekable = False
        self._last_saved = 0.0
        self._loading = True
        self._tracks = []
        source = self.source_for(channel)
        self.track_preferences.begin(source["id"] if source else None)
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
        self._playback_token = self.player.reserve_load()
        self._untracked_playback_token = None
        self._playback_active = self._playback_token is not None
        self.recovery.watch(self._playback_token)
        self.status(
            "Yayın açılıyor…",
            lambda: self.play(self.current) if self.current and self._current_persistent else None,
        )
        self.refresh_recovery()
        if self._playback_token is not None:
            self.player.load(channel.url, channel.headers, start=start)
        source = self.source_for(channel)
        if source and source.get("epg_url") and source["id"] not in self._guide_data:
            self.load_guide(source)

    def _legacy_loaded(self):
        if not self._playback_active or self._untracked_playback_token != self._playback_token:
            return
        self.recovery.loaded(self._playback_token)
        self._mark_loaded()

    def playback_loaded(self, token):
        if token != self._playback_token or not self._playback_active:
            return
        self.recovery.loaded(token)
        self._mark_loaded()

    def loaded(self):
        """Update the loaded UI; native callbacks validate their token first."""
        self._mark_loaded()

    def _mark_loaded(self):
        if self._closed:
            return
        self._idle = False
        self._loading = False
        self.transport.loaded()
        self.track_preferences.loaded()
        self.media_info.mark_loaded()
        self.refresh_media_info()
        self.info_button.setEnabled(self.current is not None)
        self.status(self.recovery.message or "Yayın oynatılıyor.")
        self.player.set_property("volume", self.volume.value())
        self.save_progress()

    def playback_error(self, message):
        if self._closed:
            return
        self.status(message)

    def playback_tracking_lost(self, token):
        if self._closed or token != self._playback_token:
            return
        self._untracked_playback_token = token
        self.recovery.suppress_retries(token)

    def playback_property(self, token, name, value):
        if token != self._playback_token:
            return
        if name == "time-pos":
            self.recovery.progress(token, value)
        elif name == "pause":
            self.recovery.paused(token, bool(value))
        elif name == "paused-for-cache":
            self.recovery.buffering(token, bool(value))

    def playback_finished(self, token, reason, message):
        if token != self._playback_token or not self._playback_active:
            return
        recovery_handled = self.recovery.failure(token, reason)
        if self.current and self.current.kind != "live" and not self._loading:
            self.save_progress()
        self._finish_playback()
        if recovery_handled and self.recovery.state == "failed":
            self.status(
                self.recovery.message,
                lambda: (
                    self.play(self.current) if self.current and self._current_persistent else None
                ),
            )
        elif recovery_handled and self.recovery.message:
            self.status(self.recovery.message)
        elif message:
            retry = (
                (
                    lambda: (
                        self.play(self.current)
                        if self.current and self._current_persistent
                        else None
                    )
                )
                if reason == "error"
                and self.current
                and self._current_persistent
                and self.current.kind != "live"
                else None
            )
            self.status(message, retry)

    def _finish_playback(self, *, end_session=True):
        if end_session:
            self._playback_active = False
            self.track_preferences.finish()
        self._idle = True
        self._loading = False
        self.transport.finished()
        self.play_button.setText("▶")
        self.media_info.reset()
        self.refresh_media_info()
        self.fullscreen.set_info_visible(False)
        self.info_button.setEnabled(False)
        self.seek.setEnabled(False)

    def ended(self):
        if self._closed:
            return
        if self._playback_active and self._untracked_playback_token == self._playback_token:
            if self.current and self.current.kind != "live" and not self._loading:
                self.save_progress()
            self.recovery.failure(self._playback_token, "unknown")
            self._finish_playback()

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
            self._tracks = value if isinstance(value, list) else []
            self.track_preferences.update_tracks(self._tracks)
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
        if self.current and self._current_persistent and not self._closed and self._record_progress:
            self.store.save_progress(
                self.current.id, self._position, self._duration, mark_recent=self._record_recent
            )

    def confirm_clear_history(self):
        if self._history_dialog is not None and isValid(self._history_dialog):
            self._history_dialog.raise_()
            return
        source = next(
            (s for s in self.store.sources() if s["id"] == self.source_combo.currentData()), None
        )
        dialog = HistoryDialog(source, self)
        self._history_dialog = dialog

        def finished(result):
            if self._closed or self._history_dialog is not dialog:
                return
            self._history_dialog = None
            if result == QDialog.Accepted:
                self.clear_history(
                    dialog.scope.currentData(), reset_progress=dialog.reset_positions.isChecked()
                )

        dialog.finished.connect(finished)
        dialog.open()

    def clear_history(self, source_id=None, *, reset_progress=False):
        self.store.clear_history(source_id, reset_progress=reset_progress)
        if self.current and (source_id is None or self.current.id.startswith(source_id + ":")):
            self._record_recent = False
            if reset_progress:
                self._record_progress = False
        if reset_progress:
            self.dismiss_resume()
        self.proxy.set_recent_ids(self.store.recent_ids())
        self.filter_changed()
        self.status(
            "İzleme geçmişi temizlendi."
            + (
                " Devam etme konumları sıfırlandı."
                if reset_progress
                else " Kaldığın yerler korundu."
            )
        )

    def seek_to_slider(self):
        if self._duration > 0 and self._seekable:
            self.transport.cancel(restore_pause=True)
            self.player.command(["seek", self.seek.value() * self._duration / 1000, "absolute"])

    def toggle_play(self):
        if self.current and not self._current_persistent:
            self.status("Bu yayın artık kaynakta bulunmuyor. Listeden başka bir yayın seç.")
            return
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
        self.dismiss_resume()
        self.save_progress()
        self.recovery.cancel()
        self._untracked_playback_token = None
        self._playback_active = False
        self.track_preferences.finish()
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

    def cancel_recovery(self):
        self.stop_playback()

    def _retry_live(self, channel_id):
        if (
            self._closed
            or self.current is None
            or self.current.id != channel_id
            or not self._current_persistent
        ):
            self.recovery.cancel()
            return
        self.play(self.current, recovering=True)

    def refresh_recovery(self):
        if self._closed:
            return
        terminal = self.recovery.state in {"failed", "untracked-failed"}
        live_wait = (
            self.recovery.state == "waiting"
            and self.current is not None
            and self.current.kind == "live"
        )
        terminal_cleanup = terminal and (self._playback_active or self._loading or not self._idle)
        if terminal_cleanup or (live_wait and (self._loading or not self._idle)):
            self._finish_playback(end_session=terminal)
            if terminal:
                self.player.stop()
        self.recovery_cancel_button.setVisible(self.recovery.can_cancel)
        self.mini_cancel_button.setVisible(self.recovery.can_cancel)
        if self.recovery.message:
            retry = (
                (
                    lambda: (
                        self.play(self.current)
                        if self.current and self._current_persistent
                        else None
                    )
                )
                if self.recovery.state in {"failed", "untracked-failed"}
                else None
            )
            self.status(self.recovery.message, retry)

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
        if not self.current or not self._current_persistent:
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
        menu = self.build_track_menu()
        menu.exec(self.cursor().pos())
        menu.deleteLater()

    def build_track_menu(self):
        menu = QMenu(self)
        generation = self.track_preferences.generation

        def guarded(callback):
            if not self._closed and self.track_preferences.generation == generation:
                callback()

        for mode, title in [("audio", "Ses parçaları"), ("sub", "Altyazılar")]:
            sub = menu.addMenu(title)
            sub.setEnabled(self.current is not None and not self._idle)
            tracks = [t for t in self._tracks if isinstance(t, dict) and t.get("type") == mode]
            off = sub.addAction("Kapalı")
            off.setCheckable(True)
            off.setChecked(not any(t.get("selected") for t in tracks))
            off.triggered.connect(
                lambda checked=False, m=mode: self.track_preferences.select(
                    m, None, generation=generation
                )
            )
            for track in tracks:
                label = track.get("title") or track.get("lang") or f"Parça {track.get('id', '')}"
                action = sub.addAction(str(label).replace("&", "&&"))
                action.setCheckable(True)
                action.setChecked(bool(track.get("selected")))
                action.triggered.connect(
                    lambda checked=False, m=mode, t=track: self.track_preferences.select(
                        m, t, generation=generation
                    )
                )
        menu.addSeparator()
        remember = menu.addAction("Bu kaynak için tercihleri hatırla")
        remember.setCheckable(True)
        remember.setChecked(self.track_preferences.remember)
        remember.setEnabled(self.track_preferences.source_id is not None)
        remember.triggered.connect(
            lambda checked: guarded(lambda: self.track_preferences.set_remember(checked))
        )
        reset = menu.addAction("Ses ve altyazı tercihlerini sıfırla")
        reset.setEnabled(self.track_preferences.source_id is not None)
        reset.triggered.connect(lambda: guarded(self.track_preferences.reset))
        menu.addSeparator()
        restart = menu.addAction("Baştan başlat")
        restart.setEnabled(
            self.current is not None and self._current_persistent and self.current.kind != "live"
        )
        restart.triggered.connect(lambda: guarded(self.restart_current))
        return menu

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
        edit = menu.addAction("Bağlantıyı düzenle")
        edit.setEnabled(source is not None and not self._busy)
        edit.triggered.connect(lambda: self.edit_source(source))
        refresh = menu.addAction("Seçili kaynağı yenile")
        refresh.setEnabled(source is not None and not self._busy)
        refresh.triggered.connect(lambda: self.import_source(source))
        check = menu.addAction("Bağlantıyı kontrol et")
        check.setEnabled(source is not None)
        check.triggered.connect(lambda: self.check_source(source))
        if source is not None:
            snapshot = self.store.source_health(source["id"])
            if snapshot is not None:
                status, checked_at = snapshot
                label = {
                    "available": "Ulaşılabilir",
                    "responding": "Sunucu yanıt veriyor",
                    "unverified": "Akış doğrulanmadı",
                    "unavailable": "Ulaşılamıyor",
                }.get(status, "Bilinmiyor")
                moment = datetime.fromtimestamp(checked_at).strftime("%d.%m.%Y %H:%M")
                last_check = menu.addAction(f"Son kontrol: {label} · {moment}")
                last_check.setEnabled(False)
            else:
                last_check = menu.addAction("Son kontrol: Henüz kontrol edilmedi")
                last_check.setEnabled(False)
        if source is not None and source["type"] == "xtream":
            account = menu.addAction("Hesap durumu")
            account.triggered.connect(lambda: self.open_account(source))
        remove = menu.addAction("Seçili kaynağı kaldır")
        remove.setEnabled(source is not None and not self._busy)
        remove.triggered.connect(lambda: self.remove_source(source))
        menu.addSeparator()
        menu.addAction("Kısayollar ve hakkında", self.about)
        return menu

    @staticmethod
    def _same_source(left, right):
        fields = ("id", "name", "type", "location", "username", "password", "epg_url")
        return all(str(left.get(field, "")) == str(right.get(field, "")) for field in fields)

    def edit_source(self, source):
        if source is None or self._busy:
            return
        expected = dict(source)
        dialog = SourceDialog(self, source=expected)
        if dialog.exec() != QDialog.Accepted:
            return
        candidate = dialog.source()
        token = object()
        self._source_edit_tokens[expected["id"]] = token

        def is_current():
            if self._closed or self._source_edit_tokens.get(expected["id"]) is not token:
                return False
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            return stored is not None and self._same_source(stored, expected)

        def completed(playlist):
            if not is_current():
                return
            self._source_edit_tokens.pop(expected["id"], None)
            if not playlist.channels:
                self.status("Bu kaynakta oynatılabilir yayın bulunamadı. Önceki liste korundu.")
                return
            if not self.store.apply_source_connection(expected, candidate, playlist):
                self.status("Kaynak bu sırada değişti. Düzenleme uygulanmadı.")
                return
            if self.current and self.current.id.startswith(expected["id"] + ":"):
                if self._playback_active and self._playback_token is not None:
                    self.recovery.suppress_retries(self._playback_token)
                else:
                    self.recovery.cancel()
            self.refresh_library(select_source=expected["id"])
            self.status("Kaynak bağlantısı doğrulandı ve güncellendi.")
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            if stored is not None and stored.get("epg_url"):
                self.load_guide(stored)

        def failed(message):
            if not is_current():
                return
            self._source_edit_tokens.pop(expected["id"], None)
            self.status(message)

        self.run_task(
            lambda: validate_candidate(candidate),
            completed,
            "Yeni bağlantı doğrulanıyor…",
            busy=True,
            failure=failed,
        )

    def check_source(self, source):
        if source is None:
            return
        expected = dict(source)
        token = object()
        self._source_health_tokens[expected["id"]] = token

        def is_current():
            if self._closed or self._source_health_tokens.get(expected["id"]) is not token:
                return False
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            return stored is not None and self._same_source(stored, expected)

        def completed(result):
            if not is_current():
                return
            self._source_health_tokens.pop(expected["id"], None)
            if not self.store.save_source_health(expected["id"], result.status, result.checked_at):
                return
            message = {
                "available": "Bağlantı kullanılabilir.",
                "responding": "Sunucu yanıt veriyor; video akışı açılmadı.",
                "unverified": "Adres geçerli; video akışı açılmadan doğrulanamıyor.",
                "unavailable": "Bağlantıya ulaşılamadı.",
            }.get(result.status, "Bağlantı durumu belirlenemedi.")
            self.status(message)

        def failed(_message):
            completed(HealthResult("unavailable", int(datetime.now().timestamp())))

        self.run_task(
            lambda: check_connection(expected),
            completed,
            "Bağlantı kontrol ediliyor…",
            busy=False,
            failure=failed,
        )

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
            stored = next(
                (item for item in self.store.sources() if item["id"] == source["id"]), None
            )
            return (
                dialog.accepts_updates and stored is not None and self._same_source(stored, source)
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
        self._source_edit_tokens.pop(source["id"], None)
        self._source_health_tokens.pop(source["id"], None)
        if self.current and self.current.id.startswith(source["id"] + ":"):
            self.stop_playback()
            self.current = None
            self._current_persistent = False
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
            self.next_title.setText(
                "Ses, altyazı ve baştan başlatma seçenekleri Oynatma menüsünde."
            )
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

    def toggle_mini_player(self):
        if self.mini_player.active or self.mini_player.pending:
            self.leave_mini_player()
        elif self._fullscreen:
            self.mini_player.enter_after_fullscreen(
                self._fullscreen_return_geometry, self._fullscreen_return_maximized
            )
            self.toggle_fullscreen()
        else:
            self.mini_player.enter()

    def leave_mini_player(self):
        if self._fullscreen:
            self.toggle_fullscreen()
        self.mini_player.leave()
        # Late fullscreen acknowledgements must restore the latest windowed mode.
        self._fullscreen_return_maximized = self.isMaximized()
        self._fullscreen_return_geometry = (
            self.normalGeometry() if self.isMaximized() else self.geometry()
        )

    def toggle_fullscreen(self):
        if not self._fullscreen:
            self.mini_player.cancel_pending()
            self._fullscreen_return_maximized = self.isMaximized()
            self._fullscreen_return_geometry = (
                self.normalGeometry() if self.isMaximized() else self.geometry()
            )
        self._fullscreen = not self._fullscreen
        if self.mini_player.active:
            self.mini_player.set_fullscreen(self._fullscreen)
        else:
            self.fullscreen.set_active(self._fullscreen)
        self.showFullScreen() if self._fullscreen else self._restore_windowed_state()

    def _restore_windowed_state(self):
        self.showNormal()
        if self.mini_player.active:
            self.mini_player.restore_mini_geometry()
        elif hasattr(self, "_fullscreen_return_geometry"):
            self.setGeometry(self._fullscreen_return_geometry)
            if self._fullscreen_return_maximized:
                self.showMaximized()

    def leave_fullscreen(self):
        if self._fullscreen:
            self.toggle_fullscreen()
        elif self.mini_player.active or self.mini_player.pending:
            self.leave_mini_player()

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
        self.showFullScreen() if self._fullscreen else self._restore_windowed_state()

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
        self.mini_player.close()
        self._source_edit_tokens.clear()
        self._source_health_tokens.clear()
        self.dismiss_resume()
        self.track_preferences.finish()
        self.fullscreen.close()
        self.transport.close()
        self.recovery.close()
        self._guide_timer.stop()
        self.logo_viewport.close()
        self.logos.close()
        self.player.shutdown()
        self.store.close()
        event.accept()
