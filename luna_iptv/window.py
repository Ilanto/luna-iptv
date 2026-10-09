# Re-export original bindings for callers and runtime monkeypatches.
from __future__ import annotations

import gzip as gzip
import hashlib as hashlib
import math as math
from collections import Counter as Counter
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from datetime import timezone as timezone
from pathlib import Path as Path
from urllib.parse import unquote as unquote
from urllib.parse import urlsplit as urlsplit

from PySide6.QtCore import QEvent as QEvent
from PySide6.QtCore import Qt as Qt
from PySide6.QtCore import QThreadPool as QThreadPool
from PySide6.QtCore import QTimer as QTimer
from PySide6.QtCore import QUrl as QUrl
from PySide6.QtCore import Signal as Signal
from PySide6.QtGui import QDesktopServices as QDesktopServices
from PySide6.QtGui import QGuiApplication as QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView as QAbstractItemView,
)
from PySide6.QtWidgets import (
    QAbstractSlider as QAbstractSlider,
)
from PySide6.QtWidgets import (
    QApplication as QApplication,
)
from PySide6.QtWidgets import (
    QDialog as QDialog,
)
from PySide6.QtWidgets import (
    QInputDialog as QInputDialog,
)
from PySide6.QtWidgets import (
    QLineEdit as QLineEdit,
)
from PySide6.QtWidgets import (
    QMainWindow as QMainWindow,
)
from PySide6.QtWidgets import (
    QMenu as QMenu,
)
from PySide6.QtWidgets import (
    QMessageBox as QMessageBox,
)
from shiboken6 import isValid as isValid

from . import __version__ as __version__
from . import icons as icons
from . import theme as theme
from .accounts import sanitize_profile as sanitize_profile
from .auto_refresh import RefreshScheduler as RefreshScheduler
from .catchup import can_catchup as can_catchup
from .category_editor import CategoryEditor as CategoryEditor
from .channel_banner import BannerPlacer as BannerPlacer
from .channel_banner import ChannelBanner as ChannelBanner
from .comfort import ComfortPreferences as ComfortPreferences
from .dialogs import AccountDialog as AccountDialog
from .dialogs import GuideDialog as GuideDialog
from .dialogs import SourceDialog as SourceDialog
from .epg import GuideIndex as GuideIndex
from .epg import parse_xmltv as parse_xmltv
from .fullscreen import FullscreenController as FullscreenController
from .idle_inhibit import IdleInhibit as IdleInhibit
from .kids_limits import KidsLimitPanel as KidsLimitPanel
from .kids_limits import KidsLimits as KidsLimits
from .layout import build_window as build_window
from .library import (
    ChannelFilter as ChannelFilter,
)
from .library import (
    ChannelModel as ChannelModel,
)
from .library import (
    channel_key as channel_key,
)
from .library import (
    ordered_channels as ordered_channels,
)
from .library import (
    resumable as resumable,
)
from .library import (
    search_key as search_key,
)
from .media_controller import MediaDetailController as MediaDetailController
from .media_info import MediaInfo as MediaInfo
from .mini_player import MiniPlayerController as MiniPlayerController
from .models import Channel as Channel
from .models import Playlist as Playlist
from .models import Programme as Programme
from .motion import set_motion_level as set_motion_level
from .mpris import MprisService as MprisService
from .multiview import MultiViewWindow as MultiViewWindow
from .network import LIMIT as LIMIT
from .network import NetworkError as NetworkError
from .network import XtreamClient as XtreamClient
from .network import channel_id as channel_id
from .network import fetch as fetch
from .network import load_m3u as load_m3u
from .new_episodes import NewEpisodeService as NewEpisodeService
from .parental_ui import ParentalDialog as ParentalDialog
from .parental_ui import ask_pin as ask_pin
from .playback_dialogs import HistoryDialog as HistoryDialog
from .playback_dialogs import ResumeDialog as ResumeDialog
from .player import Player as Player
from .preferences import TrackPreferences as TrackPreferences
from .preferences import normalize_preferences as normalize_preferences
from .profiles_ui import ProfilePicker as ProfilePicker
from .profiles_ui import ProfilesDialog as ProfilesDialog
from .recordings import RecordingService as RecordingService
from .recordings_dialog import RecordingsDialog as RecordingsDialog
from .recovery import RecoveryController as RecoveryController
from .reminders import ReminderService as ReminderService
from .reminders_dialog import RemindersDialog as RemindersDialog
from .settings import AUTOPLAY_CHOICES as AUTOPLAY_CHOICES
from .settings import MOTION_CHOICES as MOTION_CHOICES
from .settings import STARTUP_CHOICES as STARTUP_CHOICES
from .settings import selected_setting as selected_setting
from .settings_dialog import SettingsDialog as SettingsDialog
from .shell_motion import PageTransition as PageTransition
from .shell_motion import WatchPanelController as WatchPanelController
from .source_connections import HealthResult as HealthResult
from .source_connections import check_connection as check_connection
from .source_connections import validate_candidate as validate_candidate
from .statistics import StatisticsDialog as StatisticsDialog
from .statistics import WatchTracker as WatchTracker
from .subtitle_dialog import SubtitleController as SubtitleController
from .tasks import Task as Task
from .timeshift import cache_minutes as cache_minutes
from .toast import Toast as Toast
from .transport import TransportController as TransportController
from .tray import TrayController as TrayController
from .tv_mode import TvModeWindow as TvModeWindow
from .updates import UpdateChecker as UpdateChecker
from .watching import SLEEP_CHOICES as SLEEP_CHOICES
from .watching import Countdown as Countdown
from .watching import NumberEntry as NumberEntry
from .watching import SleepTimer as SleepTimer
from .watching import next_episode as next_episode
from .window_parts.extras import ExtrasMixin as ExtrasMixin
from .window_parts.guide import GuideMixin as GuideMixin
from .window_parts.library import LibraryMixin as LibraryMixin
from .window_parts.lifecycle import LifecycleMixin as LifecycleMixin
from .window_parts.playback import PlaybackMixin as PlaybackMixin
from .window_parts.profiles import ProfilesMixin as ProfilesMixin
from .window_parts.settings import SettingsMixin as SettingsMixin
from .window_parts.sources import SourcesMixin as SourcesMixin
from .window_parts.watching import WatchingMixin as WatchingMixin


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
        self._window.tray.show_window()


class MainWindow(
    SourcesMixin,
    GuideMixin,
    LibraryMixin,
    PlaybackMixin,
    WatchingMixin,
    ProfilesMixin,
    ExtrasMixin,
    SettingsMixin,
    LifecycleMixin,
    QMainWindow,
):
    playback_started = Signal(object)

    def __init__(self, store, *, ask_profile=False, tray_available=None):
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
        self._quitting = False
        self._buffering = False
        self.multiview = None
        self.tv_mode = None
        self._statistics_dialog = None
        self._busy = False
        self._importing = False
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
        self.comfort_preferences = ComfortPreferences(store, self.player)
        self.watch_tracker = WatchTracker(store)
        build_window(self)
        self._language_notice_timer = QTimer(self)
        self._language_notice_timer.setSingleShot(True)
        self._language_notice_timer.setInterval(7000)
        self._language_notice_timer.timeout.connect(self.language_notice.hide)
        self.details = MediaDetailController(self)
        self.subtitles = SubtitleController(self)
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
        self.reminder_service = ReminderService(
            self.store,
            self._watch_reminder,
            self.status,
            self,
            switch_profile=lambda profile_id: self.switch_profile(profile_id),
        )
        self.reminder_service.changed.connect(self._reminders_changed)
        self._recordings_dialog = None
        self.recording_service = RecordingService(
            self.store,
            self,
            is_locked=lambda c: c.id in self.locked_ids(),
            kids=self.kids_profile,
            authorize=self.unlock_channel,
        )
        self.updates = UpdateChecker(self.store, self)
        self.updates.found.connect(self.update_found)
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
        self.tray = TrayController(self, available=tray_available)
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(30000)
        self._watch_timer.timeout.connect(self.watch_tracker.flush)
        self._watch_timer.start()
        self.refresh_library()
        self.refresh_profile_badge()
        self.kids_limit_panel = KidsLimitPanel(self, self.extend_kids_time, self.pick_limit_profile)
        self.kids_limits = KidsLimits(
            self.store,
            self.watch_tracker.flush,
            self.close_current,
            self.show_kids_limit,
            self.toast.show_message,
            self,
        )
        self.kids_limits.reload()
        self.new_episodes = NewEpisodeService(
            self.store,
            self.run_task,
            self.watch_new_episode,
            self.switch_profile,
            self.toast.show_message,
            self,
        )
        self.new_episodes.changed.connect(self.refresh_library)
        self._guide_timer = QTimer(self)
        self._guide_timer.setInterval(30000)
        self._guide_timer.timeout.connect(self.update_guide)
        self._guide_timer.timeout.connect(self._refresh_live_cards)
        self._guide_timer.start()
        QTimer.singleShot(0, self.load_cached_guides)
        QTimer.singleShot(0, self.start_session if ask_profile else self.restore_last_channel)
        self.refresh_scheduler = RefreshScheduler(
            self.store,
            self.import_source,
            self._auto_refresh_available,
            self.toast.show_message,
            self,
        )
        self.recovery.changed.connect(self._wake_refresh)
        self.refresh_scheduler.refreshed.connect(self.new_episodes.refresh)
        self.refresh_scheduler.start()
