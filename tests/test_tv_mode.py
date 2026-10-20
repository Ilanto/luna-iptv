"""Couch navigation with one inert backend, exclusively on Qt's offscreen platform."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from luna_iptv.models import Channel, Programme
from luna_iptv.motion import set_motion_level
from luna_iptv.parental import hash_pin
from luna_iptv.parental_ui import PinDialog, reset_failures
from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.tv_browser import CARD_H, CARD_W
from luna_iptv.tv_mode import TvModeWindow
from luna_iptv.window import MainWindow
from tests.test_mini_player import InertPlayer, InertVideo


class TvPlayer(InertPlayer):
    def __init__(self, parent):
        super().__init__(parent)
        self.loads = []
        self.token = 0

    def reserve_load(self):
        self.token += 1
        return self.token

    def load(self, *args, **kwargs):
        self.loads.append((args, kwargs))


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    import luna_iptv.layout as layout_module
    import luna_iptv.window as window_module

    assert QApplication.platformName() == "offscreen"
    monkeypatch.setattr(window_module, "Player", TvPlayer)
    monkeypatch.setattr(layout_module, "VideoWidget", InertVideo)
    apply_theme(qt_app)
    store = Store(tmp_path / "tv.sqlite3")
    source = store.save_source({"id": "tv", "name": "TV", "type": "m3u"})
    store.replace_channels(
        source,
        [
            Channel("a", "Ay", "https://example.test/a", group="Haber", tvg_id="a"),
            Channel("b", "Bulut", "https://example.test/b", group="Haber"),
            Channel("c", "Ceylan", "https://example.test/c", group="Haber"),
            Channel("d", "Deniz", "https://example.test/d", group="Belgesel"),
            Channel("film", "Film", "https://example.test/film", group="Sinema", kind="movie"),
            Channel("series", "Dizi", "", kind="series", series_id="1"),
            Channel("ep", "Bölüm 1", "https://example.test/ep", kind="movie", series_id="1"),
        ],
    )
    value = MainWindow(store, tray_available=False)
    value.show()
    qt_app.processEvents()
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()
    set_motion_level("full")
    reset_failures()


def enter(window, qt_app):
    window.toggle_tv_mode()
    qt_app.processEvents()
    return window.tv_mode


def key(tv, value):
    QTest.keyClick(tv, value)


def test_entry_navigation_tabs_rows_and_exit(window, qt_app):
    tv = enter(window, qt_app)
    assert tv.isFullScreen() and tv.row == -1
    assert [title for title, _ in tv.rows] == ["Haber", "Belgesel"]
    key(tv, Qt.Key_Down)
    assert tv.focused_channel().id == "tv:a"
    key(tv, Qt.Key_Right)
    assert tv.focused_channel().id == "tv:b"
    key(tv, Qt.Key_Down)
    assert tv.focused_channel().id == "tv:d"
    key(tv, Qt.Key_Up)
    assert tv.focused_channel().id == "tv:b"
    key(tv, Qt.Key_Up)
    key(tv, Qt.Key_Right)
    assert tv.tab == 1 and tv.row == -1
    key(tv, Qt.Key_Down)
    assert tv.focused_channel().kind == "movie"
    key(tv, Qt.Key_Backspace)
    assert tv.row == -1
    for _ in range(4):
        key(tv, Qt.Key_Right)
    assert tv.tab == 5
    key(tv, Qt.Key_Return)
    assert window.tv_mode is None


def test_enter_uses_request_play_and_zaps_in_focused_row(window, qt_app, monkeypatch):
    original = window.request_play
    request = Mock(wraps=original)
    monkeypatch.setattr(window, "request_play", request)
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    assert request.call_args.args[0].id == "tv:a"
    assert tv.playing and len(window.player.loads) == 1
    assert not tv.banner.isHidden() and not tv.controls.isHidden()
    key(tv, Qt.Key_Down)
    assert window.current.id == "tv:b"
    key(tv, Qt.Key_Up)
    assert window.current.id == "tv:a"
    key(tv, Qt.Key_Up)
    assert window.current.id == "tv:c"
    key(tv, Qt.Key_Return)
    assert tv.controls.isHidden() and tv.banner.isHidden()
    key(tv, Qt.Key_Return)
    assert not tv.banner.isHidden()


def test_back_hides_picture_then_stops_then_returns_to_tabs(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    key(tv, Qt.Key_Escape)
    assert not tv.playing and tv.background_audio and tv.browser.isVisible()
    assert ["stop"] not in window.player.commands
    assert "Ses sürüyor" in tv.toast.text()
    key(tv, Qt.Key_Backspace)
    assert not tv.background_audio
    assert ["stop"] in window.player.commands
    assert window.tv_mode is tv
    key(tv, Qt.Key_Backspace)
    assert tv.row == -1
    key(tv, Qt.Key_Escape)
    assert window.tv_mode is None


def test_hold_back_exits_without_short_back_action(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    QTest.keyPress(tv, Qt.Key_Backspace)
    assert tv._back_timer.isActive()
    repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier, "", True)
    QApplication.sendEvent(tv, repeat)
    assert tv._back_timer.isActive()
    # Exercise the real timer with a short interval; the production threshold is 1 s.
    assert tv._back_timer.interval() == 1000
    tv._back_timer.start(1)
    QTest.qWait(20)
    assert window.tv_mode is None and window._playback_active


def test_video_stack_and_backend_restore_on_repeated_exit(window, qt_app):
    video, player = window.video, window.player
    stack = window.video_stack
    parent = stack.parentWidget()
    index, minimum, policy = (
        window.view_layout.indexOf(stack),
        stack.minimumSize(),
        stack.sizePolicy(),
    )
    for _ in range(3):
        tv = enter(window, qt_app)
        assert stack.parentWidget() is tv
        assert video.parentWidget() is stack and window.player is player
        assert window.view_layout.indexOf(stack) == -1
        tv.close()
        assert stack.parentWidget() is parent
        assert window.view_layout.indexOf(stack) == index
        assert stack.minimumSize() == minimum and stack.sizePolicy() == policy
        assert window.isVisible() and window.video is video
        assert player.shutdown_count == 0
        qt_app.processEvents()
    window.close()
    assert player.shutdown_count == 1


def test_main_close_restores_before_shutdown(window, qt_app):
    parent = window.video_stack.parentWidget()
    tv = enter(window, qt_app)
    player = window.player
    window.close()
    assert tv._closed and window.video_stack.parentWidget() is parent
    assert player.shutdown_count == 1


def test_seek_only_vod_and_resume_without_small_dialog(window, qt_app, monkeypatch):
    tv = enter(window, qt_app)
    seek = Mock()
    monkeypatch.setattr(window.transport, "seek_relative", seek)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    key(tv, Qt.Key_Left)
    key(tv, Qt.Key_Right)
    seek.assert_not_called()
    tv.back()
    tv.back()
    tv.back()
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    monkeypatch.setattr(window, "resume_position", lambda _: 123)
    key(tv, Qt.Key_Return)
    assert window._resume_dialog is None
    assert window.player.loads[-1][1]["start"] == 123
    key(tv, Qt.Key_Left)
    key(tv, Qt.Key_Right)
    assert [call.args[0] for call in seek.call_args_list] == [-10, 10]


def test_digits_enter_timeout_invalid_and_cancel(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_2)
    assert "2_" in tv.toast.text()
    key(tv, Qt.Key_Return)
    assert window.current.id == "tv:b" and tv.playing
    key(tv, Qt.Key_4)
    tv.number_entry._timer.start(1)
    QTest.qWait(20)
    assert window.current.id == "tv:d"
    key(tv, Qt.Key_9)
    key(tv, Qt.Key_Return)
    assert window.current.id == "tv:d"
    assert "kanal yok" in tv.toast.text()
    key(tv, Qt.Key_3)
    tv.back()
    assert not tv.number_entry.digits and not tv.number_entry._timer.isActive()


def test_locks_pin_digits_enter_and_no_unlock_cache(window, qt_app):
    window.store.set_pin_hash(hash_pin("1234"))
    window.store.set_channel_locked("tv:a", True)
    window.apply_locks()
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    prompts = []

    def pin():
        dialog = QApplication.activeModalWidget()
        try:
            prompts.append(
                (
                    isinstance(dialog, PinDialog),
                    dialog.parentWidget() is tv,
                    dialog.field.hasFocus(),
                )
            )
            QTest.keyClicks(dialog.field, "1234")
            QTest.keyClick(dialog.field, Qt.Key_Return)
        finally:
            if dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(50, pin)
    key(tv, Qt.Key_Return)
    assert tv.playing and window.current.id == "tv:a"
    tv.back()
    QTimer.singleShot(50, pin)
    key(tv, Qt.Key_Return)
    assert len(prompts) == 2 and len(window.player.loads) == 2
    assert all(all(checks) for checks in prompts)
    assert not tv.number_entry.digits


def test_denied_pin_does_not_reveal_old_video(window, qt_app):
    window.store.set_pin_hash(hash_pin("1234"))
    window.store.set_channel_locked("tv:a", True)
    window.apply_locks()
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    key(tv, Qt.Key_Return)
    assert not tv.playing and not window.player.loads


def test_kids_hide_locked_and_reject_digits_and_direct_activation(window, qt_app, monkeypatch):
    window.store.set_pin_hash(hash_pin("1234"))
    window.store.set_channel_locked("tv:b", True)
    window.apply_locks()
    monkeypatch.setattr(window, "kids_profile", lambda: True)
    window.apply_locks()
    tv = enter(window, qt_app)
    assert "tv:b" not in [c.id for _, items in tv.rows for c in items]
    locked = next(c for c in window.model.channels if c.id == "tv:b")
    tv.play_channel(locked)
    assert not tv.playing and not window.player.loads
    key(tv, Qt.Key_2)
    key(tv, Qt.Key_Return)
    assert window.current.id == "tv:c"
    key(tv, Qt.Key_Down)
    assert window.current.id == "tv:d"


def test_zapping_skips_locked_channels(window, qt_app):
    window.store.set_pin_hash(hash_pin("1234"))
    window.store.set_channel_locked("tv:b", True)
    window.apply_locks()
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    key(tv, Qt.Key_Down)
    assert window.current.id == "tv:c"


def test_guide_now_next_and_channel_order(window, qt_app, monkeypatch):
    now = datetime.now(timezone.utc)
    programme = Programme("a", "Şimdi haber", now, now + timedelta(hours=1), "")
    following = Programme("a", "Sonra hava", now + timedelta(hours=1), now + timedelta(hours=2), "")
    monkeypatch.setattr(window, "programme_now", lambda _: programme)
    window._guide_index["tv"] = Mock(upcoming=lambda *_: [following])
    tv = enter(window, qt_app)
    for _ in range(3):
        key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    assert tv.tab == 3 and len(tv.rows) == 4
    assert "Şimdi haber" in tv.description(tv.focused_channel())
    assert "Sonra hava" in tv.description(tv.focused_channel())
    window._guide_index.clear()  # MainWindow's full guide has a separate richer index API.
    key(tv, Qt.Key_Return)
    key(tv, Qt.Key_Up)
    assert window.current.id == "tv:d"


@pytest.mark.parametrize("height,scale", [(720, 2 / 3), (1080, 1), (2160, 2)])
def test_screen_height_scales_cards_and_text(window, qt_app, height, scale):
    tv = TvModeWindow(window, screen_height=height)
    assert tv.scale == scale
    assert (CARD_W * tv.scale, CARD_H * tv.scale) == (456 * scale, 316 * scale)
    assert f"font-size: {round(26 * scale)}px" in tv.controls.styleSheet()
    tv.close()


@pytest.mark.parametrize("level,animated", [("full", True), ("reduced", False), ("off", False)])
def test_focus_motion_policy(window, qt_app, level, animated):
    tv = enter(window, qt_app)
    set_motion_level(level)
    key(tv, Qt.Key_Down)
    assert (tv.browser.animation.state() == QAbstractAnimation.Running) is animated
    set_motion_level("off")
    assert tv.browser.animation.state() == QAbstractAnimation.Stopped


def test_series_episodes_are_remote_accessible(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    assert tv.series.id == "tv:series" and tv.focused_channel().id == "tv:ep"
    key(tv, Qt.Key_Return)
    assert window.current.id == "tv:ep" and tv.playing


def test_empty_catalogue_and_late_reset(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    window.model.reset([], set())
    qt_app.processEvents()
    assert tv.rows == [] and tv.row == -1
    for button in (Qt.Key_Down, Qt.Key_Left, Qt.Key_Return):
        key(tv, button)
    assert not window.player.loads


def test_f11_and_profile_menu_entry(window, qt_app):
    menu = window.build_profile_menu()
    action = next(a for a in menu.actions() if "TV modu" in a.text())
    action.trigger()
    qt_app.processEvents()
    assert window.tv_mode is not None
    key(window.tv_mode, Qt.Key_F11)
    assert window.tv_mode is None
    menu.deleteLater()


def test_background_recovery_never_reopens_picture(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    tv.back()
    window.play(window.current, recovering=True)
    assert not tv.playing and tv.browser.isVisible()
    assert window._playback_active


def test_pin_close_during_modal_loop_cancels_play(window, qt_app, monkeypatch):
    window.store.set_pin_hash(hash_pin("1234"))
    window.store.set_channel_locked("tv:a", True)
    window.apply_locks()
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)

    def close_while_unlocking(_reason):
        tv.close()
        return True

    monkeypatch.setattr(window, "guard", close_while_unlocking)
    key(tv, Qt.Key_Return)
    assert window.tv_mode is None and not window.player.loads


def test_kids_limit_prevents_entry_and_restores_open_tv(window, qt_app, monkeypatch):
    check = Mock(return_value=False)
    monkeypatch.setattr(window.kids_limits, "check", check)
    window.toggle_tv_mode()
    assert window.tv_mode is None
    check.return_value = True
    tv = enter(window, qt_app)
    parent = tv._stack_parent
    window.show_kids_limit("Bugünkü izleme süren doldu.")
    assert window.tv_mode is None
    assert window.video_stack.parentWidget() is parent
    assert window.kids_limit_panel.isVisible()


def test_home_rows_and_favourites_share_existing_ids(window, qt_app):
    window.store.set_favorite("tv:a", True)
    window.model.favorites = window.store.favorites()
    window.store.save_progress("tv:film", 40, 200)
    window.model.set_progress("tv:film", 40, 200)
    window.store.save_progress("tv:d", 0, 0)
    tv = enter(window, qt_app)
    assert [title for title, _ in tv.rows[:3]] == [
        "Kaldığın yerden devam et",
        "Favorilerinde şu an",
        "Son izlenen kanallar",
    ]
    assert [items[0].id for _, items in tv.rows[:3]] == ["tv:film", "tv:a", "tv:d"]
    for _ in range(4):
        key(tv, Qt.Key_Right)
    assert [c.id for _, items in tv.rows for c in items] == ["tv:a"]


def test_cached_plot_and_async_series_use_existing_store(window, qt_app, monkeypatch):
    from luna_iptv.media_controller import source_fingerprint
    from luna_iptv.media_details import MediaDetails

    window.store.save_source({"id": "tv", "name": "TV", "type": "xtream"})
    source = window.store.sources()[0]
    details = MediaDetails(info={"description": "Ay ışığında geçen bir yolculuk."})
    window.store.save_media_details("tv:film", source_fingerprint(source), details, 1)
    pending = []
    monkeypatch.setattr(window, "run_task", lambda fn, success, *a, **kw: pending.append(success))
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    tv.load_focused_details()
    assert tv.description(tv.focused_channel()) == details.info["description"]
    key(tv, Qt.Key_Up)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    assert len(pending) == 1
    episode = Channel("new", "Yeni bölüm", "https://example.test/new", kind="movie", series_id="1")
    pending.pop()(MediaDetails(episodes=[episode]))
    assert tv.series.id == "tv:series"
    assert "tv:new" in [c.id for _, items in tv.rows for c in items]
    tv.close()


def test_late_metadata_after_exit_is_ignored(window, qt_app, monkeypatch):
    from luna_iptv.media_details import MediaDetails

    window.store.save_source({"id": "tv", "name": "TV", "type": "xtream"})
    pending = []
    monkeypatch.setattr(window, "run_task", lambda fn, success, *a, **kw: pending.append(success))
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    tv.load_focused_details()
    tv.close()
    pending[0](MediaDetails(info={"description": "Geç yanıt"}))
    assert window.tv_mode is None and not tv._metadata


def test_browser_and_banner_render_at_1080p(window, qt_app, tmp_path):
    tv = enter(window, qt_app)
    tv.showNormal()
    tv.scale = 1
    tv.resize(1920, 1080)
    tv._apply_scale()
    key(tv, Qt.Key_Down)
    set_motion_level("off")
    qt_app.processEvents()
    image = tv.grab().toImage()
    assert image.size() == tv.size()
    assert image.pixelColor(0, 0) != image.pixelColor(70, 300)
    assert image.save(str(tmp_path / "tv-browser.png"))
    key(tv, Qt.Key_Return)
    qt_app.processEvents()
    assert tv.banner.geometry().bottom() < tv.controls.geometry().top()
    assert tv.rect().contains(tv.banner.geometry())
    assert tv.grab().save(str(tmp_path / "tv-playback.png"))


def test_metadata_requests_are_bounded_and_cancelled_series_stays_closed(
    window, qt_app, monkeypatch
):
    from luna_iptv.media_details import MediaDetails

    window.store.save_source({"id": "tv", "name": "TV", "type": "xtream"})
    pending = []
    monkeypatch.setattr(window, "run_task", lambda fn, success, *a, **kw: pending.append(success))
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    tv.load_focused_details()
    key(tv, Qt.Key_Up)
    key(tv, Qt.Key_Right)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    assert len(pending) == 1
    pending[0](MediaDetails(info={"description": "Film"}))
    assert len(pending) == 2
    key(tv, Qt.Key_Backspace)
    pending[1](MediaDetails(episodes=[Channel("late", "Geç bölüm", "", kind="movie")]))
    assert tv.series is None and tv.row == -1


def test_no_focus_animation_during_playback_and_hold_cancelled_on_deactivate(window, qt_app):
    tv = enter(window, qt_app)
    key(tv, Qt.Key_Down)
    key(tv, Qt.Key_Return)
    window.model.set_progress("tv:film", 70, 200)
    qt_app.processEvents()
    assert tv.browser.animation.state() == QAbstractAnimation.Stopped
    QTest.keyPress(tv, Qt.Key_Backspace)
    QApplication.sendEvent(tv, QEvent(QEvent.WindowDeactivate))
    QTest.keyRelease(tv, Qt.Key_Backspace)
    assert not tv._back_timer.isActive() and tv.playing


def test_multidigit_entry_and_keys_from_video_child(window, qt_app):
    window.store.replace_channels(
        "tv",
        [
            Channel(f"tv:{i}", f"Kanal {i}", f"https://example.test/{i}", group="TV")
            for i in range(1, 16)
        ],
    )
    window.model.reset(window.store.channels(), set())
    tv = enter(window, qt_app)
    key(tv, Qt.Key_1)
    key(tv, Qt.Key_2)
    key(tv, Qt.Key_Return)
    assert window.current.id == "tv:12"
    QTest.keyClick(window.video, Qt.Key_Down)
    assert window.current.id == "tv:13"
    QTest.keyClick(window.video, Qt.Key_Backspace)
    assert tv.browser.isVisible() and not tv.playing


def test_profile_switch_closes_tv_before_changing_data(window, qt_app):
    tv = enter(window, qt_app)
    window.leave_profile()
    assert window.tv_mode is None and tv._closed
    assert window.video_stack.parentWidget() is tv._stack_parent
