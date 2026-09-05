"""Exercise the visible transport and fullscreen with isolated native playback."""

import shutil
import subprocess
import time
import warnings

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.window import MainWindow


def wait(qt_app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for native player/UI state"


@pytest.fixture
def playing_window(tmp_path, qt_app):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for generated native media")
    media = tmp_path / "transport.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24",
            "-t",
            "60",
            "-c:v",
            "mpeg2video",
            "-g",
            "12",
            str(media),
        ],
        check=True,
    )
    store = Store(tmp_path / "library.sqlite3")
    source_id = store.save_source({"name": "Local transport", "type": "m3u"})
    store.replace_channels(source_id, [Channel("video", "Local test", str(media), kind="movie")])
    apply_theme(qt_app)
    window = MainWindow(store)
    props, errors = {}, []
    window.player.property_changed.connect(lambda key, value: props.update({key: value}))
    window.player.error.connect(errors.append)
    window.show()
    try:
        window.play(window.model.channels[0])
        wait(qt_app, lambda: window._seekable and window._duration > 50 and not window._loading)
        window.player.set_property("pause", True)
        window.player.command(["seek", 20, "absolute+exact"])
        wait(qt_app, lambda: props.get("pause") is True and abs(window._position - 20) < 0.15)
        yield window, props, errors
    finally:
        window.close()
        if window.player._termination:
            window.player._termination.join(timeout=20)
        qt_app.processEvents()


def test_five_second_buttons_and_shortcuts_seek_real_video(qt_app, playing_window):
    window, props, errors = playing_window
    QTest.mouseClick(window.seek_forward_button, Qt.LeftButton)
    wait(qt_app, lambda: abs(window._position - 25) < 0.15)
    QTest.mouseClick(window.seek_back_button, Qt.LeftButton)
    wait(qt_app, lambda: abs(window._position - 20) < 0.15)
    # QShortcut's WindowShortcut context requires Qt's logical active window.
    # Native Wayland does not let a test process force compositor/user focus.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(window)
    window.setFocus(Qt.OtherFocusReason)
    qt_app.processEvents()
    assert QApplication.activeWindow() is window
    QTest.keyClick(window, Qt.Key_Right)
    wait(qt_app, lambda: abs(window._position - 25) < 0.15)
    QTest.keyClick(window, Qt.Key_Left)
    wait(qt_app, lambda: abs(window._position - 20) < 0.15)
    assert props["pause"] is True
    assert not errors


def test_fast_scan_both_directions_and_play_return(qt_app, playing_window):
    window, props, errors = playing_window
    for rate in (2, 4, 8, 16):
        QTest.mouseClick(window.forward_button, Qt.LeftButton)
        assert window.transport.rate == rate
    wait(qt_app, lambda: window._position > 25)
    position = window._position
    QTest.mouseClick(window.rewind_button, Qt.LeftButton)
    assert window.transport.rate == -2
    wait(qt_app, lambda: window._position < position - 0.8)
    assert "2" in window.rate_button.text()
    QTest.mouseClick(window.play_button, Qt.LeftButton)
    wait(qt_app, lambda: props.get("pause") is False)
    assert window.transport.rate == 0
    assert window.rate_button.text() == "1×"
    assert not errors


def test_fullscreen_video_fills_screen_and_keeps_context(qt_app, playing_window):
    window, _, errors = playing_window
    backend = window.player._mpv
    context = window.video.context()
    parent = window.video.parent()
    window.info_panel.show()
    before = window.video.geometry()
    window.toggle_fullscreen()
    try:
        wait(qt_app, lambda: window.isFullScreen() and window.size() == window.screen().size())
        wait(qt_app, lambda: window.video.size() == window.size())
        assert window.video.mapTo(window, QPoint()) == QPoint()
        assert window.controls.isVisible()
        assert window.video.parent() is parent
        assert window.video.context() is context
        assert window.player._mpv is backend
        frame = window.video.grabFramebuffer()
        assert not frame.isNull()
        colors = {
            frame.pixelColor(int(frame.width() * x / 10), int(frame.height() * y / 10)).name()
            for x in range(1, 9)
            for y in range(1, 9)
        }
        assert len(colors) > 8
    finally:
        window.leave_fullscreen()
    wait(qt_app, lambda: not window.isFullScreen() and window.video.size() != window.size())
    assert window.sidebar.isVisible() and window.library.isVisible()
    assert window.info_panel.isVisible()
    assert window.video.context() is context and window.video.parent() is parent
    assert window.video.geometry().width() >= before.width() - 1
    assert not errors


def test_idle_and_live_source_transport_capabilities(qt_app, tmp_path):
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    try:
        assert not window.seek_back_button.isEnabled()
        assert not window.seek_forward_button.isEnabled()
        assert not window.rewind_button.isEnabled()
        assert not window.forward_button.isEnabled()
        window.transport.prepare(live=True)
        window.transport.observe("seekable", True)
        window.transport.observe("duration", 100)
        window.transport.loaded()
        assert window.seek_forward_button.isEnabled()
        assert not window.forward_button.isEnabled()
        window.transport.finished()
        assert not window.seek_forward_button.isEnabled()
    finally:
        window.close()
        qt_app.processEvents()


def test_fullscreen_button_idle_hides_and_mouse_restores_controls(qt_app, playing_window):
    window, _, errors = playing_window

    def move_over_video(position):
        # QTest.mouseMove relies on cursor warping, which native Wayland does not
        # permit. Deliver the same Qt event to the real QOpenGLWidget directly.
        QApplication.sendEvent(
            window.video,
            QMouseEvent(
                QEvent.MouseMove,
                QPointF(position),
                QPointF(window.video.mapToGlobal(position)),
                Qt.NoButton,
                Qt.NoButton,
                Qt.NoModifier,
            ),
        )

    QTest.mouseClick(window.fullscreen_button, Qt.LeftButton)
    try:
        wait(qt_app, lambda: window.isFullScreen() and window.video.size() == window.size())
        move_over_video(window.video.rect().center())
        wait(qt_app, lambda: window.controls.isHidden(), timeout=5)
        assert window.video.cursor().shape() == Qt.BlankCursor
        assert window.video.size() == window.size()
        move_over_video(window.video.rect().center() + QPoint(10, 10))
        wait(qt_app, window.controls.isVisible, timeout=2)
        assert window.video.cursor().shape() != Qt.BlankCursor
        assert window.video.size() == window.size()
    finally:
        window.leave_fullscreen()
    assert not errors


def test_channel_change_and_stop_cancel_scan_without_delayed_seeks(qt_app, playing_window):
    window, _, errors = playing_window
    for _ in range(4):
        window.transport.cycle(1)
    assert window.transport.rate == 16
    window.play(window.current)
    wait(qt_app, lambda: not window._loading and window._position >= 20)
    assert window.transport.rate == 0
    started = window._position
    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    assert window._position - started < 2
    window.transport.cycle(-1)
    assert window.transport.rate == -2
    window.stop_playback()
    assert window.transport.rate == 0
    assert not window.seek_forward_button.isEnabled()
    assert not window.forward_button.isEnabled()
    assert not errors


def test_transport_shortcuts_do_not_intercept_library_search(qt_app, playing_window):
    window, props, errors = playing_window
    window.search.setFocus()
    QTest.keyClicks(window.search, "jlk")
    assert window.search.text() == "jlk"
    assert window.transport.rate == 0
    assert props["pause"] is True
    assert not errors


def test_rapid_scan_exit_and_reentry_keeps_real_transport_responsive(qt_app, playing_window):
    window, props, errors = playing_window
    window.transport.normal_play()
    wait(qt_app, lambda: props.get("pause") is False)
    window.transport.cycle(1)
    wait(qt_app, lambda: props.get("pause") is True)
    for _ in range(5):
        window.transport.normal_play()
        window.transport.cycle(1)
    position = window._position
    wait(qt_app, lambda: window._position > position + 1)
    assert window.transport.rate == 2
    window.transport.cancel()
    wait(qt_app, lambda: props.get("pause") is False)
    assert window.transport.rate == 0
    assert not errors


def test_scan_shortcut_reveals_hidden_fullscreen_controls(qt_app, playing_window):
    window, _, errors = playing_window
    window.toggle_fullscreen()
    try:
        wait(qt_app, lambda: window.isFullScreen() and window.video.size() == window.size())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            QApplication.setActiveWindow(window)
        window.setFocus(Qt.OtherFocusReason)
        qt_app.processEvents()
        window.fullscreen._pointer_in_overlay = False
        window.fullscreen._keyboard_navigation = False
        window.fullscreen._hide_idle()
        assert window.controls.isHidden()
        QTest.keyClick(window, Qt.Key_L)
        assert window.transport.rate == 2
        assert window.controls.isVisible()
    finally:
        window.leave_fullscreen()
    assert not errors
