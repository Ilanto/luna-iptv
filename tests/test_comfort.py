"""Playback comfort, tray lifecycle, actual watch time and first-run guidance."""

import math
from datetime import date, datetime, timedelta

import pytest
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "comfort.sqlite3")
    value.save_source({"id": "one", "name": "One", "type": "m3u"})
    value.save_source({"id": "two", "name": "Two", "type": "m3u"})
    value.replace_channels(
        "one",
        [
            Channel("live", "Canlı", "file:///live"),
            Channel("film", "Film", "file:///film", kind="movie"),
        ],
    )
    yield value
    value.close()


class Commands:
    def __init__(self):
        self.values, self.commands = [], []

    def set_property(self, name, value):
        self.values.append((name, value))

    def command(self, args):
        self.commands.append(args)


@pytest.mark.parametrize(
    "key,value,expected",
    [
        *[
            ("aspect", v, [("video-aspect-override", a), ("panscan", p)])
            for v, a, p in [
                ("auto", "-1", 0),
                ("16:9", "16:9", 0),
                ("4:3", "4:3", 0),
                ("21:9", "21:9", 0),
                ("fill", "-1", 1),
            ]
        ],
        *[("zoom", v, [("video-zoom", math.log2(1 + v / 100))]) for v in (0, 10, 20)],
        *[(k, v, [(k, v)]) for k in ("brightness", "contrast") for v in (-20, -10, 0, 10, 20)],
        *[("boost", v, [("volume-max", v), ("volume", v)]) for v in (100, 130, 160)],
    ],
)
def test_adjustment_commands_persist_and_reload(store, key, value, expected):
    from luna_iptv.comfort import ComfortPreferences

    player = Commands()
    prefs = ComfortPreferences(store, player)
    prefs.begin("one")
    prefs.select(key, value)
    assert player.values == expected
    assert store.playback_preferences("one")["comfort"][key] == value
    prefs.begin("two")
    assert prefs.values[key] == prefs.defaults[key]
    player.values.clear()
    prefs.begin("one")
    prefs.loaded()
    for item in expected:
        assert item in player.values


def test_normalization_filter_reset_and_track_preservation(store):
    from luna_iptv.comfort import ComfortPreferences
    from luna_iptv.preferences import TrackPreferences, normalize_preferences

    assert (
        normalize_preferences(
            {
                "comfort": {
                    "af": "bad",
                    "boost": 999,
                    "zoom": True,
                    "brightness": 5,
                    "normalize": "yes",
                },
                "secret": 1,
            }
        )
        == {}
    )
    player = Commands()
    tracks = TrackPreferences(store, player)
    tracks.begin("one")
    prefs = ComfortPreferences(store, player)
    prefs.begin("one")
    prefs.select("normalize", True)
    assert player.commands[-1] == ["af", "add", "@luna-norm:dynaudnorm"]
    tracks.select("sub", None)
    assert store.playback_preferences("one")["comfort"]["normalize"] is True
    prefs.select("normalize", False)
    assert player.commands[-1] == ["af", "remove", "@luna-norm"]
    prefs.select("brightness", 20)
    prefs.reset()
    assert "comfort" not in store.playback_preferences("one")
    assert store.playback_preferences("one")["sub"] == {"mode": "off"}
    assert ("brightness", 0) in player.values
    tracks.set_remember(False)
    prefs.select("contrast", 20)
    assert "comfort" not in store.playback_preferences("one")
    prefs.begin("one")
    assert prefs.values["contrast"] == 0


def test_week_aggregation_is_separate_from_progress_order(store):
    today = date(2026, 10, 9)
    store.save_progress("one:film", 10000, 20000)
    store.add_watch_time("one:live", "2026-10-05", 3600)
    store.add_watch_time("one:live", "2026-10-05", 1800)
    store.add_watch_time("one:film", "2026-10-09", 1800)
    store.add_watch_time("one:film", "2026-10-04", 999)
    stats = store.watch_statistics(today)
    assert stats["week_seconds"] == 7200
    assert len(stats["days"]) == 7
    assert stats["days"][-1] == ("2026-10-09", 1800)
    assert stats["top"][0] == ("Canlı", 5400)
    assert stats["live_seconds"] == 5400 and stats["vod_seconds"] == 1800
    profile = store.create_profile("Other", "#a9b8ff")
    store.use_profile(profile)
    assert store.watch_statistics(today)["week_seconds"] == 0


def test_watch_clock_pause_midnight_and_profile_isolation(store):
    from luna_iptv.statistics import WatchTracker

    now = [datetime(2026, 10, 8, 23, 59, 50)]
    elapsed = [0.0]
    tracker = WatchTracker(store, monotonic=lambda: elapsed[0], clock=lambda: now[0])

    def advance(seconds):
        elapsed[0] += seconds
        now[0] += timedelta(seconds=seconds)

    tracker.begin("one:live")
    advance(60)  # loading is not watching
    tracker.set_running(True)
    now[0] = datetime(2026, 10, 8, 23, 59, 50)
    tracker.flush()
    advance(20)
    tracker.set_running(False)
    advance(120)
    tracker.flush()
    assert store.watch_statistics(date(2026, 10, 9))["days"][-2:] == [
        ("2026-10-08", 10),
        ("2026-10-09", 10),
    ]
    tracker.set_running(True)
    advance(30)
    original = store.profile_id
    store.use_profile(store.create_profile("Other", "#a9b8ff"))
    tracker.finish()
    assert store.watch_statistics(date(2026, 10, 9))["week_seconds"] == 0
    store.use_profile(original)
    assert store.watch_statistics(date(2026, 10, 9))["week_seconds"] == 50


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    from luna_iptv.window import MainWindow

    value = MainWindow(Store(tmp_path / "window.sqlite3"), tray_available=True)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    monkeypatch.setattr(value.player, "command", lambda *a: None)
    yield value
    if isValid(value):
        value.quit_application()
    qt_app.processEvents()


def test_tray_close_show_quit_and_setting(window, qt_app):
    window.store.set_setting("close_to_tray", True)
    window.show()
    assert not window.close()
    assert not window._closed and window.isHidden()
    assert window.tray.notified
    window.tray.show_action.trigger()
    assert window.isVisible()
    window.tray.quit_action.trigger()
    assert window._closed


def test_onboarding_progress_and_skip(window):
    card = window.home_view.onboarding
    assert card.current_step == 0 and not card.isHidden()
    window.store.save_source({"id": "demo", "name": "Demo", "type": "m3u"})
    window.refresh_library()
    assert card.current_step == 1
    window.store.save_source(
        {"id": "demo", "name": "Demo", "type": "m3u", "epg_url": "file:///guide"}
    )
    window.refresh_home()
    assert card.current_step == 2
    card.skip_button.click()
    assert card.isHidden() and window.store.setting("onboarding_done") is True


def test_statistics_dialog_renders(store, qt_app):
    from luna_iptv.statistics import StatisticsDialog

    store.add_watch_time("one:film", date.today().isoformat(), 3600)
    dialog = StatisticsDialog(store)
    dialog.show()
    qt_app.processEvents()
    assert not dialog.grab().isNull()
    assert "1" in dialog.total_label.text()
    assert dialog.chart.accessibleDescription()
    dialog.close()


def seed_window(window):
    window.store.save_source({"id": "demo", "name": "Demo", "type": "m3u"})
    channels = [Channel(f"c{i}", f"Kanal {i}", f"file:///c{i}") for i in range(7)]
    channels.append(Channel("film", "Film", "file:///film", kind="movie"))
    window.store.replace_channels("demo", channels)
    window.refresh_library()
    return {c.id: c for c in window.model.channels}


def menu_action(menu, *path):
    action = next(a for a in menu.actions() if a.text() == path[0])
    return menu_action(action.menu(), *path[1:]) if len(path) > 1 else action


def test_playback_menu_load_reset_and_stale_actions(window, monkeypatch):
    channels = seed_window(window)
    commands = Commands()
    monkeypatch.setattr(window.player, "set_property", commands.set_property)
    monkeypatch.setattr(window.player, "command", commands.command)
    monkeypatch.setattr(window.player, "reserve_load", lambda: 42)
    window.play(channels["demo:c0"])
    window.loaded()
    menu = window.build_track_menu()
    menu_action(menu, "Görüntü oranı", "Doldur").trigger()
    menu_action(menu, "Parlaklık/Kontrast", "Parlaklık", "+20").trigger()
    menu_action(menu, "Ses güçlendirme", "160%").trigger()
    menu_action(menu, "Sesi dengele").trigger()
    assert "Sesi dengele: Açık" == window.mini_status.text()
    assert ("volume", 160) in commands.values
    commands.values.clear()
    window.play(channels["demo:c1"])
    window.loaded()
    assert ("panscan", 1) in commands.values
    assert ("brightness", 20) in commands.values
    assert ("volume", 160) in commands.values
    menu_action(menu, "Görüntü oranı", "4:3").trigger()
    assert window.comfort_preferences.values["aspect"] == "fill"
    menu.deleteLater()
    menu = window.build_track_menu()
    menu_action(menu, "Varsayılana dön").trigger()
    assert "comfort" not in window.store.playback_preferences("demo")
    assert commands.commands[-1] == ["af", "remove", "@luna-norm"]
    assert ("video-aspect-override", "-1") in commands.values
    menu.deleteLater()


def test_window_watch_time_gates_pause_buffer_load_stop_and_stale_events(window, monkeypatch):
    from luna_iptv.statistics import WatchTracker

    channels = seed_window(window)
    elapsed = [0.0]
    window.watch_tracker = WatchTracker(window.store, monotonic=lambda: elapsed[0])
    monkeypatch.setattr(window.player, "reserve_load", lambda: 42)
    window.play(channels["demo:c0"])
    elapsed[0] = 30
    window.loaded()
    elapsed[0] = 40
    window.playback_property(42, "pause", True)
    elapsed[0] = 100
    window.playback_property(42, "pause", False)
    elapsed[0] = 120
    window.playback_property(42, "paused-for-cache", True)
    elapsed[0] = 200
    window.playback_property(42, "paused-for-cache", False)
    window.playback_property(41, "pause", True)  # old stream cannot stop accounting
    window.player_property("time-pos", 9000)  # seek isn't watch time
    elapsed[0] = 230
    window.stop_playback()
    elapsed[0] = 300
    window.watch_tracker.flush()
    assert window.store.watch_statistics()["week_seconds"] == pytest.approx(60)


def test_tray_recent_live_limit_actions_and_profile_refresh(window, monkeypatch):
    channels = seed_window(window)
    for cid in channels:
        window.store.save_progress(cid, 30, 0)
    window.tray.refresh()
    actions = window.tray.recent_menu.actions()
    assert [a.text() for a in actions] == [f"Kanal {i}" for i in (6, 5, 4, 3, 2)]
    requested = []
    monkeypatch.setattr(window, "request_play", requested.append)
    actions[0].trigger()
    assert requested == [channels["demo:c6"]]
    # Play/pause is the same command route as the normal transport.
    window.current = channels["demo:c0"]
    window._idle = False
    toggled = []
    monkeypatch.setattr(window.player, "pause_toggle", lambda: toggled.append(True))
    window.tray.refresh()
    assert not window.tray.title_action.isEnabled()
    assert window.tray.title_action.text() == "Kanal 0"
    window.tray.play_action.trigger()
    assert toggled == [True]
    window.store.use_profile(window.store.create_profile("Other", "#a9b8ff"))
    window.tray.refresh()
    assert not window.tray.recent_menu.actions()


def test_tray_unavailable_setting_disabled_and_close_quits(qt_app, tmp_path):
    from luna_iptv.settings_dialog import SettingsDialog
    from luna_iptv.window import MainWindow

    value = MainWindow(Store(tmp_path / "no-tray.sqlite3"), tray_available=False)
    value.store.set_setting("close_to_tray", True)
    dialog = SettingsDialog(value.store, value, tray_available=False)
    assert not dialog.close_to_tray.isEnabled()
    assert "kullanılamıyor" in dialog.close_to_tray.toolTip()
    assert value.close()
    assert value._closed


def test_tray_default_close_really_quits(window):
    assert window.store.setting("close_to_tray", False) is False
    assert window.close()
    assert window._closed


def test_close_to_tray_keeps_playback_and_notifies_once(window, monkeypatch):
    channels = seed_window(window)
    monkeypatch.setattr(window.player, "reserve_load", lambda: 42)
    window.play(channels["demo:c0"])
    window.loaded()
    window.store.set_setting("close_to_tray", True)
    notices = []
    monkeypatch.setattr(window, "status", notices.append)
    window.close()
    assert window._playback_active and window.watch_tracker.running
    window.tray.show_window()
    window.close()
    assert notices == ["Luna tepside çalışıyor"]


def test_onboarding_optional_guide_and_favorites_complete(window, monkeypatch):
    card = window.home_view.onboarding
    calls = []
    monkeypatch.setattr(window, "add_source", lambda: calls.append("source"))
    card.primary_button.click()
    assert calls == ["source"]
    seed_window(window)
    assert card.current_step == 1
    monkeypatch.setattr(window, "configure_guide", lambda: calls.append("guide"))
    card.primary_button.click()
    assert calls[-1] == "guide"
    card.guide_skip.click()
    assert card.current_step == 2
    card.primary_button.click()
    assert window.nav_buttons["live"].isChecked()
    window.store.set_favorite("demo:c0", True)
    card.refresh()
    assert card.isHidden() and window.store.setting("onboarding_done") is True
    card.refresh()
    assert card.isHidden()


def test_watch_data_survives_reopen_and_profile_delete_cascades(store):
    store.add_watch_time("one:live", date.today().isoformat(), 42)
    reopened = Store(store.path)
    assert reopened.watch_statistics()["week_seconds"] == 42
    reopened.close()
    profile = store.create_profile("Other", "#a9b8ff")
    store.use_profile(profile)
    store.add_watch_time("one:film", date.today().isoformat(), 50)
    store.delete_profile(profile)
    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM watch_log WHERE profile_id=?", (profile,)
        ).fetchone()[0]
        == 0
    )


def test_statistics_profile_action_opens_dialog(window):
    menu = window.build_profile_menu()
    menu_action(menu, "İstatistikler").trigger()
    assert window._statistics_dialog.isVisible()
    menu.deleteLater()


def test_recovery_wait_does_not_count_as_watching(window, monkeypatch):
    from luna_iptv.statistics import WatchTracker

    channels = seed_window(window)
    elapsed = [0.0]
    window.watch_tracker = WatchTracker(window.store, monotonic=lambda: elapsed[0])
    monkeypatch.setattr(window.player, "reserve_load", lambda: 42)
    window.play(channels["demo:c0"])
    window.loaded()
    elapsed[0] = 10
    window._finish_playback(end_session=False)
    elapsed[0] = 110
    window.watch_tracker.flush()
    assert window.store.watch_statistics()["week_seconds"] == 10


def test_explicit_normal_volume_reapplies_over_previous_slider_value(store):
    from luna_iptv.comfort import ComfortPreferences

    player = Commands()
    prefs = ComfortPreferences(store, player)
    prefs.begin("one")
    prefs.select("boost", 100)
    prefs.begin("one")
    player.values.clear()
    prefs.loaded(volume=70)
    assert ("volume", 100) in player.values


def test_native_libmpv_controls_preserve_unrelated_audio_filter(store, qt_app, monkeypatch):
    import mpv

    from luna_iptv.comfort import ComfortPreferences
    from luna_iptv.player import Player

    native = mpv.MPV
    monkeypatch.setattr(
        mpv, "MPV", lambda **kwargs: native(**(kwargs | {"vo": "null", "ao": "null"}))
    )
    player = Player()
    assert player._mpv is not None
    original_command = player.command
    futures = []

    def command(args):
        future = original_command(args)
        futures.append(future)
        return future

    monkeypatch.setattr(player, "command", command)

    def settle():
        for future in futures:
            assert future is not None
            future.result(timeout=3)
        futures.clear()

    try:
        player.command(["af", "add", "@other:lavfi=[volume=0.9]"])
        prefs = ComfortPreferences(store, player)
        prefs.begin("one")
        prefs.select("aspect", "21:9")
        prefs.select("zoom", 20)
        prefs.select("brightness", -20)
        prefs.select("contrast", 20)
        prefs.select("boost", 160)
        prefs.select("normalize", True)
        settle()
        backend = player._mpv
        assert backend.video_aspect_override == pytest.approx(21 / 9)
        assert backend.video_zoom == pytest.approx(math.log2(1.2))
        assert backend.brightness == -20 and backend.contrast == 20
        assert backend.volume_max == 160 and backend.volume == 160
        assert {f["label"] for f in backend.af} == {"other", "luna-norm"}
        prefs.select("normalize", False)
        settle()
        assert [f["label"] for f in backend.af] == ["other"]
        prefs.reset()
        settle()
        assert backend.video_zoom == 0 and backend.panscan == 0
        assert backend.volume_max == 100 and backend.volume == 100
        assert [f["label"] for f in backend.af] == ["other"]
    finally:
        player.shutdown()
        if player._termination:
            player._termination.join(timeout=5)


def test_onboarding_guide_button_selects_source_from_all_sources(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from luna_iptv.dialogs import GuideDialog

    seed_window(window)
    window.source_combo.setCurrentIndex(window.source_combo.findData(""))
    assert window.source_combo.currentData() == ""
    opened = []

    def execute(dialog):
        opened.append(window.source_combo.currentData())
        return QDialog.Rejected

    monkeypatch.setattr(GuideDialog, "exec", execute)
    window.home_view.onboarding.primary_button.click()
    assert opened == ["demo"]


def test_closing_current_clears_tray_title_before_next_profile(window):
    channels = seed_window(window)
    window.current = channels["demo:c0"]
    window.tray.refresh()
    window.close_current()
    assert window.current is None
    assert window.tray.title_action.text() == "Henüz yayın seçilmedi"
    assert not window.tray.play_action.isEnabled()


def test_hidden_video_consumes_frames_without_drawing_and_stops_after_shutdown(
    window, monkeypatch, qt_app
):
    from types import SimpleNamespace

    qt_app.processEvents()  # Drain the initial hide event before installing the render stand-in.
    video = window.video
    calls = []
    video._render = SimpleNamespace(
        update=lambda: calls.append("update") or True, render=lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(video, "isVisible", lambda: False)
    monkeypatch.setattr(video, "makeCurrent", lambda: calls.append("current"))
    monkeypatch.setattr(video, "doneCurrent", lambda: calls.append("done"))
    from PySide6.QtGui import QOpenGLContext

    context = object()
    monkeypatch.setattr(video, "context", lambda: context)
    monkeypatch.setattr(QOpenGLContext, "currentContext", lambda: context)
    try:
        video._frame_requested.emit()
        qt_app.processEvents()
        assert calls == ["current", "update", {"skip_rendering": True}, "done"]
        calls.clear()
        window.player._closed = True
        video._frame_requested.emit()
        qt_app.processEvents()
        assert calls == []
    finally:
        video._render = None
        window.player._closed = False


def test_legacy_buffer_clear_during_load_allows_watch_time(window, monkeypatch):
    channels = seed_window(window)
    monkeypatch.setattr(window.player, "reserve_load", lambda: 42)
    window.play(channels["demo:c0"])
    window._untracked_playback_token = 42
    window.player_property("paused-for-cache", True)
    window.player_property("paused-for-cache", False)
    window.loaded()
    assert not window._buffering
    assert window.watch_tracker.running
