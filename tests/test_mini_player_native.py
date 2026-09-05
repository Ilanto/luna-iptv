"""Generated local video through the actual existing native render surface."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from test_transport_ui import playing_window as playing_window
from test_transport_ui import wait


def test_mini_fullscreen_roundtrips_preserve_native_framebuffer(qt_app, playing_window, tmp_path):
    window, _, errors = playing_window
    backend, video = window.player._mpv, window.video
    context, parent = video.context(), video.parent()
    normal_size, normal_minimum = window.size(), window.minimumSize()
    window.toggle_mini_player()
    wait(qt_app, lambda: window.width() <= 580 and window.height() <= 410)
    wait(qt_app, lambda: video.size() == window.watch.size())
    assert video.mapTo(window, QPoint()) == QPoint()
    for _ in range(2):
        window.toggle_fullscreen()
        wait(qt_app, lambda: window.isFullScreen() and video.size() == window.size())
        window.leave_fullscreen()
        wait(qt_app, lambda: not window.isFullScreen() and window.width() <= 580)
    assert video.parent() is parent and video.context() is context
    assert window.player._mpv is backend
    window.fullscreen.reveal()
    frame = video.grabFramebuffer()
    assert not frame.isNull()
    colors = {
        frame.pixelColor(int(frame.width() * x / 10), int(frame.height() * y / 10)).name()
        for x in range(1, 9)
        for y in range(1, 9)
    }
    assert len(colors) > 8
    window.grab().save(str(tmp_path / "mini-player.png"))
    window.leave_mini_player()
    try:
        wait(qt_app, lambda: window.size() == normal_size)
    except AssertionError:
        pytest.fail(
            f"Normal restore size: actual={window.size()}, expected={normal_size}; "
            f"saved={window.mini_player._normal_geometry}, state={window.windowState()}"
        )
    assert window.minimumSize() == normal_minimum
    assert video.context() is context and window.player._mpv is backend
    assert not errors


def test_mini_controls_operate_the_existing_player(qt_app, playing_window):
    window, props, errors = playing_window
    window.toggle_mini_player()
    wait(qt_app, lambda: window.width() <= 580)
    QTest.mouseClick(window.seek_forward_button, Qt.LeftButton)
    wait(qt_app, lambda: abs(window._position - 25) < 0.15)
    QTest.mouseClick(window.seek_back_button, Qt.LeftButton)
    wait(qt_app, lambda: abs(window._position - 20) < 0.15)
    QTest.mouseClick(window.play_button, Qt.LeftButton)
    wait(qt_app, lambda: props.get("pause") is False)
    QTest.mouseClick(window.mute_button, Qt.LeftButton)
    wait(qt_app, lambda: props.get("mute") is True)
    window.stop_playback()
    assert window.mini_player.active
    assert "durduruldu" in window.mini_status.text()
    QTest.mouseClick(window.mini_button, Qt.LeftButton)
    assert not window.mini_player.active
    assert not errors
