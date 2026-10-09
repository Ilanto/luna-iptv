from __future__ import annotations

import gzip
import hashlib
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
)
from shiboken6 import isValid

from . import __version__, theme
from .accounts import sanitize_profile
from .category_editor import CategoryEditor
from .channel_banner import BannerPlacer, ChannelBanner
from .dialogs import AccountDialog, GuideDialog, SourceDialog
from .epg import GuideIndex, parse_xmltv
from .fullscreen import FullscreenController
from .idle_inhibit import IdleInhibit
from .layout import build_window
from .library import (
    ChannelFilter,
    ChannelModel,
    channel_key,
    ordered_channels,
    resumable,
    search_key,
)
from .media_controller import MediaDetailController
from .media_info import MediaInfo
from .mini_player import MiniPlayerController
from .models import Channel, Playlist
from .motion import set_motion_level
from .mpris import MprisService
from .network import LIMIT, NetworkError, XtreamClient, channel_id, fetch, load_m3u
from .parental_ui import ParentalDialog, ask_pin
from .playback_dialogs import HistoryDialog, ResumeDialog
from .player import Player
from .preferences import TrackPreferences, normalize_preferences
from .profiles_ui import ProfilePicker, ProfilesDialog
from .recovery import RecoveryController
from .reminders import ReminderService
from .reminders_dialog import RemindersDialog
from .settings import AUTOPLAY_CHOICES, MOTION_CHOICES, STARTUP_CHOICES, selected_setting
from .settings_dialog import SettingsDialog
from .shell_motion import PageTransition, WatchPanelController
from .source_connections import HealthResult, check_connection, validate_candidate
from .tasks import Task
from .toast import Toast
from .transport import TransportController
from .watching import SLEEP_CHOICES, Countdown, NumberEntry, SleepTimer, next_episode


def clock_text(seconds):
    seconds = int(max(0, seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def track_path(channel_id):
    """A stable MPRIS track object path for a channel id (ids may hold any character)."""
    return "/org/mpris/MediaPlayer2/luna/" + hashlib.sha1(channel_id.encode()).hexdigest()[:16]


class RemoteControl:
    """What desktop media controls (MPRIS) may do with the window."""

    def __init__(self, window):
        self._window = window

    def play(self):
        if self._window._playback_paused or self._window._idle:
            self._window.toggle_play()

    def pause(self):
        if self._window._playback_active and not self._window._playback_paused:
            self._window.toggle_play()

    def play_pause(self):
        self._window.toggle_play()

    def stop(self):
        self._window.stop_playback()

    def next(self):
        self._window.zap(1)

    def previous(self):
        self._window.zap(-1)

    def seek(self, offset_us):
        self._window.transport.seek_relative(offset_us / 1e6)

    def set_position(self, position_us):
        self._window.transport.seek_relative(position_us / 1e6 - self._window._position)

    def raise_window(self):
        if self._window.isMinimized():
            self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()


class MainWindow(QMainWindow):
    def __init__(self, store, *, ask_profile=False):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.store = store
        set_motion_level(selected_setting(store, "motion_level", MOTION_CHOICES))
        self._settings_dialog = None
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
        self._guide_index = {}
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
        self._folder_id = None
        self.proxy.setSourceModel(self.model)
        self.player = Player(self)
        self.transport = TransportController(self.player, self)
        self.recovery = RecoveryController(self)
        self.track_preferences = TrackPreferences(store, self.player)
        build_window(self)
        self._language_notice_timer = QTimer(self)
        self._language_notice_timer.setSingleShot(True)
        self._language_notice_timer.setInterval(7000)
        self._language_notice_timer.timeout.connect(self.language_notice.hide)
        self.details = MediaDetailController(self)
        self.fullscreen = FullscreenController(self, self.view_layout, self.player_header)
        self.channel_banner = ChannelBanner(self.watch)
        self._banner_placer = BannerPlacer(
            self.channel_banner,
            self.video_stack,
            self.controls,
            lambda: self.fullscreen.active,
            self.banner_suppressed,
        )
        self.fullscreen.controls_shown.connect(lambda: self.show_channel_banner(None))
        self.fullscreen.controls_hidden.connect(self.channel_banner.hide_banner)
        self.mini_player = MiniPlayerController(self)
        self.watch_panel = WatchPanelController(self)
        self.page_transition = PageTransition(self.library_pages)
        self.toast = Toast(self)
        self.idle_inhibit = IdleInhibit()
        self.mpris = MprisService(RemoteControl(self), self)
        self.reminder_service = ReminderService(self.store, self._watch_reminder, self.status, self)
        self.reminder_service.changed.connect(self._reminders_changed)
        self.sleep_timer = SleepTimer(self)
        self.sleep_timer.changed.connect(self.refresh_sleep_button)
        self.sleep_timer.expired.connect(self.sleep_expired)
        self._sleep_label_timer = QTimer(self)
        self._sleep_label_timer.setInterval(20000)
        self._sleep_label_timer.timeout.connect(self.refresh_sleep_button)
        self.next_countdown = Countdown(self)
        self.next_countdown.tick.connect(self._next_episode_tick)
        self.next_countdown.due.connect(self.play_next_episode)
        self.number_entry = NumberEntry(self)
        self.number_entry.typing.connect(self._number_typing)
        self.number_entry.chosen.connect(self.jump_to_number)
        self._notice_kind = None
        self._failover_tried = set()
        self._reminders_dialog = None
        self._playback_paused = False
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
        self.refresh_profile_badge()
        self._guide_timer = QTimer(self)
        self._guide_timer.setInterval(30000)
        self._guide_timer.timeout.connect(self.update_guide)
        self._guide_timer.timeout.connect(self._refresh_live_cards)
        self._guide_timer.start()
        QTimer.singleShot(0, self.load_cached_guides)
        QTimer.singleShot(0, self.start_session if ask_profile else self.restore_last_channel)

    def status(self, message, retry=None, *, icon=None):
        self.mini_status.setText(message)
        self.mini_status.setToolTip(message)
        self.message.setText(message)
        self._retry = retry
        self.retry_button.setVisible(retry is not None)
        self._sync_message_bar()
        if retry is not None or not self.recovery_cancel_button.isHidden():
            self.toast.dismiss(immediate=True)
        elif not message.startswith("Hazır"):
            self.toast.show_message(message, icon=icon)

    def _sync_message_bar(self):
        actions = self._retry is not None or not self.recovery_cancel_button.isHidden()
        self.message_bar.setVisible(actions and not self.fullscreen.active)

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
        if self._busy or not self.guard("Kaynak eklemek için PIN gir."):
            return
        dialog = SourceDialog(self, location)
        if dialog.exec() == QDialog.Accepted:
            self.import_source(dialog.source())

    def import_source(self, source):
        from .backup import source_incomplete

        if source_incomplete(source):
            self.status("Önce eksik kaynak bilgilerini «Bağlantıyı düzenle» ile tamamlayın.")
            return
        if self._busy:
            return
        source = dict(source)
        self.channel_list.set_loading(True)
        self.filter_changed()

        def done(result):
            try:
                self.accept_import(source, result)
            finally:
                self.channel_list.set_loading(False)
                self.filter_changed()

        def failed(error):
            self.channel_list.set_loading(False)
            self.filter_changed()
            self.status(error, lambda: self.import_source(source))

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
            done,
            "Kaynak okunuyor…",
            lambda: self.import_source(source),
            failure=failed,
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
                self._sync_playback_state()
                self.player.stop()
                self.current = None
                self.watch_panel.sync()
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
        self.model.reset(
            ordered_channels(self.store.channels(), self.store.category_prefs()),
            self.store.favorites(),
            self.store.progress_map(),
        )
        if self.store.pin_hash():
            self.store.lock_adult_groups()  # new adult categories from this catalogue
        self.apply_locks(filter_now=False)
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
        self.details.invalidate()
        has_channels = bool(self.model.channels)
        self.welcome_title.setText("Yayının hazır." if has_channels else "Ekran senin.")
        self.welcome_subtitle.setText(
            "Soldan bir yayın seç.\nİzleme alanın burada."
            if has_channels
            else "Kendi listeni ekle. Sevdiğin yayını seç.\nGerisini Luna’ya bırak."
        )
        self.welcome_action.setText("Başka kaynak ekle" if has_channels else "İlk kaynağını ekle")
        self.refresh_home()
        self._reminders_changed()  # the visible guide must not keep removed channels

    def refresh_home(self):
        if not self._closed and self.library_pages.currentWidget() is self.home_view:
            self.home_view.refresh()

    def set_section(self, section):
        if section == "guide":
            self.show_guide()
            return
        target = self.home_view if section == "home" else self.browse
        changed = self.library_pages.currentWidget() is not target or (
            target is self.browse and self.proxy.section != section
        )
        self.page_transition.begin(changed)
        if section == "home":
            for key, button in self.nav_buttons.items():
                button.setChecked(key == "home")
            self.library_pages.setCurrentWidget(self.home_view)
            self.refresh_home()
            self.page_transition.end()
            return
        self.library_pages.setCurrentWidget(self.browse)
        if section != "favorites":
            self._folder_id = None
            self.proxy.folder_ids = None
        self.proxy.section = section
        self.history_clear_button.setVisible(section == "recent")
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
        self.page_transition.end()

    def source_changed(self):
        self.proxy.source = self.source_combo.currentData() or ""
        self.refresh_categories()
        self.filter_changed()

    def refresh_categories(self):
        self.proxy.set_category_prefs(self.store.category_prefs())
        current = self.category.currentData() or ""
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("Tüm kategoriler", "")
        hidden = self.model.locked if self.proxy.hide_locked else frozenset()
        counts = Counter()
        positions = {}
        hidden_groups = set()
        personal = self.proxy.section in ("favorites", "recent")
        for c in self.model.channels:
            if (
                c.id in hidden
                or (self.proxy.source and not c.id.startswith(self.proxy.source + ":"))
                or (not personal and c.kind != self.proxy.section)
                or (c.series_id and c.kind == "movie")
            ):
                continue
            key = self.proxy.category_key(c)
            if not personal and self.proxy.category_hidden(c):
                hidden_groups.add(key)
                continue
            if not c.group:
                continue
            counts[c.group] += 1
            position = self.proxy.category_positions.get(key) if not personal else None
            if position is not None:
                positions[c.group] = min(positions.get(c.group, position), position)
        groups = sorted(
            counts,
            key=lambda g: (g not in positions, positions.get(g, 0), -counts[g], g.casefold()),
        )
        for group in groups:
            self.category.addItem(group, group)
        index = self.category.findData(current)
        self.category.setCurrentIndex(index if index >= 0 else 0)
        self.category.blockSignals(False)
        self.category_bar.edit_button.setVisible(not personal)
        self.category_bar.set_items(
            [(group, group, counts[group]) for group in groups],
            self.category.currentData() or "",
        )
        self.category_bar.edit_button.setEnabled(bool(self.store.sources()))
        self._hidden_category_count = len(hidden_groups)
        self.hidden_categories_label.setText(
            f'{len(hidden_groups)} kategori gizli · <a href="edit" style="color: {theme.ACCENT};'
            f' text-decoration: none;">Düzenle</a>'
        )
        self.refresh_folders()

    def edit_categories(self, *_):
        if self.proxy.section not in ("live", "movie", "series"):
            return
        if not self.guard("Kategori düzenini değiştirmek için PIN gir."):
            return
        dialog = CategoryEditor(self.store, self.proxy.source, self.proxy.section, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh_library()  # reorders from the catalogue, so a reset restores it
        dialog.deleteLater()

    def refresh_folders(self):
        folders = self.store.folders()
        if self._folder_id not in {folder_id for folder_id, _ in folders}:
            self._folder_id = None
        self.proxy.folder_ids = (
            self.store.folder_items(self._folder_id) if self._folder_id is not None else None
        )
        favorites = {
            c.id
            for c in self.model.channels
            if c.id in self.model.favorites
            and not (self.proxy.hide_locked and c.id in self.model.locked)
            and (not self.proxy.source or c.id.startswith(self.proxy.source + ":"))
        }
        self.folder_bar.set_items(
            [
                (str(folder_id), name, len(self.store.folder_items(folder_id) & favorites))
                for folder_id, name in folders
            ],
            str(self._folder_id) if self._folder_id is not None else "",
            total=len(favorites),
        )
        for value, button in self.folder_bar.buttons().items():
            if value:
                button.setContextMenuPolicy(Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(
                    lambda pos, folder_id=int(value), anchor=button: self.folder_context_menu(
                        folder_id, anchor.mapToGlobal(pos)
                    )
                )

    def choose_folder(self, value):
        self._folder_id = int(value) if value else None
        self.refresh_folders()
        self.filter_changed()

    def create_folder(self, channel=None):
        name, accepted = QInputDialog.getText(self, "Yeni klasör", "Klasör adı:")
        if not accepted:
            return
        try:
            folder_id = self.store.create_folder(name)
        except ValueError as error:
            QMessageBox.warning(self, "Klasör oluşturulamadı", str(error))
            return
        if channel is not None:
            self.set_folder_membership(folder_id, channel, True)
        else:
            self.refresh_folders()
            self.filter_changed()
        self.status(f"“{name.strip()}” klasörü oluşturuldu.", icon="check")
        return folder_id

    def rename_folder(self, folder_id):
        name = dict(self.store.folders()).get(folder_id)
        if name is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Klasörü yeniden adlandır", "Klasör adı:", QLineEdit.Normal, name
        )
        if not accepted:
            return
        try:
            self.store.rename_folder(folder_id, name)
        except ValueError as error:
            QMessageBox.warning(self, "Klasör yeniden adlandırılamadı", str(error))
            return
        self.refresh_folders()

    def delete_folder(self, folder_id):
        name = dict(self.store.folders()).get(folder_id)
        if name is None:
            return
        if (
            QMessageBox.question(
                self,
                "Klasörü sil",
                f"“{name}” klasörü silinsin mi?\nFavorilerin korunacak.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self.store.delete_folder(folder_id)
        self.refresh_folders()
        self.filter_changed()

    def build_folder_menu(self, folder_id):
        menu = QMenu(self)
        menu.addAction("Yeniden adlandır…", lambda: self.rename_folder(folder_id))
        menu.addAction("Sil", lambda: self.delete_folder(folder_id))
        return menu

    def folder_context_menu(self, folder_id, position):
        menu = self.build_folder_menu(folder_id)
        menu.exec(position)
        menu.deleteLater()

    def build_channel_menu(self, channel):
        menu = QMenu(self)
        favorite = channel.id in self.store.favorites()
        menu.addAction(
            "Favorilerden çıkar" if favorite else "Favorilere ekle",
            lambda: self.toggle_channel_favorite(channel),
        )
        folders = menu.addMenu("Klasöre ekle")
        memberships = self.store.folders_of(channel.id)
        for folder_id, name in self.store.folders():
            action = folders.addAction(name)
            action.setCheckable(True)
            action.setChecked(folder_id in memberships)
            action.triggered.connect(
                lambda checked, fid=folder_id: self.set_folder_membership(fid, channel, checked)
            )
        folders.addSeparator()
        folders.addAction("Yeni klasör…", lambda: self.create_folder(channel))
        self._add_reminder_menu(menu, channel)
        if self.store.pin_hash() and not self.kids_profile():
            menu.addSeparator()
            if channel.id in self.store.locked_channels():
                menu.addAction("Kilidi kaldır…", lambda: self.set_channel_locked(channel, False))
            elif channel.id not in self.model.locked:
                menu.addAction("Kilitle", lambda: self.set_channel_locked(channel, True))
        return menu

    def channel_context_menu(self, position):
        channel = self.channel_list.indexAt(position).data(Qt.UserRole)
        if channel is None:
            return
        menu = self.build_channel_menu(channel)
        menu.exec(self.channel_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def set_folder_membership(self, folder_id, channel, member):
        self.store.set_in_folder(folder_id, channel.id, member)
        self.refresh_favorites()

    def show_guide(self):
        """The Rehber page; the browse filters keep their state for when people return."""
        self.page_transition.begin(self.library_pages.currentWidget() is not self.guide_view)
        for key, button in self.nav_buttons.items():
            button.setChecked(key == "guide")
        self.library_pages.setCurrentWidget(self.guide_view)
        self.refresh_guide_view()
        self.guide_view.go_now()
        self.page_transition.end()

    def refresh_guide_view(self):
        rows, seen = [], set()
        for channel in self.model.channels:
            if channel.kind != "live" or not channel.tvg_id or channel.id in seen:
                continue
            if self.proxy.hide_locked and channel.id in self.model.locked:
                continue
            index = self._guide_index.get(channel.id.split(":", 1)[0])
            if index is not None and channel.tvg_id in index.channel_ids():
                rows.append((channel, index))
                seen.add(channel.id)
        self.guide_view.can_remind = True
        reminded = {(r["channel_id"], r["start"]) for r in self.reminder_service.reminders()}
        self.guide_view.set_rows(rows, reminded)

    def choose_category(self, group):
        index = self.category.findData(group)
        self.category.setCurrentIndex(index if index >= 0 else 0)
        self.category_bar.set_current(self.category.currentData() or "")

    def choose_search_kind(self, kind):
        if kind == "guide":
            # Programmes live in the Rehber page: carry the words there.
            query = self.search.text().strip()
            self.set_section("guide")
            self.guide_view.search.setText(query)
            return
        self.proxy.kind = kind
        self.filter_changed()

    def programme_hits(self, query, hours=24):
        """How many programmes of the next ``hours`` match ``query`` on visible channels."""
        key = search_key(query)
        if len(key) < 2 or not self._guide_index:
            return 0
        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=hours)
        by_source = {}
        for channel in self.model.channels:
            if channel.kind != "live" or not channel.tvg_id:
                continue
            if self.proxy.hide_locked and channel.id in self.model.locked:
                continue
            source = channel.id.split(":", 1)[0]
            if self.proxy.source and source != self.proxy.source:
                continue
            by_source.setdefault(source, set()).add(channel.tvg_id)
        return sum(
            len(index.search(key, start, end, by_source[source]))
            for source, index in self._guide_index.items()
            if source in by_source
        )

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().casefold().strip()
        favorites = self.proxy.section == "favorites"
        self.proxy.group = "" if favorites else self.category.currentData() or ""
        searching = bool(self.proxy.query)
        if not searching:
            self.proxy.kind = ""
        self.proxy.refresh()
        count = self.proxy.rowCount()
        if searching:
            counts = self.proxy.search_counts()
            kinds = [
                ("live", "Canlı", counts["live"]),
                ("movie", "Film", counts["movie"]),
                ("series", "Dizi", counts["series"]),
            ]
            programmes = self.programme_hits(self.search.text())
            if programmes:
                kinds.append(("guide", "Rehberde", programmes))
            self.kind_bar.set_items(kinds, self.proxy.kind)
            self.count_label.setText(f"{count:,} sonuç".replace(",", "."))
        else:
            self.count_label.setText(f"{count:,} yayın".replace(",", "."))
        self.category_bar.setVisible(not searching and not favorites)
        self.hidden_categories_label.setVisible(
            bool(self._hidden_category_count)
            and not searching
            and self.proxy.section in ("live", "movie", "series")
        )
        self.folder_row.setVisible(favorites)
        self.kind_bar.setVisible(searching)
        kind = self.proxy.kind if searching else self.proxy.section
        self.channel_list.set_poster_mode(kind in ("movie", "series"))
        loading = self.channel_list.loading
        self.no_results.setVisible(count == 0 and not loading)
        self.channel_list.setVisible(count > 0 or loading)
        self.no_results.setText(
            "Aramana uygun yayın yok.\nFiltreleri değiştirebilirsin."
            if self.model.channels
            else "Henüz yayın yok.\nBir kaynak ekleyerek başla."
        )

    def activate_index(self, index):
        self.open_channel(index.data(Qt.UserRole))

    def open_channel(self, channel):
        """Open a channel through the shared playback or PIN-guarded detail path."""
        if not channel:
            return
        if channel.kind == "live":
            self.details.dismiss()
            self.request_play(channel)
        elif self.unlock_channel(channel):
            self.details.open(channel)

    def _proxy_row(self, channel_id):
        for row in range(self.proxy.rowCount()):
            if self.proxy.index(row, 0).data(Qt.UserRole).id == channel_id:
                return row
        return None

    def can_zap(self):
        return (
            self.current is not None and self.current.kind == "live" and self.proxy.rowCount() > 1
        )

    def zap(self, step):
        """Play the next (1) or previous (-1) live channel in the order the grid shows.

        Without a live channel playing, Page Up / Page Down keep scrolling the grid.
        """
        if not self.can_zap():
            bar = self.channel_list.verticalScrollBar()
            action = QAbstractSlider.SliderAction
            bar.triggerAction(action.SliderPageStepAdd if step > 0 else action.SliderPageStepSub)
            return False
        row = self._proxy_row(self.current.id)
        if row is None:
            self.status("Bu kanal şu anki listede yok; önce listeden bir kanal seç.")
            return False
        rows = self.proxy.rowCount()
        for _ in range(rows - 1):
            row = (row + step) % rows
            channel = self.proxy.index(row, 0).data(Qt.UserRole)
            if channel.kind == "live" and channel.id not in self.model.locked:
                index = self.proxy.index(row, 0)
                self.channel_list.setCurrentIndex(index)
                self.channel_list.scrollTo(index)
                self.request_play(channel)
                return True
        return False

    def restore_last_channel(self):
        """Select or play the last live channel according to the startup preference."""
        if self._closed or self.current is not None:
            return
        action = selected_setting(self.store, "startup_action", STARTUP_CHOICES)
        if action == "none":
            return
        live = {
            c.id for c in self.model.channels if c.kind == "live" and c.id not in self.model.locked
        }
        last = next((cid for cid in self.store.recent_ids(10_000) if cid in live), None)
        row = self._proxy_row(last) if last else None
        if row is None:
            return
        index = self.proxy.index(row, 0)
        self.channel_list.setCurrentIndex(index)
        self.channel_list.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        if self.library_pages.currentWidget() is self.home_view:
            self.home_view.select_channel(last)
        else:
            self.channel_list.setFocus()
        if action == "play":
            self.request_play(index.data(Qt.UserRole))
            return
        self.welcome_subtitle.setText(
            f"Son izlediğin: {index.data(Qt.UserRole).name}\nOynatmak için Enter'a bas."
        )

    def open_settings(self):
        if not self.guard("Ayarları açmak için PIN gir."):
            return
        if self._settings_dialog is None or not isValid(self._settings_dialog):
            self._settings_dialog = SettingsDialog(self.store, self)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def source_for(self, channel):
        prefix = channel.id.split(":", 1)[0]
        return next((s for s in self.store.sources() if s["id"] == prefix), None)

    def resume_position(self, channel):
        position, duration = self.store.progress(channel.id)
        return position if channel.kind != "live" and resumable(position, duration) else 0

    def dismiss_resume(self):
        dialog, self._resume_dialog = self._resume_dialog, None
        if dialog is not None and isValid(dialog):
            dialog.reject()

    def request_play(self, channel, *, preferences=None, approved=False, failover=False):
        """Play a channel; locked ones ask for the PIN every time (approved: already asked)."""
        if not self.unlock_channel(channel, approved=approved):
            return
        if not failover:
            self._failover_tried = set()  # a person's own choice starts a new chain
        self.cancel_next_episode()
        self.dismiss_resume()
        preferences = normalize_preferences(preferences) if preferences is not None else None
        position = self.resume_position(channel)
        if not position:
            self.play(channel, start_override=0, preferences=preferences)
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
                self.play(
                    fresh,
                    start_override=position if dialog.choice == "resume" else 0,
                    preferences=preferences,
                )

        dialog.finished.connect(finished)
        dialog.open()

    def restart_current(self):
        if self.current and self._current_persistent and self.current.kind != "live":
            self.play(self.current, start_override=0)

    def _sync_playback_state(self):
        """Tell the desktop what is playing: the idle inhibit and media controls."""
        self.idle_inhibit.set_active(self._playback_active and not self._playback_paused)
        self._sync_mpris()

    def _sync_mpris(self):
        channel = self.current if self._playback_active else None
        if channel is None:
            self.mpris.update(status="Stopped")
            self.mpris.set_position(0)
            return
        programme = self.programme_now(channel)
        source = self.source_for(channel)
        artist = channel.name if programme else (source["name"] if source else "")
        live = channel.kind == "live"
        self.mpris.update(
            status="Paused" if self._playback_paused else "Playing",
            title=programme.title if programme else channel.name,
            artist=artist,
            art_url=channel.logo if channel.logo.startswith(("http://", "https://")) else "",
            length_us=None if live or self._duration <= 0 else round(self._duration * 1e6),
            can_seek=self._seekable,
            can_go_next=self.can_zap(),
            can_go_previous=self.can_zap(),
            track_id=track_path(channel.id),
        )

    def play(self, channel, *, start_override=None, recovering=False, preferences=None):
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
        persist_preferences = preferences is not None
        if preferences is None and self.current is not None and self.current.id == channel.id:
            preferences = self.track_preferences.current_choices()
        self.current = channel
        self.watch_panel.sync()
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
        track_options = self.track_preferences.begin(
            source["id"] if source else None,
            preferences=preferences,
            persist=persist_preferences,
        )
        self._language_notice_timer.stop()
        self.language_notice.hide()
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
        self._playback_paused = False
        self._sync_playback_state()
        self.recovery.watch(self._playback_token)
        self.status(
            "Yayın açılıyor…",
            lambda: self.play(self.current) if self.current and self._current_persistent else None,
        )
        self.refresh_recovery()
        if self._playback_token is not None:
            self.player.load(channel.url, channel.headers, start=start, track_options=track_options)
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

    def banner_suppressed(self):
        """The mini player (unless it went fullscreen) has no room for the banner."""
        mini = getattr(self, "mini_player", None)
        return bool(mini is not None and mini.active and not self.isFullScreen())

    def show_channel_banner(self, seconds=4.5):
        """The TV-style banner: number, channel, programme now and next."""
        channel = self.current
        if self._closed or channel is None or self.video_stack.currentIndex() != 1:
            return
        if self.banner_suppressed():
            self.channel_banner.hide_banner()
            return
        programme = self.programme_now(channel)
        following = None
        if programme is not None:
            index = self._guide_index.get(channel.id.split(":", 1)[0])
            upcoming = index.upcoming(channel.tvg_id, 1) if index else []
            following = upcoming[0] if upcoming else None
        number = None
        if channel.kind == "live":
            ids = [c.id for c in self.numbered_channels()]
            number = ids.index(channel.id) + 1 if channel.id in ids else None
        self.channel_banner.set_content(channel, number, programme, following)
        self._banner_placer.place()
        self.channel_banner.show_for(seconds)

    def _mark_loaded(self):
        if self._closed:
            return
        self.show_channel_banner()
        self._idle = False
        self._loading = False
        self.transport.loaded()
        self.track_preferences.loaded()
        self._show_track_notice()
        self.media_info.mark_loaded()
        self.refresh_media_info()
        self.info_button.setEnabled(self.current is not None)
        # mpv only reports pause changes; a new file starting unpaused sends none.
        self.play_button.setText("▶" if self._playback_paused else "Ⅱ")
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
            self._playback_paused = bool(value)
            self.recovery.paused(token, bool(value))
            self._sync_playback_state()
        elif name == "paused-for-cache":
            self.recovery.buffering(token, bool(value))
        elif name == "track-list" and self._playback_active:
            self._update_tracks(value)

    def _update_tracks(self, value):
        self._tracks = value if isinstance(value, list) else []
        self.track_preferences.update_tracks(self._tracks)
        if not self._loading:
            self._show_track_notice()

    def _show_track_notice(self):
        if not self._closed and (notice := self.track_preferences.take_notice()):
            self.language_notice.setText(notice)
            self.language_notice.show()
            self._language_notice_timer.start()

    def playback_finished(self, token, reason, message):
        if token != self._playback_token or not self._playback_active:
            return
        recovery_handled = self.recovery.failure(token, reason)
        if self.current and self.current.kind != "live" and not self._loading:
            self.save_progress()
        self._finish_playback()
        if reason == "eof" and not self.sleep_timer.media_ended():
            self.offer_next_episode()
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
            self._playback_paused = False
            self._sync_playback_state()
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
            self.mpris.set_position(round(self._position * 1e6))
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
            self._sync_mpris()
        elif name == "seekable":
            self._seekable = bool(value)
            self.seek.setEnabled(self._seekable and self._duration > 0)
            self._sync_mpris()
        elif name == "seeking" and not value and self._playback_active:
            # A seek has landed: media controls learn the new position.
            self.mpris.seeked(round(self._position * 1e6))
        elif name == "pause":
            self.play_button.setText("▶" if value else "Ⅱ")
            if self._playback_active and self._untracked_playback_token == self._playback_token:
                # Players without playlist entry ids report pause only here.
                self._playback_paused = bool(value)
                self._sync_playback_state()
        elif name == "mute":
            self.mute_button.setText("Sessiz" if value else "Ses")
        elif name == "volume" and value is not None:
            self.volume.blockSignals(True)
            self.volume.setValue(round(value))
            self.volume.blockSignals(False)
        elif name == "track-list":
            if self._untracked_playback_token == self._playback_token:
                self._update_tracks(value)
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
            self.model.set_progress(self.current.id, self._position, self._duration)
            self.refresh_home()

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
            self.model.replace_progress(self.store.progress_map())
        self.proxy.set_recent_ids(self.store.recent_ids())
        self.filter_changed()
        self.refresh_home()
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

    # Watching comforts: next episode, sleep timer, channel numbers.

    def _notice(self, kind, text, primary=None, secondary=None):
        self._notice_kind = kind
        self.watch_notice.present(text, primary, secondary)

    def _clear_notice(self, kind=None):
        if kind is None or self._notice_kind == kind:
            self._notice_kind = None
            self.watch_notice.hide()

    def offer_next_episode(self):
        """After an episode ends: the next one starts by itself after a short countdown."""
        following = next_episode(self.model.channels, self.current)
        if following is None:
            return False
        if selected_setting(self.store, "autoplay_next", AUTOPLAY_CHOICES) != "on":
            self._notice(
                "next",
                f"Sonraki bölüm: {following.name}",
                ("Oynat", lambda: self.play_next_episode(following)),
                ("Kapat", lambda: self._clear_notice("next")),
            )
            return True
        self.next_countdown.start(following)
        return True

    def _next_episode_tick(self, seconds):
        following = self.next_countdown.payload
        self._notice(
            "next",
            f"Sonraki bölüm {seconds} saniye içinde: {following.name}",
            ("Şimdi oynat", self.next_countdown.finish_now),
            ("İptal", self.cancel_next_episode),
        )

    def cancel_next_episode(self):
        self.next_countdown.cancel()
        self._clear_notice("next")

    def play_next_episode(self, channel):
        self._clear_notice("next")
        fresh = next((c for c in self.model.channels if c.id == channel.id), None)
        if fresh is not None:
            # The series was already open, so this episode needs no PIN again.
            self.request_play(fresh, approved=True)

    def sleep_menu(self):
        menu = self.build_sleep_menu()
        menu.exec(self.sleep_button.mapToGlobal(self.sleep_button.rect().topLeft()))
        menu.deleteLater()

    def build_sleep_menu(self):
        menu = QMenu(self)
        menu.setTitle("Uyku zamanlayıcısı")
        if self.sleep_timer.active:
            remaining = (
                "Bitince duracak"
                if self.sleep_timer.mode == "media"
                else f"Kalan: {self.sleep_timer.label()}"
            )
            menu.addAction(remaining).setEnabled(False)
            menu.addAction("15 dakika uzat", lambda: self.sleep_timer.extend(15))
            menu.addAction("Kapat", self.sleep_timer.cancel)
            menu.addSeparator()
        for minutes in SLEEP_CHOICES:
            menu.addAction(f"{minutes} dakika sonra", lambda m=minutes: self.start_sleep(m))
        if self.current is not None and self._playback_active:
            if self.current.kind == "live":
                programme = self.programme_now(self.current)
                if programme is not None:
                    end = programme.end.timestamp()
                    menu.addAction("Bu program bitince", lambda: self.start_sleep_until(end))
            elif self._duration > 0:
                label = "Bu bölüm bitince" if self.current.series_id else "Bu film bitince"
                menu.addAction(label, self.start_sleep_at_media_end)
        return menu

    def start_sleep(self, minutes):
        self.sleep_timer.start(minutes)
        self.status(f"Uyku zamanlayıcısı: {minutes} dakika sonra oynatma duracak.")

    def start_sleep_at_media_end(self):
        self.sleep_timer.start_at_media_end()
        self.status("Uyku zamanlayıcısı: bitince oynatma duracak.")

    def start_sleep_until(self, moment):
        self.sleep_timer.start_until(moment)
        self.status("Uyku zamanlayıcısı: bitince oynatma duracak.")

    def refresh_sleep_button(self):
        active = self.sleep_timer.active
        self.sleep_label.setText(self.sleep_timer.label())
        self.sleep_label.setVisible(active)
        self.sleep_button.setToolTip(
            (
                "Uyku zamanlayıcısı: bitince duracak"
                if self.sleep_timer.mode == "media"
                else f"Uyku zamanlayıcısı: {self.sleep_timer.label()} kaldı"
            )
            if active
            else "Uyku zamanlayıcısı"
        )
        if active and self.sleep_timer.mode != "media":
            self._sleep_label_timer.start()
        else:
            self._sleep_label_timer.stop()

    def sleep_expired(self):
        self.cancel_next_episode()
        if self._playback_active:
            self.stop_playback()
        self.leave_fullscreen()
        self.status("Uyku zamanlayıcısı: oynatma durduruldu. İyi geceler.")

    def numbered_channels(self):
        """Live channels in list order, of the chosen source; their 1-based place is the number."""
        source = self.proxy.source
        return [
            c
            for c in self.model.channels
            if c.kind == "live"
            and not self.proxy.category_hidden(c)
            and (not source or c.id.startswith(source + ":"))
            and not (self.proxy.hide_locked and c.id in self.model.locked)
        ]

    def _number_typing(self, digits):
        self._notice("number", f"Kanal {digits}_")

    def jump_to_number(self, number):
        channels = self.numbered_channels()
        if number > len(channels):
            self._notice("number", f"{number} numaralı kanal yok (1–{len(channels)}).")
            QTimer.singleShot(2500, lambda: self._clear_notice("number"))
            return False
        channel = channels[number - 1]
        self._notice("number", f"{number} · {channel.name}")
        QTimer.singleShot(2500, lambda: self._clear_notice("number"))
        self.request_play(channel)
        return True

    def close_current(self):
        """Stop and forget the current channel, back to the welcome screen."""
        self.channel_banner.hide_banner()
        self.stop_playback()
        self.current = None
        self.watch_panel.sync()
        self._current_persistent = False
        self.favorite_button.setEnabled(False)
        self.video_stack.setCurrentIndex(0)
        self.video_title.setText("İyi bir yayına yer aç.")

    def stop_playback(self):
        self.cancel_next_episode()  # Stop, profile switches and closing end the countdown too
        self.dismiss_resume()
        self.save_progress()
        self.recovery.cancel()
        self._untracked_playback_token = None
        self._playback_active = False
        self._playback_paused = False
        self._sync_playback_state()
        self.track_preferences.finish()
        self._language_notice_timer.stop()
        self.language_notice.hide()
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

    def alternative_channel(self, channel, tried=()):
        """The same live channel in another source: same guide id, else the same plain name."""
        if channel is None or channel.kind != "live":
            return None
        source = channel.id.split(":", 1)[0]
        name = channel_key(channel.name)
        for candidate in self.model.channels:
            if (
                candidate.kind != "live"
                or candidate.id in tried
                or candidate.id.split(":", 1)[0] == source
                or candidate.id in self.model.locked
                or self.proxy.category_hidden(candidate)
            ):
                continue
            same_guide = channel.tvg_id and candidate.tvg_id == channel.tvg_id
            if same_guide or (name and channel_key(candidate.name) == name):
                return candidate
        return None

    def _fail_over(self):
        """A live channel that will not open is tried once in each other source that has it."""
        failed = self.current
        tried = self._failover_tried | {failed.id}
        alternative = self.alternative_channel(failed, tried)
        if alternative is None:
            return False
        source = self.source_for(alternative)
        self._failover_tried = tried
        note = f"{failed.name} açılmadı; {source['name'] if source else 'diğer kaynak'} üzerinden açılıyor."

        def switch():
            self.request_play(alternative, failover=True)
            self.status(note)  # after play's own "bağlanılıyor" line, so it is the one seen

        QTimer.singleShot(0, switch)
        return True

    def refresh_recovery(self):
        if self._closed:
            return
        terminal = self.recovery.state in {"failed", "untracked-failed"}
        if (
            terminal
            and self.current is not None
            and self.current.kind == "live"
            and self._playback_active
            and self._fail_over()
        ):
            self._finish_playback()
            return
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
        self._sync_message_bar()
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
        self.toggle_channel_favorite(self.current)

    def toggle_channel_favorite(self, channel):
        favorite = channel.id not in self.store.favorites()
        self.store.set_favorite(channel.id, favorite)
        self.refresh_favorites()
        self.status(
            f"{channel.name} favorilere eklendi."
            if favorite
            else f"{channel.name} favorilerden çıkarıldı.",
            icon="check",
        )

    def refresh_favorites(self):
        self.model.favorites = self.store.favorites()
        if self.model.rowCount():
            self.model.dataChanged.emit(
                self.model.index(0), self.model.index(self.model.rowCount() - 1)
            )
        if self.current:
            favorite = self.current.id in self.model.favorites
            self.favorite_button.setText("★" if favorite else "☆")
        self.refresh_home()
        self.details.refresh_favorite()
        self.refresh_folders()
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
        backup = menu.addAction("Yedekle…", self.export_backup)
        backup.setEnabled(not self._busy)
        restore = menu.addAction("Yedekten geri yükle…", self.restore_backup)
        restore.setEnabled(not self._busy)
        menu.addSeparator()
        menu.addAction("Kısayollar ve hakkında", self.about)
        menu.addAction("Hatırlatıcılar…", self.open_reminders)
        return menu

    def export_backup(self):
        from .backup_dialog import save_backup_dialog

        if not self.guard("Yedek almak için PIN gir."):
            return
        save_backup_dialog(self)

    def restore_backup(self):
        from .backup_dialog import restore_backup_dialog

        if not self.guard("Yedekten geri yüklemek için PIN gir."):
            return
        restore_backup_dialog(self)

    @staticmethod
    def _same_source(left, right):
        fields = ("id", "name", "type", "location", "username", "password", "epg_url")
        return all(str(left.get(field, "")) == str(right.get(field, "")) for field in fields)

    def edit_source(self, source):
        if source is None or self._busy or not self.guard("Bağlantıyı düzenlemek için PIN gir."):
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
        if source is None or not self.guard("Kaynağı yeniden adlandırmak için PIN gir."):
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
        if not self.guard("Kaynağı kaldırmak için PIN gir."):
            return
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
            self.close_current()
        account_dialog = self._account_dialog
        if account_dialog is not None:
            if not isValid(account_dialog):
                self._account_dialog = None
            elif getattr(account_dialog, "source_id", None) == source["id"]:
                account_dialog.close()
        self.store.remove_source(source["id"])
        self._guide_data.pop(source["id"], None)
        self._guide_index.pop(source["id"], None)
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
            self._guide_index[source["id"]] = GuideIndex(programmes)
            self._refresh_live_cards()
            if self.library_pages.currentWidget() is self.guide_view:
                self.refresh_guide_view()
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

    def _add_reminder_menu(self, menu, channel):
        index = self._guide_index.get(channel.id.split(":", 1)[0])
        upcoming = index.upcoming(channel.tvg_id, 20) if index and channel.kind == "live" else []
        if upcoming:
            reminders = menu.addMenu("Hatırlatıcı kur")
            for programme in upcoming:
                label = f"{programme.start.astimezone():%d.%m %H:%M} · {programme.title}"
                reminders.addAction(
                    label.replace("&", "&&"),
                    lambda programme=programme: self.remind_programme(channel, programme),
                )
        menu.addAction("Hatırlatıcılar…", self.open_reminders)

    def remind_programme(self, channel, programme):
        try:
            reminder_id = self.reminder_service.add(channel, programme)
        except ValueError as error:
            self.status(str(error))
            return None
        if reminder_id is not None:
            self.status(
                f"Hatırlatıcı kuruldu: {programme.title}, {programme.start.astimezone():%H:%M}",
                icon="check",
            )
        return reminder_id

    def guard(self, reason):
        """Ask for the PIN when one is set; every time, nothing is remembered."""
        return ask_pin(self.store, reason, self)

    def kids_profile(self):
        profile = self.store.profile(self.store.profile_id)
        return bool(profile and profile["kids"])

    def unlock_channel(self, channel, *, approved=False):
        """May this channel open now? approved: the PIN was just asked for it."""
        if channel.id not in self.model.locked:
            return True
        if self.kids_profile():
            self.status("Bu içerik bu profilde kapalı.")
            return False
        return approved or self.guard(f"“{channel.name}” kilitli. Açmak için PIN gir.")

    def locked_ids(self):
        """Channels behind the PIN: locked one by one or through their category."""
        if not self.store.pin_hash():
            return set()
        channels = self.store.locked_channels()
        groups = self.store.locked_groups()
        return {
            c.id
            for c in self.model.channels
            if c.id in channels or (c.id.split(":", 1)[0], c.group) in groups
        }

    def apply_locks(self, *, filter_now=True):
        self.model.set_locked(self.locked_ids())
        self.proxy.hide_locked = self.kids_profile() and bool(self.model.locked)
        if filter_now:
            self.refresh_categories()
            self.filter_changed()
            self._reminders_changed()
            self.refresh_home()
        if self.current and self.proxy.hide_locked and self.current.id in self.model.locked:
            self.close_current()

    def set_channel_locked(self, channel, locked):
        if not locked and not self.guard(f"“{channel.name}” kilidini kaldırmak için PIN gir."):
            return
        self.store.set_channel_locked(channel.id, locked)
        self.apply_locks()
        self.status(f"“{channel.name}” kilitlendi." if locked else "Kilit kaldırıldı.")

    def open_parental(self):
        if not self.guard("Ebeveyn denetimini açmak için PIN gir."):
            return
        dialog = ParentalDialog(self.store, self)
        dialog.changed.connect(self.apply_locks)
        dialog.changed.connect(self.refresh_profile_badge)
        dialog.exec()

    def open_profiles(self):
        if not self.guard("Profilleri yönetmek için PIN gir."):
            return
        dialog = ProfilesDialog(self.store, self)
        dialog.deleting.connect(self._deleting_profile)
        dialog.changed.connect(self.profiles_changed)
        dialog.exec()

    def profiles_changed(self):
        """A profile was edited or deleted; the active one may have changed under us."""
        self.load_profile()

    def refresh_profile_badge(self):
        self.profile_button.set_profile(self.store.profile(self.store.profile_id))

    def profile_menu(self):
        menu = self.build_profile_menu()
        menu.exec(self.profile_button.mapToGlobal(self.profile_button.rect().topRight()))
        menu.deleteLater()

    def build_profile_menu(self):
        menu = QMenu(self)
        for profile in self.store.profiles():
            action = menu.addAction(profile["name"])
            action.setCheckable(True)
            action.setChecked(profile["id"] == self.store.profile_id)
            action.triggered.connect(
                lambda checked=False, pid=profile["id"]: self.switch_profile(pid)
            )
        menu.addSeparator()
        menu.addAction("Profilleri yönet…", self.open_profiles)
        menu.addAction("Ebeveyn denetimi…", self.open_parental)
        return menu

    def switch_profile(self, profile_id):
        """Leaving a kids profile or entering a protected one asks for the PIN."""
        target = self.store.profile(profile_id)
        if target is None or profile_id == self.store.profile_id:
            return False
        if (target["protected"] or self.kids_profile()) and not self.guard(
            f"“{target['name']}” profiline geçmek için PIN gir."
        ):
            return False
        self.leave_profile()
        self.store.use_profile(profile_id)
        self.load_profile()
        self.status(f"{target['name']} profili açık.", icon="check")
        return True

    def leave_profile(self):
        """Before another profile becomes active: save and stop what this one watches."""
        self.details.dismiss()  # an open detail card was unlocked for this profile only
        if self.current is not None:
            self.close_current()

    def _deleting_profile(self, profile_id):
        if profile_id == self.store.profile_id:
            self.leave_profile()

    def load_profile(self):
        """Reload everything personal: favorites, folders, history, reminders, locks."""
        self.details.dismiss()
        self.reminder_service.reload()
        self.refresh_library()
        self.refresh_favorites()
        self.refresh_profile_badge()

    def start_session(self):
        if self._closed:
            return
        self.choose_profile_at_start()
        self.restore_last_channel()

    def choose_profile_at_start(self):
        """'Kim izliyor?' when there is more than one profile; protected ones need the PIN."""
        profiles = self.store.profiles()
        if len(profiles) > 1:
            picker = ProfilePicker(profiles, self.store.profile_id, self)
            if picker.exec() == QDialog.Accepted and picker.chosen != self.store.profile_id:
                if self.switch_profile(picker.chosen):
                    return self.store.profile_id
        return self.enter_active_profile()

    def enter_active_profile(self):
        """The profile left active last time; a protected one asks before it opens."""
        profile = self.store.profile(self.store.profile_id)
        if not (profile["protected"] and self.store.pin_hash()):
            return profile["id"]
        if self.guard(f"“{profile['name']}” profiline girmek için PIN gir."):
            return profile["id"]
        fallback = next((p for p in self.store.profiles() if not p["protected"]), None)
        if fallback is None:
            self.close()  # every profile is protected and the PIN was not given
            return None
        self.store.use_profile(fallback["id"])
        self.load_profile()
        self.status(f"{fallback['name']} profili açık.", icon="check")
        return fallback["id"]

    def _reminders_changed(self):
        """Rebuild the guide rows if the guide is on screen."""
        if self.library_pages.currentWidget() is self.guide_view:
            self.refresh_guide_view()

    def cancel_reminder(self, reminder_id):
        self.reminder_service.remove(reminder_id)

    def open_reminders(self):
        if self._reminders_dialog is None or not isValid(self._reminders_dialog):
            self._reminders_dialog = RemindersDialog(self.reminder_service, self.store, self)
        self._reminders_dialog.refresh()
        self._reminders_dialog.show()
        self._reminders_dialog.raise_()
        self._reminders_dialog.activateWindow()
        return self._reminders_dialog

    def _watch_reminder(self, channel_id):
        if self._closed:
            return
        channel = next((item for item in self.store.channels() if item.id == channel_id), None)
        if channel is None:
            self.status("Bu kanal artık kaynakta bulunmuyor.")
            return
        self.leave_mini_player()
        RemoteControl(self).raise_window()
        self.request_play(channel)

    def programme_now(self, channel):
        """What a live card shows as on now; cheap enough to call while painting."""
        if channel.kind != "live" or not channel.tvg_id:
            return None
        index = self._guide_index.get(channel.id.split(":", 1)[0])
        return index.now(channel.tvg_id) if index else None

    def _refresh_live_cards(self):
        if self._guide_index and not self._closed:
            self.channel_list.viewport().update()
            if self.library_pages.currentWidget() is self.home_view:
                self.home_view.refresh_programmes()

    def update_guide(self):
        if self._closed:
            return
        self._sync_mpris()
        if not self.current:
            return
        if self.current.kind != "live":
            self.now_title.setText("Kaldığın yer hatırlanır.")
            self.next_title.setText(
                "Ses, altyazı ve baştan başlatma seçenekleri Oynatma menüsünde."
            )
            return
        source = self.source_for(self.current)
        index = self._guide_index.get(source["id"]) if source else None
        now = index.now(self.current.tvg_id) if index else None
        upcoming = index.upcoming(self.current.tvg_id, 4) if index else []
        self.now_title.setText(
            f"ŞİMDİ  {now.start.astimezone():%H:%M} — {now.end.astimezone():%H:%M}   {now.title}"
            if now
            else "Bu kanal için güncel program bulunamadı."
        )
        self.next_title.setText(
            "SIRADA\n"
            + "\n".join(f"{item.start.astimezone():%H:%M}   {item.title}" for item in upcoming)
            if upcoming
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
        self.video_stack.updateGeometry()
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
        self.watch_panel.sync(animate=False)

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
            f"Luna IPTV {__version__}\nÖzgün, kişisel Linux IPTV istemcisi.\n\nCtrl+O  Kaynak ekle\nCtrl+F  Ara\nBoşluk  Oynat / duraklat\nF  Tam ekran\nM  Sesi aç / kapat\n← / →  5 saniye sar\nJ / L  Geri / ileri tara: 2×–16×\nK  Normal oynatmaya dön\nPage Up / Page Down  Önceki / sonraki kanal\n0–9  Kanal numarasıyla geç\nEsc  Tam ekrandan çık\n\nQt + libmpv · Native Wayland ve X11\nHesaplar yalnızca yerel diskte saklanır.",
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
        self.watch_panel.animation.stop()
        self.page_transition.finish()
        self.toast.dismiss(immediate=True)
        self.channel_list.set_loading(False)
        self.idle_inhibit.close()
        self.mpris.close()
        self.reminder_service.close()
        self.mini_player.close()
        self._source_edit_tokens.clear()
        self._source_health_tokens.clear()
        self.dismiss_resume()
        self.details.close()
        self.track_preferences.finish()
        self.fullscreen.close()
        self.transport.close()
        self.recovery.close()
        self._guide_timer.stop()
        self.logo_viewport.close()
        self.home_view.close_artwork()
        self.logos.close()
        self.posters.close()
        self.player.shutdown()
        self.store.close()
        event.accept()
