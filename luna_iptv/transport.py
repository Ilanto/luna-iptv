"""Lightweight seek-based transport scanning for seekable media."""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal


class TransportController(QObject):
    """Coordinate exact skips and nominal-rate keyframe scanning."""

    changed = Signal()
    _pause_command_finished = Signal(int, int, bool, bool)

    _RATES = (2, 4, 8, 16)
    _TICK_MS = 500

    def __init__(self, player: Any, parent=None) -> None:
        super().__init__(parent)
        self._player = player
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._scan_tick)
        self._pause_command_finished.connect(self._on_pause_command_finished)

        self._closed = False
        self._loaded = False
        self._live = False
        self._seekable = False
        self._partially_seekable = False
        self._seeking = False
        self._paused = False
        self._position = 0.0
        self._duration = 0.0

        self._rate = 0
        self._entry_paused: bool | None = None
        self._pause_intent = False
        self._lifecycle_generation = 0
        self._pause_request_generation = 0
        self._pending_pause_requests: dict[int, bool] = {}
        self._scan_pause_request: int | None = None
        self._waiting_for_pause = False
        self._anchor_position = 0.0
        self._anchor_time = 0.0

    @property
    def can_seek(self) -> bool:
        return not self._closed and self._loaded and self._seekable

    @property
    def can_scan(self) -> bool:
        return (
            self.can_seek and not self._live and not self._partially_seekable and self._duration > 0
        )

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def label(self) -> str:
        return "1×" if self._rate == 0 else f"{self._rate:+d}×"

    def prepare(self, live: bool) -> None:
        before = self._visible_state()
        self._cancel(restore_pause=False)
        self._reset_pause_lifecycle()
        self._loaded = False
        self._live = bool(live)
        self._seekable = False
        self._partially_seekable = False
        self._seeking = False
        self._paused = False
        self._pause_intent = False
        self._position = 0.0
        self._duration = 0.0
        self._emit_if_changed(before)

    def loaded(self) -> None:
        if self._closed:
            return
        before = self._visible_state()
        self._loaded = True
        self._emit_if_changed(before)

    def finished(self) -> None:
        before = self._visible_state()
        self._cancel(restore_pause=False)
        self._reset_pause_lifecycle()
        self._loaded = False
        self._seekable = False
        self._partially_seekable = False
        self._seeking = False
        self._paused = False
        self._pause_intent = False
        self._position = 0.0
        self._duration = 0.0
        self._emit_if_changed(before)

    def observe(self, name: str, value: Any) -> None:
        if self._closed:
            return
        before = self._visible_state()

        if name == "time-pos":
            number = self._number(value)
            if number is not None:
                self._position = max(0.0, number)
        elif name == "duration":
            number = self._number(value)
            if number is not None:
                self._duration = max(0.0, number)
        elif name == "seekable":
            self._seekable = bool(value)
        elif name == "partially-seekable":
            self._partially_seekable = bool(value)
        elif name == "seeking":
            self._seeking = bool(value)
        elif name == "pause":
            self._paused = bool(value)
            if not self._pending_pause_requests and not self._rate:
                self._pause_intent = self._paused

        if self._rate and not self.can_scan:
            self._cancel(restore_pause=True)
        self._emit_if_changed(before)

    def seek_relative(self, seconds: float) -> bool:
        amount = self._number(seconds)
        if not self.can_seek or amount is None or amount == 0:
            return False
        self.cancel(restore_pause=True)
        self._player.command(["seek", amount, "relative+exact"])
        return True

    def cycle(self, direction: int) -> int:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        if not self.can_scan:
            return self._rate

        if self._rate and (self._rate > 0) == (direction > 0):
            index = self._RATES.index(abs(self._rate))
            if index == len(self._RATES) - 1:
                self.cancel(restore_pause=True)
                return 0
            next_rate = self._RATES[index + 1] * direction
        else:
            next_rate = self._RATES[0] * direction

        before = self._visible_state()
        entering = self._rate == 0
        if entering:
            self._entry_paused = self._logical_pause()
        self._rate = next_rate
        self._reanchor()
        if entering:
            # Order a pause after any pending restore before scan seeks begin.
            pause_request = self._request_pause(True, always=True, scan_barrier=True)
            if pause_request is None:
                self._rate = 0
                self._entry_paused = None
                self._emit_if_changed(before)
                return self._rate
            if self._rate == 0 or self._scan_pause_request != pause_request:
                self._emit_if_changed(before)
                return self._rate
        self._timer.start()
        self._emit_if_changed(before)
        return self._rate

    def normal_play(self) -> None:
        self.cancel(restore_pause=False)
        if not self._closed and self._loaded:
            self._request_pause(False, always=True)

    def cancel(self, restore_pause: bool = True) -> None:
        before = self._visible_state()
        self._cancel(restore_pause=restore_pause)
        self._emit_if_changed(before)

    def close(self) -> None:
        if self._closed:
            return
        before = self._visible_state()
        self._cancel(restore_pause=False)
        self._reset_pause_lifecycle()
        self._closed = True
        self._loaded = False
        self._seekable = False
        self._partially_seekable = False
        self._emit_if_changed(before)

    def _scan_tick(self) -> None:
        if not self._rate or not self.can_scan or self._waiting_for_pause or self._seeking:
            return

        elapsed = max(0.0, time.monotonic() - self._anchor_time)
        target = self._anchor_position + self._rate * elapsed
        target = min(self._duration, max(0.0, target))
        self._player.command(["seek", target, "absolute+keyframes"])

        if target <= 0.0 or target >= self._duration:
            self.cancel(restore_pause=True)

    def _reanchor(self) -> None:
        self._anchor_position = min(self._duration, max(0.0, self._position))
        self._anchor_time = time.monotonic()

    def _cancel(self, restore_pause: bool) -> None:
        was_scanning = self._rate != 0
        entry_paused = self._entry_paused
        self._timer.stop()
        self._rate = 0
        self._entry_paused = None
        self._scan_pause_request = None
        self._waiting_for_pause = False
        if (
            restore_pause
            and was_scanning
            and entry_paused is not None
            and self._logical_pause() != entry_paused
        ):
            self._request_pause(entry_paused)

    def _logical_pause(self) -> bool:
        return self._pause_intent

    def _request_pause(
        self, paused: bool, *, always: bool = False, scan_barrier: bool = False
    ) -> int | None:
        logical_pause = self._logical_pause()
        if not always and logical_pause == paused:
            return None

        self._pause_request_generation += 1
        request = self._pause_request_generation
        lifecycle = self._lifecycle_generation
        self._pause_intent = paused
        self._pending_pause_requests[request] = paused
        if scan_barrier:
            self._scan_pause_request = request
            self._waiting_for_pause = True

        future = self._player.set_property("pause", paused)
        if future is None:
            self._pending_pause_requests.pop(request, None)
            if scan_barrier:
                self._scan_pause_request = None
                self._waiting_for_pause = False
            self._pause_intent = self._latest_pause_intent()
            return None

        def completed(done_future) -> None:
            try:
                succeeded = done_future.exception() is None
            except Exception:
                succeeded = False
            try:
                self._pause_command_finished.emit(lifecycle, request, paused, succeeded)
            except RuntimeError:
                # The QObject may already be deleted during application teardown.
                pass

        future.add_done_callback(completed)
        return request

    def _on_pause_command_finished(
        self, lifecycle: int, request: int, paused: bool, succeeded: bool
    ) -> None:
        if lifecycle != self._lifecycle_generation or request not in self._pending_pause_requests:
            return
        before = self._visible_state()
        self._pending_pause_requests.pop(request)
        is_latest_request = request == self._pause_request_generation

        if succeeded and is_latest_request:
            self._paused = paused
        elif not succeeded and is_latest_request:
            self._pause_intent = self._latest_pause_intent()

        if request == self._scan_pause_request:
            self._waiting_for_pause = False
            if not succeeded:
                self._cancel(restore_pause=False)
        self._emit_if_changed(before)

    def _latest_pause_intent(self) -> bool:
        if self._pending_pause_requests:
            return self._pending_pause_requests[max(self._pending_pause_requests)]
        return self._paused

    def _reset_pause_lifecycle(self) -> None:
        self._lifecycle_generation += 1
        self._pending_pause_requests.clear()
        self._scan_pause_request = None
        self._waiting_for_pause = False

    def _visible_state(self) -> tuple[bool, bool, int, bool]:
        return (self.can_seek, self.can_scan, self._rate, self._paused)

    def _emit_if_changed(self, before: tuple[bool, bool, int, bool]) -> None:
        if self._visible_state() != before:
            self.changed.emit()

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None
