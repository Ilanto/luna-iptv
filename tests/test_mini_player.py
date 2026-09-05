"""Mini mode uses real Qt layouts and an inert video backend; no GPU required."""

import time
from concurrent.futures import Future

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
from shiboken6 import isValid

from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.window import MainWindow


class InertPlayer(QObject):
    property_changed = Signal(str, object)
    error = Signal(str)
    file_loaded = Signal()
    ended = Signal()
    ready = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.commands = []
        self.shutdown_count = 0

    def command(self, args):
        self.commands.append(args)
        future = Future()
        future.set_result(None)
        return future

    def set_property(self, name, value):
        return self.command(["set", name, value])

    def pause_toggle(self):
        self.command(["cycle", "pause"])

    def stop(self):
        self.command(["stop"])

    def shutdown(self):
        self.shutdown_count += 1


class InertVideo(QWidget):
    def __init__(self, player, parent):
        super().__init__(parent)
        self.player = player
        self.context_token = object()

    def context(self):
        return self.context_token


def wait_for(qt_app, predicate, timeout=3, stable_for=0.1):
    deadline = time.monotonic() + timeout
    settled_since = None
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            if settled_since is None:
                settled_since = time.monotonic()
            if time.monotonic() - settled_since >= stable_for:
                return
        else:
            settled_since = None
        time.sleep(0.005)
    raise AssertionError("Window mode or geometry did not settle")


def fullscreen_settled(window):
    return window.isFullScreen() and (
        QApplication.platformName() == "offscreen" or window.size() == window.screen().size()
    )


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    import luna_iptv.layout as layout_module
    import luna_iptv.window as window_module

    monkeypatch.setattr(window_module, "Player", InertPlayer)
    monkeypatch.setattr(layout_module, "VideoWidget", InertVideo)
    apply_theme(qt_app)
    window = MainWindow(Store(tmp_path / "mini.sqlite3"))
    window.video_stack.setCurrentWidget(window.video)
    initial_size = window.size()
    window.show()
    wait_for(qt_app, lambda: window.windowHandle().isExposed() and window.size() == initial_size)
    yield window
    if isValid(window):
        window.close()
    qt_app.processEvents()


def enter_mini(window, qt_app):
    assert hasattr(window, "toggle_mini_player"), "Mini player entry point is missing"
    window.toggle_mini_player()
    wait_for(
        qt_app,
        lambda: (
            window.mini_player.active
            and not window.isFullScreen()
            and window.width() <= 580
            and window.height() <= 410
        ),
    )


def test_mini_is_compact_and_keeps_native_objects(window, qt_app):
    player, video = window.player, window.video
    parent, context = video.parent(), video.context()
    enter_mini(window, qt_app)

    assert window.mini_player.active
    assert not window._fullscreen and not window.isFullScreen()
    assert window.width() <= 580 and window.height() <= 410
    assert window.minimumWidth() <= 480 and window.minimumHeight() <= 300
    assert window.player is player and window.video is video
    assert video.parent() is parent and video.context() is context
    assert video.size() == window.watch.size()
    assert window.sidebar.isHidden() and window.library.isHidden()
    assert window.player_header.isHidden() and window.guide.isHidden()
    assert window.mini_button.isVisible() and "dön" in window.mini_button.text().lower()


def test_return_restores_normal_geometry_minimum_and_visibility(window, qt_app):
    window.guide.hide()
    window.info_panel.show()
    qt_app.processEvents()
    geometry, minimum = window.geometry(), window.minimumSize()
    enter_mini(window, qt_app)
    window.leave_mini_player()
    wait_for(qt_app, lambda: window.geometry() == geometry)

    assert not window.mini_player.active and not window.fullscreen.active
    assert window.minimumSize() == minimum
    assert window.geometry() == geometry
    assert window.sidebar.isVisible() and window.library.isVisible()
    assert window.guide.isHidden()
    assert window.info_panel.isVisible()


def test_explicit_info_close_survives_mini_exit(window, qt_app):
    window.info_panel.show()
    enter_mini(window, qt_app)
    assert window.info_panel.isHidden()
    window.stop_playback()
    window.leave_mini_player()
    qt_app.processEvents()
    assert window.info_panel.isHidden()
    assert not window.info_button.isEnabled()


def test_mini_fullscreen_escape_returns_to_mini_then_normal(window, qt_app):
    normal_minimum = window.minimumSize()
    enter_mini(window, qt_app)
    mini_size = window.size()
    for _ in range(3):
        window.toggle_fullscreen()
        wait_for(qt_app, lambda: fullscreen_settled(window))
        assert window._fullscreen and window.isFullScreen()
        window.leave_fullscreen()
        wait_for(qt_app, lambda: not window.isFullScreen() and window.size() == mini_size)
        assert window.mini_player.active and not window._fullscreen
        assert window.size() == mini_size
        assert window.sidebar.isHidden()
    window.leave_fullscreen()  # Esc from mini restores the normal window.
    qt_app.processEvents()
    assert not window.mini_player.active
    assert window.minimumSize() == normal_minimum
    assert window.sidebar.isVisible()


def test_fullscreen_entry_to_mini_returns_to_normal(window, qt_app):
    geometry = window.geometry()
    window.toggle_fullscreen()
    wait_for(qt_app, lambda: fullscreen_settled(window))
    enter_mini(window, qt_app)
    assert not window._fullscreen and not window.isFullScreen()
    assert window.width() <= 580
    window.leave_mini_player()
    wait_for(qt_app, lambda: window.geometry() == geometry)
    assert window.geometry() == geometry


def test_compact_essential_controls_fit_at_minimum_width(window, qt_app):
    enter_mini(window, qt_app)
    window.resize(window.minimumSize())
    wait_for(qt_app, lambda: window.size() == window.minimumSize())
    window.status("Bağlantı yeniden deneniyor — " * 12)
    qt_app.processEvents()
    window.fullscreen.reveal()
    qt_app.processEvents()
    controls = window.controls
    assert controls.width() <= window.width()
    for widget in (
        window.seek_back_button,
        window.seek_forward_button,
        window.play_button,
        window.mute_button,
        window.volume,
        window.mini_button,
        window.fullscreen_button,
    ):
        assert widget.isVisible()
        origin = widget.mapTo(controls, QPoint())
        assert origin.x() >= 0
        assert origin.x() + widget.width() <= controls.width()
    assert window.time_label.width() >= 90
    assert window.mini_status.isVisible()
    assert "Bağlantı" in window.mini_status.text()
    assert window.width() == window.minimumWidth()


def test_tagged_advanced_controls_restore_without_replaying_dynamic_visibility(window, qt_app):
    advanced = QPushButton("Oynatma", window.controls)
    advanced.setProperty("mini_hidden", True)
    window.controls.layout().addWidget(advanced)
    advanced.show()
    assert advanced.isVisible()
    dynamic = QLabel("Yeni durum", window.controls)
    window.controls.layout().addWidget(dynamic)
    dynamic.hide()
    enter_mini(window, qt_app)
    assert advanced.isHidden()
    dynamic.show()
    window.leave_mini_player()
    qt_app.processEvents()
    assert advanced.isVisible()
    assert dynamic.isVisible()


def test_normal_maximized_state_is_restored(window, qt_app):
    window.showMaximized()
    wait_for(qt_app, lambda: window.isMaximized())
    assert window.isMaximized()
    enter_mini(window, qt_app)
    assert not window.isMaximized()
    window.leave_mini_player()
    qt_app.processEvents()
    assert window.isMaximized()


def test_entry_button_and_close_keep_single_backend(window, qt_app):
    assert hasattr(window, "mini_button"), "Visible mini player button is missing"
    player = window.player
    QTest.mouseClick(window.mini_button, Qt.LeftButton)
    wait_for(qt_app, lambda: window.mini_player.active and window.width() <= 580)
    assert window.mini_player.active
    window.close()
    assert player.shutdown_count == 1
    assert not window.fullscreen.active
    assert not window.mini_player.active


def test_mini_idle_and_shortcuts_reveal_the_shared_controls(window, qt_app):
    enter_mini(window, qt_app)
    window.fullscreen.eventFilter(window.video, QEvent(QEvent.MouseMove))
    window.fullscreen._hide_idle()
    assert window.controls.isHidden()
    assert window.video.cursor().shape() == Qt.BlankCursor
    window.shortcut_action("M", window.player.pause_toggle)
    assert window.controls.isVisible()
    assert window.video.cursor().shape() != Qt.BlankCursor
    assert window.mini_player.active
    window.leave_mini_player()
    assert window.video.cursor().shape() != Qt.BlankCursor


def test_info_intent_survives_mini_fullscreen_and_explicit_close(window, qt_app):
    window.info_panel.show()
    enter_mini(window, qt_app)
    assert window.info_panel.isHidden()
    window.toggle_fullscreen()
    qt_app.processEvents()
    assert window.info_panel.isVisible()
    window.fullscreen.set_info_visible(False)
    window.leave_fullscreen()
    window.leave_mini_player()
    qt_app.processEvents()
    assert window.info_panel.isHidden()


def test_delayed_fullscreen_restore_cannot_reapply_old_mini_geometry(window, qt_app):
    geometry = window.geometry()
    enter_mini(window, qt_app)
    window.toggle_fullscreen()
    window.leave_fullscreen()
    window.leave_mini_player()
    # A delayed Wayland state acknowledgement may request windowed mode again.
    window._restore_windowed_state()
    wait_for(qt_app, lambda: not window.isFullScreen() and window.geometry() == geometry)
    assert window.geometry() == geometry


@pytest.mark.parametrize("action", ["cancel", "close", "fullscreen"])
def test_cancelled_pending_mini_request_cannot_reenter(window, qt_app, action):
    controller = window.mini_player
    controller.enter_after_fullscreen(window.geometry(), False)
    generation = controller._request_generation
    assert controller.pending
    if action == "cancel":
        window.leave_mini_player()
    elif action == "close":
        window.close()
    else:
        window.toggle_fullscreen()
    controller._complete_pending(generation)
    qt_app.processEvents()
    assert not controller.pending and not controller.active
