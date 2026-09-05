"""Fullscreen overlay layout without recreating the native video widget."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget
from shiboken6 import isValid


class FullscreenController(QObject):
    """Move existing controls over a full-bleed video while fullscreen is active."""

    IDLE_MILLISECONDS = 2500
    OVERLAY_MARGIN = 16
    OVERLAY_GAP = 10
    OVERLAY_MAX_WIDTH = 1100

    def __init__(self, window, view_layout, header):
        super().__init__(window)
        self._window = window
        self._view_layout = view_layout
        self._header = header
        self.active = False
        self.compact = False
        self._compact_hidden = []
        self._compact_time_policy = None
        self._closed = False
        self._controls_visible = False
        self._info_open = False
        self._keyboard_navigation = False
        self._pointer_in_overlay = False
        self._normal_margins = None
        self._normal_spacing = -1
        self._normal_indices = []
        self._normal_hidden = []
        self._cursor_state = []
        self._mouse_tracking_state = []

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_MILLISECONDS)
        self._idle_timer.timeout.connect(self._hide_idle)

        self._application = QApplication.instance()
        if self._application is not None:
            self._application.installEventFilter(self)

    def set_active(self, active: bool):
        active = bool(active)
        if self._closed:
            return
        if active == self.active:
            if active:
                self.reveal()
            return
        if active:
            if not self._essential_widgets_are_valid():
                return
            self._snapshot_normal_state()
            self.active = True
            self._enter()
        else:
            self._leave()
            self.active = False

    def set_compact(self, compact: bool):
        """Use compact controls without changing the native fullscreen state."""
        compact = bool(compact)
        if self._closed or compact == self.compact:
            return
        self.compact = compact
        controls = self._widget("controls")
        if compact and controls is not None:
            self._compact_hidden = [
                (widget, widget.isHidden())
                for widget in controls.findChildren(QWidget)
                if widget.property("mini_hidden")
            ]
            for widget, _hidden in self._compact_hidden:
                widget.hide()
        else:
            for widget, hidden in self._compact_hidden:
                if self._valid(widget):
                    widget.setVisible(not hidden)
            self._compact_hidden = []
        time_label = self._widget("time_label")
        if time_label is not None:
            if compact:
                self._compact_time_policy = time_label.sizePolicy()
                time_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            elif self._compact_time_policy is not None:
                time_label.setSizePolicy(self._compact_time_policy)
                self._compact_time_policy = None
        status = self._widget("mini_status_row") or self._widget("mini_status")
        if status is not None:
            status.setVisible(compact)
        if self.active:
            self.reveal()
            self.layout_overlays()

    def reveal(self):
        if self._closed or not self.active:
            return
        controls = self._widget("controls")
        info_panel = self._widget("info_panel")
        if controls is None:
            return

        needs_layout = not self._controls_visible or controls.isHidden()
        if info_panel is not None:
            needs_layout = needs_layout or info_panel.isHidden() == (
                self._info_open and not self.compact
            )
        controls.show()
        if info_panel is not None:
            info_panel.setVisible(self._info_open and not self.compact)
        if not self._controls_visible:
            self._restore_cursors()
        self._controls_visible = True
        if needs_layout:
            self.layout_overlays()
        self._idle_timer.start()

    def set_info_visible(self, visible: bool) -> bool:
        info_panel = self._widget("info_panel")
        visible = bool(visible)
        if info_panel is None:
            return False
        if not self.active:
            info_panel.setVisible(visible)
            return visible

        self._normal_hidden = [
            (widget, not visible if widget is info_panel else hidden)
            for widget, hidden in self._normal_hidden
        ]
        changed = visible != self._info_open
        self._info_open = visible
        if visible:
            self.reveal()
        else:
            info_panel.hide()
            if changed and self._controls_visible:
                self.layout_overlays()
        return visible

    def toggle_info(self) -> bool:
        info_panel = self._widget("info_panel")
        if info_panel is None:
            return False
        visible = not self._info_open if self.active else info_panel.isHidden()
        return self.set_info_visible(visible)

    def layout_overlays(self):
        if self._closed or not self.active:
            return
        watch = self._widget("watch")
        controls = self._widget("controls")
        if watch is None or controls is None:
            return

        bounds = watch.rect()
        available_width = max(0, bounds.width() - 2 * (8 if self.compact else self.OVERLAY_MARGIN))
        overlay_width = min(available_width, self.OVERLAY_MAX_WIDTH)
        if overlay_width <= 0 or bounds.height() <= 0:
            return

        controls_height = self._overlay_height(controls, bounds.height())
        controls_x = bounds.x() + (bounds.width() - overlay_width) // 2
        controls_y = max(
            bounds.y(),
            bounds.bottom() - (8 if self.compact else self.OVERLAY_MARGIN) - controls_height + 1,
        )
        controls.setGeometry(controls_x, controls_y, overlay_width, controls_height)
        controls.raise_()

        info_panel = self._widget("info_panel")
        if info_panel is not None:
            available_height = max(1, controls_y - bounds.y() - self.OVERLAY_GAP)
            info_height = self._overlay_height(info_panel, available_height)
            info_y = max(bounds.y(), controls_y - self.OVERLAY_GAP - info_height)
            info_panel.setGeometry(controls_x, info_y, overlay_width, info_height)
            info_panel.raise_()
            controls.raise_()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._idle_timer.stop()
        application, self._application = self._application, None
        if application is not None and self._valid(application):
            application.removeEventFilter(self)
        if self.active:
            self._leave()
        else:
            self._restore_mouse_tracking()
            self._restore_cursors()
        self.active = False

    def eventFilter(self, watched, event):
        if not isinstance(watched, QWidget):
            return False
        if (
            getattr(self, "_closed", True)
            or not getattr(self, "active", False)
            or not self._valid(watched)
        ):
            return False
        if not self._belongs_to_window(watched):
            return False
        event_type = event.type()
        if watched is self._widget("watch") and event_type == QEvent.Resize:
            self.layout_overlays()
        if event_type == QEvent.LayoutRequest and (
            watched is self._widget("watch") or self._is_overlay_widget(watched)
        ):
            self.layout_overlays()
        if event_type == QEvent.FocusIn and self._is_overlay_widget(watched):
            reason = event.reason()
            self._keyboard_navigation = reason in (
                Qt.TabFocusReason,
                Qt.BacktabFocusReason,
                Qt.ShortcutFocusReason,
            )
            if self._keyboard_navigation:
                self.reveal()
        elif event_type == QEvent.FocusOut and self._is_overlay_widget(watched):
            self._keyboard_navigation = False
        elif event_type == QEvent.Enter and self._is_overlay_widget(watched):
            self._pointer_in_overlay = True
            self.reveal()
        elif event_type == QEvent.Leave and self._is_overlay_widget(watched):
            self._pointer_in_overlay = False
        elif event_type in (
            QEvent.MouseMove,
            QEvent.MouseButtonPress,
            QEvent.Wheel,
        ):
            if self._is_overlay_widget(watched):
                self._pointer_in_overlay = True
            else:
                self._pointer_in_overlay = False
                self._keyboard_navigation = False
            self.reveal()
        elif event_type == QEvent.KeyPress:
            self.reveal()
        return False

    def _enter(self):
        for widget in self._normal_panels():
            if self._valid(widget):
                widget.hide()

        if self._valid(self._view_layout):
            for widget in (self._widget("info_panel"), self._widget("controls")):
                if widget is not None:
                    self._view_layout.removeWidget(widget)
            self._view_layout.setContentsMargins(0, 0, 0, 0)
            self._view_layout.setSpacing(0)
            self._view_layout.invalidate()
            self._view_layout.activate()

        self._controls_visible = False
        self._keyboard_navigation = False
        self._pointer_in_overlay = False
        self._set_overlay_mouse_tracking(True)
        self.reveal()

    def _leave(self):
        self._idle_timer.stop()
        self._restore_cursors()
        self._restore_mouse_tracking()

        if self._valid(self._view_layout):
            for index, widget in sorted(self._normal_indices, key=lambda item: item[0]):
                if index >= 0 and self._valid(widget) and self._view_layout.indexOf(widget) < 0:
                    self._view_layout.insertWidget(index, widget)
            if self._normal_margins is not None:
                self._view_layout.setContentsMargins(self._normal_margins)
            self._view_layout.setSpacing(self._normal_spacing)
            self._view_layout.invalidate()
            self._view_layout.activate()

        for widget, hidden in self._normal_hidden:
            if self._valid(widget):
                widget.setVisible(not hidden)
        self._controls_visible = False
        self._keyboard_navigation = False
        self._pointer_in_overlay = False

    def _snapshot_normal_state(self):
        self._normal_margins = self._view_layout.contentsMargins()
        self._normal_spacing = self._view_layout.spacing()
        self._normal_indices = [
            (self._view_layout.indexOf(widget), widget)
            for widget in (self._widget("info_panel"), self._widget("controls"))
            if widget is not None
        ]
        widgets = [*self._normal_panels()]
        for widget in (self._widget("info_panel"), self._widget("controls")):
            if widget is not None:
                widgets.append(widget)
        self._normal_hidden = [
            (widget, widget.isHidden()) for widget in widgets if self._valid(widget)
        ]
        info_panel = self._widget("info_panel")
        self._info_open = info_panel is not None and not info_panel.isHidden()
        self._cursor_state = []
        self._mouse_tracking_state = []
        for widget in (self._widget("watch"), self._widget("video")):
            if widget is not None:
                self._cursor_state.append(
                    (widget, widget.testAttribute(Qt.WA_SetCursor), QCursor(widget.cursor()))
                )
                self._mouse_tracking_state.append((widget, widget.hasMouseTracking()))

    def _hide_idle(self):
        if self._closed or not self.active:
            return
        if self._interaction_blocks_hiding():
            self._idle_timer.start()
            return
        controls = self._widget("controls")
        info_panel = self._widget("info_panel")
        if controls is not None:
            controls.hide()
        if info_panel is not None:
            info_panel.hide()
        self._controls_visible = False
        self._set_blank_cursor()

    def _interaction_blocks_hiding(self) -> bool:
        for name in ("seek", "volume"):
            slider = self._widget(name)
            if slider is not None and slider.isSliderDown():
                return True

        popup = QApplication.activePopupWidget()
        if popup is not None and self._belongs_to_window(popup):
            return True

        if self._pointer_in_overlay:
            return True

        focus = QApplication.focusWidget()
        if not self._keyboard_navigation or focus is None or not self._belongs_to_window(focus):
            return False
        return self._is_overlay_widget(focus)

    def _is_overlay_widget(self, widget) -> bool:
        if not self._valid(widget):
            return False
        return any(
            container is not None and (widget is container or container.isAncestorOf(widget))
            for container in (self._widget("controls"), self._widget("info_panel"))
        )

    def _belongs_to_window(self, widget) -> bool:
        window = self._window if self._valid(self._window) else None
        current = widget if self._valid(widget) else None
        while current is not None:
            if current is window:
                return True
            try:
                current = current.parentWidget()
            except RuntimeError:
                return False
        return False

    def _normal_panels(self):
        return tuple(
            widget
            for widget in (
                self._widget("sidebar"),
                self._widget("library"),
                self._widget("guide"),
                self._widget("message_bar"),
                self._header if self._valid(self._header) else None,
            )
            if widget is not None
        )

    def _essential_widgets_are_valid(self) -> bool:
        return self._valid(self._view_layout) and all(
            self._widget(name) is not None
            for name in ("watch", "video_stack", "info_panel", "controls")
        )

    def _widget(self, name):
        if not self._valid(self._window):
            return None
        try:
            widget = getattr(self._window, name, None)
        except RuntimeError:
            return None
        return widget if self._valid(widget) else None

    def _set_overlay_mouse_tracking(self, enabled: bool):
        for widget in (self._widget("watch"), self._widget("video")):
            if widget is not None:
                widget.setMouseTracking(enabled)

    def _restore_mouse_tracking(self):
        for widget, enabled in self._mouse_tracking_state:
            if self._valid(widget):
                widget.setMouseTracking(enabled)

    def _set_blank_cursor(self):
        for widget in (self._widget("watch"), self._widget("video")):
            if widget is not None:
                widget.setCursor(Qt.BlankCursor)

    def _restore_cursors(self):
        for widget, had_explicit_cursor, cursor in self._cursor_state:
            if not self._valid(widget):
                continue
            if had_explicit_cursor:
                widget.setCursor(cursor)
            else:
                widget.unsetCursor()

    @staticmethod
    def _overlay_height(widget, maximum: int) -> int:
        preferred = max(widget.minimumHeight(), widget.sizeHint().height())
        return max(1, min(preferred, maximum))

    @staticmethod
    def _valid(obj) -> bool:
        return obj is not None and isValid(obj)
