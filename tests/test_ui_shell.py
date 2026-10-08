"""Interface shell checks use inert playback and Qt's offscreen platform."""

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from test_mini_player import InertPlayer, InertVideo

from luna_iptv import motion
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def shell(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr("luna_iptv.window.Player", InertPlayer)
    monkeypatch.setattr("luna_iptv.layout.VideoWidget", InertVideo)
    store = Store(tmp_path / "shell.sqlite3")
    store.set_setting("motion_level", "off")
    source = store.save_source({"name": "Test", "type": "m3u", "location": "test.m3u"})
    store.replace_channels(source, [Channel("one", "Test", "https://example.test/live")])
    window = MainWindow(store)
    monkeypatch.setattr(window.player, "reserve_load", lambda: 10, raising=False)
    monkeypatch.setattr(window.player, "load", lambda *a, **kw: None, raising=False)
    window.show()
    qt_app.processEvents()
    yield window, store.channels(source)[0]
    if isValid(window):
        window.close()
    qt_app.processEvents()
    motion.set_motion_level("full")


def test_idle_panel_and_startup_message(shell):
    window, _ = shell
    assert window.current is None
    assert window.watch.isHidden()
    assert window.splitter.sizes()[1] == 0
    assert not window.toast.isVisible()
    assert window.message_bar.isHidden()
    assert window.add_button.isVisible()


def test_panel_tracks_channel_not_playback_state(shell, qt_app):
    window, channel = shell
    window.play(channel)
    assert window.watch.isVisible()
    assert window.splitter.sizes()[1] >= 380
    window.stop_playback()
    assert window.watch.isVisible()
    window.play_button.setFocus()
    window.close_current()
    assert window.watch.isHidden()
    assert window.splitter.sizes()[1] == 0
    assert not window.watch.isAncestorOf(QApplication.focusWidget())


def test_panel_remembers_handle_width(shell):
    window, channel = shell
    window.play(channel)
    window.splitter.setSizes([650, 590])
    window.splitter.splitterMoved.emit(window.splitter.sizes()[0], 1)
    width = window.splitter.sizes()[1]
    window.close_current()
    window.play(channel)
    assert abs(window.splitter.sizes()[1] - width) <= 2


@pytest.mark.parametrize("mode", ["fullscreen", "mini"])
def test_panel_defers_changes_in_video_modes(shell, mode):
    window, channel = shell
    window.play(channel)
    before = window.splitter.sizes()
    if mode == "fullscreen":
        window._fullscreen = True
    else:
        window.mini_player.active = True
    window.close_current()
    assert window.splitter.sizes() == before
    assert not window.watch.isHidden()
    window._fullscreen = False
    window.mini_player.active = False
    window.watch_panel.sync()
    assert window.watch.isHidden()


def test_panel_animation_finishes_and_motion_change_settles(shell):
    window, channel = shell
    motion.set_motion_level("full")
    window.play(channel)
    animation = window.watch_panel.animation
    assert animation.duration() == 220
    animation.setCurrentTime(animation.duration())
    assert window.splitter.sizes()[1] >= 380
    window.close_current()
    motion.set_motion_level("off")
    assert window.watch.isHidden()
    assert window.splitter.sizes()[1] == 0


def test_status_toast_and_retry_action(shell):
    window, _ = shell
    window.status("Favorilere eklendi.", icon="check")
    assert window.message.text() == "Favorilere eklendi."
    assert window.toast.text == window.message.text()
    assert window.toast.isVisible()
    assert window.message_bar.isHidden()
    assert window.toast.accessibleName() == window.message.text()
    calls = []
    window.status("Yeniden dene.", lambda: calls.append(True))
    assert window.message_bar.isVisible()
    assert window.retry_button.isVisible()
    assert not window.toast.isVisible()
    window.retry_button.click()
    assert calls == [True]
    window.status("Tamam.")
    assert window.message_bar.isHidden()
    assert window.toast.isVisible()


def test_recovery_keeps_cancel_in_action_bar(shell):
    window, channel = shell
    window.play(channel)
    assert window.recovery_cancel_button.isVisible()
    assert window.message_bar.isVisible()
    window.playback_loaded(10)
    assert window.message_bar.isHidden()


def test_toast_duration_replacement_hover_and_click(shell, qt_app):
    window, _ = shell
    window.status("Kısa")
    toast = window.toast
    assert toast.timer.interval() == 3200
    window.status("x" * 80)
    assert toast.timer.interval() == 6000
    window.status("x" * 200)
    assert toast.timer.interval() == 7000
    assert toast.text == "x" * 200
    QApplication.sendEvent(toast, QEvent(QEvent.Enter))
    assert not toast.timer.isActive()
    QApplication.sendEvent(toast, QEvent(QEvent.Leave))
    assert toast.timer.isActive()
    QTest.mouseClick(toast, Qt.LeftButton)
    assert not toast.isVisible()
    assert not toast.timer.isActive()


def test_toast_resizes_and_stays_within_two_lines(shell, qt_app):
    window, _ = shell
    window.status("Uzun bir bildirim. " * 60)
    toast = window.toast
    assert toast.width() <= 520
    assert toast.label.height() <= 2 * toast.label.fontMetrics().lineSpacing() + 2
    window.resize(1450, 900)
    qt_app.processEvents()
    assert abs(toast.geometry().center().x() - window.rect().center().x()) <= 1
    assert window.height() - toast.geometry().bottom() >= 18
    QTest.mouseClick(toast.label, Qt.LeftButton, pos=QPoint(5, 5))
    assert not toast.isVisible()


@pytest.mark.parametrize("level", ["off", "reduced", "full"])
def test_page_transitions_cleanup_and_same_section(shell, level):
    window, _ = shell
    motion.set_motion_level(level)
    for section, page in [
        ("live", window.browse),
        ("movie", window.browse),
        ("favorites", window.browse),
        ("guide", window.guide_view),
        ("home", window.home_view),
    ]:
        window.set_section(section)
        assert window.library_pages.currentWidget() is page
        transition = window.page_transition
        if level != "off":
            assert transition.animation.state() == QAbstractAnimation.Running
            transition.animation.setCurrentTime(transition.animation.duration())
        assert page.graphicsEffect() is None
        assert transition.overlay is None
        window.set_section(section)
        assert transition.animation.state() != QAbstractAnimation.Running
        assert transition.overlay is None


def test_panel_slide_has_intermediate_sizes_and_survives_mode_entry(shell, qt_app):
    window, channel = shell
    motion.set_motion_level("full")
    window.play(channel)
    animation = window.watch_panel.animation
    animation.setCurrentTime(30)
    width = window.splitter.sizes()[1]
    assert 0 < width < animation.endValue()
    window.toggle_mini_player()
    assert animation.state() != QAbstractAnimation.Running
    window.close_current()
    assert window.watch.isVisible()
    window.leave_mini_player()
    assert window.watch.isHidden()
    assert window.splitter.sizes()[1] == 0


def test_fullscreen_exit_reconciles_channel_and_actions(shell, qt_app):
    window, channel = shell
    window.play(channel)
    window.toggle_fullscreen()
    window.status("Tekrar dene", lambda: None)
    assert window.message_bar.isHidden()
    window.leave_fullscreen()
    assert window.watch.isVisible()
    assert window.retry_button.isVisible()
    window.toggle_fullscreen()
    window.close_current()
    assert window.watch.isVisible()
    window.leave_fullscreen()
    assert window.watch.isHidden()


def test_profile_switch_and_source_removal_close_panel(shell, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, channel = shell
    window.play(channel)
    profile = window.store.create_profile("Diğer", "#a9b8ff")
    window.switch_profile(profile)
    assert window.current is None
    assert window.watch.isHidden()
    assert not window.toast.icon.isHidden()
    window.play(channel)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window.remove_source(window.source_for(channel))
    assert window.current is None
    assert window.watch.isHidden()


def test_toast_animation_replacement_expiry_and_cleanup(shell):
    window, _ = shell
    motion.set_motion_level("full")
    window.status("İlk mesaj")
    toast = window.toast
    toast.animation.setCurrentTime(toast.animation.duration())
    window.status("Yeni mesaj", icon="check")
    assert toast.text == "Yeni mesaj"
    assert toast._replacement is not None
    toast.animation.setCurrentTime(toast.animation.duration())
    assert toast._replacement is None
    assert toast.opacity.opacity() == 1
    toast.timer.timeout.emit()
    toast.animation.setCurrentTime(toast.animation.duration())
    assert toast.isHidden()
    window.status("Hazır. Kaynakların bu bilgisayarda kalır.")
    assert toast.isHidden()


def test_toast_renders_and_announces_plain_text(shell, qt_app, tmp_path):
    from PySide6.QtGui import QColor

    from luna_iptv import theme

    window, _ = shell
    window.status("TRT 1 favorilere eklendi.", icon="check")
    qt_app.processEvents()
    toast = window.toast
    image = toast.grab().toImage()
    image.save(str(tmp_path / "toast.png"))
    assert image.pixelColor(image.width() // 2, 12) == QColor(theme.RAISED)
    assert toast.label.textFormat() == Qt.PlainText
    assert toast.accessibleDescription() == window.message.text()


def test_page_interruptions_leave_no_snapshots(shell):
    window, _ = shell
    motion.set_motion_level("full")
    window.set_section("live")
    window.set_section("movie")
    window.set_section("guide")
    assert window.page_transition.overlay is not None
    motion.set_motion_level("off")
    assert window.page_transition.overlay is None
    assert window.page_transition.incoming is None
    assert window.library_pages.currentWidget() is window.guide_view


@pytest.mark.parametrize("mode", ["mini", "fullscreen"])
@pytest.mark.parametrize("panel_width", [640, 900])
def test_user_panel_width_survives_video_mode_round_trip(shell, qt_app, mode, panel_width):
    window, channel = shell
    window.play(channel)
    window.resize(1600, 900)
    qt_app.processEvents()
    window.splitter.setSizes([1500 - panel_width, panel_width])
    window.splitter.splitterMoved.emit(window.splitter.sizes()[0], 1)
    width = window.splitter.sizes()[1]
    if mode == "mini":
        window.toggle_mini_player()
        qt_app.processEvents()
        window.leave_mini_player()
    else:
        window.toggle_fullscreen()
        qt_app.processEvents()
        window.leave_fullscreen()
    qt_app.processEvents()
    assert abs(window.splitter.sizes()[1] - width) <= 2


def test_toast_lifetime_resumes_after_window_hide(shell, qt_app):
    window, _ = shell
    window.status("Kısa bildirim")
    toast = window.toast
    window.hide()
    assert not toast.timer.isActive()
    window.show()
    qt_app.processEvents()
    assert toast.isVisible()
    assert toast.timer.isActive()
    toast.timer.timeout.emit()
    assert toast.isHidden()
    window.hide()
    window.show()
    assert toast.isHidden()
