from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from luna_iptv.fullscreen import FullscreenController


@dataclass
class OverlayHarness:
    window: QMainWindow
    view: QVBoxLayout
    header: QWidget
    focus_button: QPushButton


@pytest.fixture
def overlay(qt_app):
    window = QMainWindow()
    window._fullscreen = False
    window.resize(980, 700)

    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    body = QHBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(0)
    outer.addLayout(body, 1)

    window.sidebar = QFrame()
    window.sidebar.setFixedWidth(80)
    body.addWidget(window.sidebar)
    window.library = QFrame()
    window.library.setFixedWidth(120)
    body.addWidget(window.library)

    window.watch = QWidget()
    body.addWidget(window.watch, 1)
    view = QVBoxLayout(window.watch)
    view.setContentsMargins(11, 12, 13, 14)
    view.setSpacing(7)

    header = QFrame()
    header.setFixedHeight(42)
    view.addWidget(header)
    window.video_stack = QFrame()
    window.video_stack.setMinimumSize(160, 90)
    view.addWidget(window.video_stack, 1)
    window.video = QWidget(window.video_stack)

    window.info_panel = QFrame()
    window.info_panel.setFixedHeight(86)
    view.addWidget(window.info_panel)

    window.controls = QFrame()
    window.controls.setFixedHeight(76)
    controls_layout = QVBoxLayout(window.controls)
    window.seek = QSlider(Qt.Horizontal)
    window.volume = QSlider(Qt.Horizontal)
    focus_button = QPushButton("Odak")
    controls_layout.addWidget(window.seek)
    controls_layout.addWidget(window.volume)
    controls_layout.addWidget(focus_button)
    view.addWidget(window.controls)

    window.guide = QFrame()
    window.guide.setFixedHeight(58)
    view.addWidget(window.guide)

    window.message_bar = QFrame()
    window.message_bar.setFixedHeight(32)
    outer.addWidget(window.message_bar)
    window.setCentralWidget(root)
    window.show()
    qt_app.processEvents()

    harness = OverlayHarness(window, view, header, focus_button)
    yield harness

    if isValid(window):
        window.close()
    qt_app.processEvents()


def test_enter_keeps_video_widget_and_fills_watch_behind_overlays(qt_app, overlay):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    window.info_panel.show()
    qt_app.processEvents()
    video_parent = window.video.parent()
    video_identity = id(window.video)
    fullscreen_state = window._fullscreen

    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    qt_app.processEvents()

    assert controller.active is True
    assert window._fullscreen is fullscreen_state
    assert all(
        widget.isHidden()
        for widget in (window.sidebar, window.library, window.guide, window.message_bar, header)
    )
    assert view.indexOf(window.info_panel) == -1
    assert view.indexOf(window.controls) == -1
    assert window.info_panel.parent() is window.watch
    assert window.controls.parent() is window.watch
    assert id(window.video) == video_identity
    assert window.video.parent() is video_parent
    assert window.video_stack.geometry() == window.watch.rect()

    controls_rect = window.controls.geometry()
    assert window.controls.isVisible()
    assert controls_rect.width() == min(window.watch.width() - 32, 1100)
    assert abs(controls_rect.center().x() - window.watch.rect().center().x()) <= 1
    assert controls_rect.bottom() < window.watch.rect().bottom()
    assert window.info_panel.isVisible()
    assert window.info_panel.geometry().bottom() < controls_rect.top()

    controller.close()


def test_exit_restores_layout_visibility_and_local_cursors(qt_app, overlay):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    window.info_panel.hide()
    window.watch.setCursor(Qt.CrossCursor)
    window.video.setCursor(Qt.PointingHandCursor)
    original_margins = view.getContentsMargins()
    original_spacing = view.spacing()
    original_indices = {
        window.info_panel: view.indexOf(window.info_panel),
        window.controls: view.indexOf(window.controls),
    }
    video_parent = window.video.parent()

    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    window.info_panel.show()
    controller.reveal()
    controller.set_active(False)
    qt_app.processEvents()

    assert controller.active is False
    assert view.getContentsMargins() == original_margins
    assert view.spacing() == original_spacing
    assert view.indexOf(window.info_panel) == original_indices[window.info_panel]
    assert view.indexOf(window.controls) == original_indices[window.controls]
    assert window.info_panel.isHidden()
    assert all(
        not widget.isHidden()
        for widget in (window.sidebar, window.library, window.guide, window.message_bar, header)
    )
    assert window.video.parent() is video_parent
    assert window.watch.cursor().shape() == Qt.CrossCursor
    assert window.video.cursor().shape() == Qt.PointingHandCursor

    controller.close()


def test_idle_waits_for_slider_focus_and_popup_then_hides(qt_app, overlay, monkeypatch):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    focus_button = overlay.focus_button
    focused = [None]
    popup = [None]
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: focused[0]))
    monkeypatch.setattr(QApplication, "activePopupWidget", staticmethod(lambda: popup[0]))
    window.info_panel.show()
    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    assert controller._idle_timer.interval() == 2500
    assert controller._idle_timer.isSingleShot()

    window.seek.setSliderDown(True)
    controller._hide_idle()
    assert window.controls.isVisible()
    assert controller._idle_timer.isActive()
    window.seek.setSliderDown(False)

    focused[0] = focus_button
    controller.eventFilter(
        focus_button,
        QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason),
    )
    controller._hide_idle()
    assert window.controls.isVisible()

    menu = QMenu(window)
    menu.addAction("Parça")
    focused[0] = None
    popup[0] = menu
    controller._hide_idle()
    assert window.controls.isVisible()

    popup[0] = None
    controller._hide_idle()
    assert window.controls.isHidden()
    assert window.info_panel.isHidden()
    assert window.watch.cursor().shape() == Qt.BlankCursor
    assert window.video.cursor().shape() == Qt.BlankCursor

    controller.eventFilter(window.video, QEvent(QEvent.MouseMove))
    qt_app.processEvents()
    assert window.controls.isVisible()
    assert window.info_panel.isVisible()
    assert window.watch.cursor().shape() == Qt.ArrowCursor
    assert controller._idle_timer.isActive()

    controller._hide_idle()
    controller.eventFilter(window.video, QEvent(QEvent.KeyPress))
    qt_app.processEvents()
    assert window.controls.isVisible()

    controller.close()


def test_mouse_focus_does_not_block_idle_and_video_motion_ends_keyboard_guard(
    qt_app, overlay, monkeypatch
):
    window = overlay.window
    focus_button = overlay.focus_button
    focused = [focus_button]
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: focused[0]))
    controller = FullscreenController(window, overlay.view, overlay.header)
    controller.set_active(True)

    controller.eventFilter(
        focus_button,
        QFocusEvent(QEvent.FocusIn, Qt.MouseFocusReason),
    )
    controller._hide_idle()
    assert window.controls.isHidden()

    controller.reveal()
    controller.eventFilter(
        focus_button,
        QFocusEvent(QEvent.FocusIn, Qt.BacktabFocusReason),
    )
    controller._hide_idle()
    assert window.controls.isVisible()

    controller.eventFilter(focus_button, QEvent(QEvent.Enter))
    controller._hide_idle()
    assert window.controls.isVisible()

    controller.eventFilter(window.video, QEvent(QEvent.MouseMove))
    controller._hide_idle()
    assert window.controls.isHidden()

    controller.close()


def test_info_intent_does_not_resurrect_after_idle_or_fullscreen_exit(qt_app, overlay):
    window = overlay.window
    window.info_panel.show()
    controller = FullscreenController(window, overlay.view, overlay.header)
    controller.set_active(True)
    controller._hide_idle()
    assert window.info_panel.isHidden()

    controller.set_info_visible(False)
    controller.reveal()
    assert window.info_panel.isHidden()

    assert controller.toggle_info() is True
    assert window.info_panel.isVisible()
    assert controller.toggle_info() is False
    assert window.info_panel.isHidden()

    controller.set_active(False)
    assert window.info_panel.isHidden()

    controller.set_info_visible(False)
    assert window.info_panel.isHidden()
    assert controller.toggle_info() is True
    assert window.info_panel.isVisible()

    controller.close()


def test_idle_only_hide_restores_pre_fullscreen_info_visibility(qt_app, overlay):
    window = overlay.window
    window.info_panel.show()
    controller = FullscreenController(window, overlay.view, overlay.header)
    controller.set_active(True)

    controller._hide_idle()
    assert window.info_panel.isHidden()

    controller.set_active(False)
    assert window.info_panel.isVisible()

    controller.close()


def test_events_from_another_window_do_not_reveal_overlay(qt_app, overlay):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    window.setFocus()
    controller._hide_idle()
    assert window.controls.isHidden()

    outside = QWidget()
    outside.resize(100, 100)
    outside.setMouseTracking(True)
    outside.show()
    qt_app.processEvents()
    controller.eventFilter(outside, QEvent(QEvent.MouseMove))
    controller.eventFilter(outside, QEvent(QEvent.KeyPress))
    qt_app.processEvents()

    assert window.controls.isHidden()

    outside.close()
    controller.close()


def test_watch_resize_repositions_overlays_without_insetting_video(qt_app, overlay):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    first_controls = window.controls.geometry()

    window.resize(1600, 820)
    qt_app.processEvents()

    assert window.video_stack.geometry() == window.watch.rect()
    assert window.controls.geometry() != first_controls
    assert window.controls.width() == min(window.watch.width() - 32, 1100)
    assert abs(window.controls.geometry().center().x() - window.watch.rect().center().x()) <= 1

    controller.close()


def test_close_stops_filter_and_timer_even_after_overlay_widgets_are_deleted(qt_app, overlay):
    window = overlay.window
    view = overlay.view
    header = overlay.header
    controller = FullscreenController(window, view, header)
    controller.set_active(True)
    assert controller._idle_timer.isActive()

    window.watch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    assert not isValid(window.watch)

    controller.close()
    controller.close()

    assert controller.active is False
    assert not controller._idle_timer.isActive()
    assert controller._application is None
