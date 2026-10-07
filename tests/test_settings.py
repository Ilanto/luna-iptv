"""Application defaults persist, respect source choices and update existing widgets."""

import json
import sqlite3

import pytest
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QFrame, QPushButton
from shiboken6 import isValid

from luna_iptv import motion
from luna_iptv.media_dialog import _LANGUAGE_PREFERENCES
from luna_iptv.models import Channel
from luna_iptv.preferences import DEFAULT_TRACK_OPTIONS, TrackPreferences
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "home", "name": "Home", "type": "m3u"})
    yield value
    value.close()


@pytest.fixture
def motion_policy(qt_app):
    motion.set_motion_level("full")
    yield
    motion.set_motion_level("full")


def test_settings_upgrade_and_json_round_trip(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = Store(path)
    old.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE IF EXISTS app_settings")
    store = Store(path)
    assert store.setting("missing", {"fallback": True}) == {"fallback": True}
    values = {"motion_level": "reduced", "nested": {"Türkçe": [True, None, 4]}}
    for key, value in values.items():
        store.set_setting(key, value)
    store.set_setting("motion_level", "off")
    store.close()
    reopened = Store(path)
    try:
        assert reopened.setting("motion_level", "full") == "off"
        assert reopened.setting("nested", None) == values["nested"]
        row = reopened._db.execute("SELECT value FROM app_settings WHERE key='nested'").fetchone()
        assert json.loads(row[0]) == values["nested"]
        reopened._db.execute("UPDATE app_settings SET value='broken' WHERE key='nested'")
        assert reopened.setting("nested", "fallback") == "fallback"
    finally:
        reopened.close()


def test_defaults_preserve_current_playback_behavior(store):
    preferences = TrackPreferences(store, None)
    assert preferences.begin("home") == DEFAULT_TRACK_OPTIONS


@pytest.mark.parametrize("subtitle, expected", [("off", "no"), ("en", "auto")])
def test_global_languages_reach_preview_and_load_options(store, subtitle, expected):
    store.set_setting("audio_language", "tr")
    store.set_setting("subtitle_language", subtitle)
    preferences = TrackPreferences(store, None)
    preview = preferences.preview("home")
    assert preview["audio"]["lang"] == "tr"
    assert preferences.preview(None)["audio"]["lang"] == "tr"
    options = preferences.begin("home")
    assert options["alang"] == "tr"
    assert options["sid"] == expected
    if subtitle == "en":
        assert options["slang"] == "en"
        assert options["subs-fallback"] == "no"
    assert store.playback_preferences("home") == {}
    store.set_setting("audio_language", "de")
    assert preferences.preview("home")["audio"]["lang"] == "de"
    assert preferences.current_choices()["audio"]["lang"] == "tr"


@pytest.mark.parametrize("choice", [{"mode": "track", "lang": "en"}, {"mode": "auto"}])
def test_source_choices_win_per_track_type(store, choice):
    store.set_setting("audio_language", "tr")
    store.set_setting("subtitle_language", "off")
    store.save_playback_preferences("home", {"audio": choice})
    preferences = TrackPreferences(store, None)
    preview = preferences.preview("home")
    assert preview["audio"]["mode"] == choice["mode"]
    assert preview["audio"].get("lang") == choice.get("lang")
    assert preview["sub"] == {"mode": "off"}
    assert preferences.begin("home")["alang"] == choice.get("lang", "")


def test_disabled_source_remembering_uses_global_defaults(store):
    store.set_setting("audio_language", "tr")
    store.save_playback_preferences(
        "home", {"remember": False, "audio": {"mode": "track", "lang": "en"}}
    )
    preferences = TrackPreferences(store, None)
    assert preferences.preview("home")["remember"] is False
    assert preferences.begin("home")["alang"] == "tr"
    assert store.playback_preferences("home")["audio"]["lang"] == "en"


def test_explicit_playback_choices_override_global_defaults(store):
    store.set_setting("audio_language", "tr")
    store.set_setting("subtitle_language", "off")
    preferences = TrackPreferences(store, None)
    assert (
        preferences.begin(
            "home", {"audio": {"mode": "auto"}, "sub": {"mode": "auto"}, "remember": False}
        )
        == DEFAULT_TRACK_OPTIONS
    )


@pytest.mark.parametrize("level", ["reduced", "off"])
def test_existing_hover_stops_and_future_hover_is_instant(motion_policy, level):
    button = motion.IconButton("Ses")
    button._animate(1.0)
    assert button._animation.state() == QAbstractAnimation.Running
    motion.set_motion_level(level)
    assert button._animation.state() == QAbstractAnimation.Stopped
    assert button._hover == 1.0
    button.leaveEvent(QEvent(QEvent.Leave))
    assert button._hover == 0.0
    center = button.rect().center()
    button.enterEvent(QEnterEvent(center, center, center))
    assert button._hover == 1.0
    assert button._animation.state() == QAbstractAnimation.Stopped
    motion.set_motion_level("full")
    button._animate(0.0)
    assert button._animation.state() == QAbstractAnimation.Running
    button.close()


@pytest.mark.parametrize("level", ["reduced", "off"])
def test_sky_and_logo_stop_and_paint_static_frames(motion_policy, qt_app, level):
    sky, logo = motion.MoonSky(), motion.LogoMark(46)
    sky.resize(350, 250)
    sky.show()
    logo.show()
    logo._start()
    assert sky.animating and logo.animating
    motion.set_motion_level(level)
    assert not sky.animating and not logo.animating
    first_sky, first_logo = sky.grab().toImage(), logo.grab().toImage()
    sky._clock.invalidate()
    logo._seconds += 10
    assert sky.grab().toImage() == first_sky
    assert logo.grab().toImage() == first_logo
    sky.hide()
    sky.show()
    logo._start()
    assert not sky.animating and not logo.animating
    motion.set_motion_level("full")
    assert sky.animating
    sky.hide()
    logo.set_quiet(True)
    motion.set_motion_level("off")
    motion.set_motion_level("full")
    assert not sky.animating and not logo.animating
    sky.close()
    logo.close()


def test_indicator_slides_in_reduced_and_jumps_immediately_in_off(motion_policy):
    parent = motion.NavFrame()
    first, second = QPushButton(parent), QPushButton(parent)
    first.setGeometry(0, 0, 60, 40)
    second.setGeometry(0, 80, 60, 40)
    parent.show()
    indicator = parent.indicator
    indicator.follow(first, animate=False)
    motion.set_motion_level("reduced")
    indicator.follow(second)
    assert indicator._animation.state() == QAbstractAnimation.Running
    motion.set_motion_level("off")
    assert indicator._animation.state() == QAbstractAnimation.Stopped
    assert indicator.geometry() == second.geometry()
    indicator.follow(first)
    assert indicator.geometry() == first.geometry()
    assert indicator._animation.state() == QAbstractAnimation.Stopped
    parent.close()


def test_motion_signal_tolerates_deleted_widgets(motion_policy):
    parent = QFrame()
    motion.IconButton("Ses", parent=parent)
    motion.LogoMark(32, parent)
    motion.MoonSky(parent)
    motion.NavIndicator(parent)
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    motion.set_motion_level("off")
    with pytest.raises(ValueError):
        motion.set_motion_level("unknown")


def test_settings_dialog_saves_immediately_and_reopens(store, motion_policy):
    from luna_iptv.settings_dialog import SettingsDialog

    button = motion.IconButton("Ses")
    button._animate(1.0)
    dialog = SettingsDialog(store)
    assert dialog.windowTitle() == "Ayarlar"
    assert dialog.findChildren(QFrame, "panel")
    assert dialog.motion_combo.currentData() == "full"
    assert dialog.startup_combo.currentData() == "select"
    for combo, subtitles in ((dialog.audio_combo, False), (dialog.subtitle_combo, True)):
        expected = ["Otomatik"] + (["Kapalı"] if subtitles else [])
        expected += [title for title, _ in _LANGUAGE_PREFERENCES]
        assert [combo.itemText(i) for i in range(combo.count())] == expected
    dialog.motion_combo.setCurrentIndex(dialog.motion_combo.findData("off"))
    assert store.setting("motion_level", "full") == "off"
    assert button._animation.state() == QAbstractAnimation.Stopped
    dialog.audio_combo.setCurrentIndex(dialog.audio_combo.findData("tr"))
    dialog.subtitle_combo.setCurrentIndex(dialog.subtitle_combo.findData("off"))
    dialog.startup_combo.setCurrentIndex(dialog.startup_combo.findData("play"))
    assert store.setting("audio_language", "auto") == "tr"
    assert store.setting("subtitle_language", "auto") == "off"
    assert store.setting("startup_action", "select") == "play"
    assert [b.text() for b in dialog.findChildren(QPushButton)] == ["Kapat"]
    dialog.close_button.click()
    reopened = SettingsDialog(store)
    assert reopened.audio_combo.currentData() == "tr"
    assert reopened.subtitle_combo.currentData() == "off"
    assert reopened.motion_combo.currentData() == "off"
    assert reopened.startup_combo.currentData() == "play"
    reopened.close()
    button.close()


@pytest.fixture
def make_window(store, qt_app, monkeypatch, motion_policy):
    windows = []
    loads = []
    from luna_iptv.player import Player

    monkeypatch.setattr(Player, "load", lambda self, *args, **kwargs: loads.append(args))
    monkeypatch.setattr(Player, "set_property", lambda *args: None)
    channels = store.replace_channels(
        "home",
        [
            Channel("live", "Live", "file:///live.ts"),
            Channel("film", "Film", "file:///film.mkv", kind="movie"),
        ],
    )

    def create(action="select", recent=True):
        store.set_setting("startup_action", action)
        store.set_setting("motion_level", "off")
        if recent:
            for channel in channels:
                store.save_progress(channel.id, 10, 0)
        window = MainWindow(store)
        windows.append(window)
        return window, loads

    yield create
    for window in windows:
        if isValid(window):
            window.close()
    qt_app.processEvents()


@pytest.mark.parametrize("action", ["select", "play", "none"])
def test_startup_action_and_saved_motion(make_window, qt_app, action):
    window, loads = make_window(action)
    qt_app.processEvents()
    assert not window.welcome_sky.animating
    selected = window.channel_list.currentIndex().data(Qt.UserRole)
    if action == "none":
        assert selected is None
        assert window.current is None and not loads
    else:
        assert selected.id == "home:live"
        assert bool(loads) == (action == "play")
        assert (window.current is not None) == (action == "play")
    if action == "play":
        window.restore_last_channel()
        assert len(loads) == 1


def test_startup_play_without_history_is_idle(make_window, qt_app):
    window, loads = make_window("play", recent=False)
    qt_app.processEvents()
    assert window.current is None and not loads


def test_settings_rail_button_reuses_open_dialog(make_window, qt_app):
    from luna_iptv.settings_dialog import SettingsDialog

    window, _ = make_window()
    assert window.settings_button.toolTip() == "Ayarlar"
    assert window.settings_button.icon_name() == "gear"
    layout = window.sidebar.layout()
    assert layout.indexOf(window.settings_button) + 1 == layout.indexOf(window.source_menu_button)
    window.settings_button.click()
    dialog = window._settings_dialog
    window.open_settings()
    assert window._settings_dialog is dialog
    assert len(window.findChildren(SettingsDialog)) == 1
    dialog.close()
    qt_app.processEvents()
    window.open_settings()
    assert window._settings_dialog.isVisible()


def test_pending_startup_callback_after_close_does_not_read_closed_store(make_window):
    window, loads = make_window("play")
    window.close()
    window.restore_last_channel()
    assert not loads


def test_invalid_saved_choices_fall_back_without_overwriting(store, motion_policy):
    from luna_iptv.settings import STARTUP_CHOICES, playback_defaults, selected_setting
    from luna_iptv.settings_dialog import SettingsDialog

    store.set_setting("motion_level", ["off"])
    store.set_setting("startup_action", "obsolete")
    store.set_setting("audio_language", {"lang": "tr"})
    store.set_setting("subtitle_language", None)
    dialog = SettingsDialog(store)
    assert dialog.motion_combo.currentData() == "full"
    assert dialog.startup_combo.currentData() == "select"
    assert selected_setting(store, "startup_action", STARTUP_CHOICES) == "select"
    assert playback_defaults(store) == {"audio": {"mode": "auto"}, "sub": {"mode": "auto"}}
    assert store.setting("motion_level", None) == ["off"]
    dialog.close()


def test_recovery_keeps_global_choices_without_remembering_them(make_window, qt_app):
    window, _ = make_window(recent=False)
    qt_app.processEvents()
    window.store.set_setting("audio_language", "tr")
    window.store.set_setting("subtitle_language", "off")
    channel = window.store.channels()[0]
    window.play(channel)
    assert window.store.playback_preferences("home") == {}
    window.store.set_setting("audio_language", "de")
    window.play(channel, recovering=True)
    assert window.track_preferences.current_choices()["audio"]["lang"] == "tr"
    assert window.track_preferences.current_choices()["sub"] == {"mode": "off"}
    assert window.store.playback_preferences("home") == {}
    assert window.track_preferences.preview("home")["audio"]["lang"] == "de"


def test_settings_dialog_renders_controls_within_its_panels(store, motion_policy, qt_app):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QComboBox, QLabel

    from luna_iptv import theme
    from luna_iptv.settings_dialog import SettingsDialog

    dialog = SettingsDialog(store)
    dialog.setStyleSheet(theme.STYLE)
    dialog.show()
    qt_app.processEvents()
    for widget in dialog.findChildren(QComboBox) + dialog.findChildren(QLabel):
        rect = QRect(widget.mapTo(dialog, QPoint()), widget.size())
        assert dialog.rect().contains(rect)
        if isinstance(widget, QLabel) and not widget.wordWrap():
            assert widget.width() >= widget.fontMetrics().horizontalAdvance(widget.text())
    assert dialog.grab().save(str(store.path.parent / "settings.png"))
    dialog.close()
