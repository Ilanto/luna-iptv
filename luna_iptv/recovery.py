"""Bounded, event-driven recovery for live playback only."""

from __future__ import annotations

import math
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal


class RecoveryController(QObject):
    """Track one playback request and schedule bounded live reconnects."""

    changed = Signal()
    retry_requested = Signal(str)

    _RETRY_DELAYS_MS = (1000, 2000, 4000)
    _CONNECT_TIMEOUT_MS = 12_000
    _BUFFER_TIMEOUT_MS = 15_000
    _STABLE_PLAYBACK_MS = 15_000
    _MAX_PROGRESS_GAP_SECONDS = 3.0
    _RECOVERABLE_REASONS = {"error", "eof", "connect-timeout", "buffer-timeout"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer_generation = 0
        self._timer_slot = None
        self._closed = False
        self._active = False
        self._channel_id = ""
        self._live = False
        self._retry_enabled = True
        self._current_token: int | None = None
        self._attempt = 0
        self._state = "idle"
        self._timer_kind: str | None = None
        self._loaded = False
        self._paused = False
        self._buffering = False
        self._last_progress: float | None = None
        self._stable_started_at: float | None = None
        self._last_progress_at: float | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def attempt(self) -> int:
        return self._attempt

    @property
    def current_token(self) -> int | None:
        return self._current_token

    @property
    def can_cancel(self) -> bool:
        return (
            self._live
            and self._active
            and self._state
            in {
                "connecting",
                "untracked-connecting",
                "buffering",
                "waiting",
            }
        )

    @property
    def message(self) -> str:
        if self._state == "connecting":
            return (
                f"Canlı yayına yeniden bağlanılıyor ({self._attempt}/3)…"
                if self._attempt
                else "Canlı yayına bağlanılıyor…"
            )
        if self._state == "untracked-connecting":
            return "Yayın açılıyor…"
        if self._state == "buffering":
            return "Canlı yayın arabelleğe alınıyor…"
        if self._state == "waiting":
            delay = self._RETRY_DELAYS_MS[self._attempt - 1] // 1000
            return f"Bağlantı kesildi · {delay} sn sonra yeniden deneniyor ({self._attempt}/3)…"
        if self._state == "playing":
            return "Yayın oynatılıyor."
        if self._state == "paused":
            return "Yayın duraklatıldı."
        if self._state == "failed":
            return "Canlı yayına üç denemeden sonra bağlanılamadı."
        if self._state == "untracked-failed":
            return "Yayın başlatılamadı."
        return ""

    def begin(self, channel_id: str, live: bool) -> None:
        before = self._visible_state()
        self._stop_timer()
        self._active = True
        self._channel_id = str(channel_id)
        self._live = bool(live)
        self._retry_enabled = True
        self._current_token = None
        self._attempt = 0
        self._state = "connecting" if self._live else "idle"
        self._loaded = False
        self._paused = False
        self._buffering = False
        self._last_progress = None
        self._clear_stability()
        self._emit_if_changed(before)

    def watch(self, token: int | None) -> bool:
        if self._closed or not self._active or token is None:
            return False
        before = self._visible_state()
        self._current_token = int(token)
        self._loaded = False
        self._paused = False
        self._buffering = False
        self._last_progress = None
        self._clear_stability()
        if self._live:
            self._state = "connecting"
            self._schedule("connect", self._CONNECT_TIMEOUT_MS)
        self._emit_if_changed(before)
        return True

    def suppress_retries(self, token: int) -> bool:
        """Keep observing this request, but never reopen it automatically."""

        if not self._matches(token):
            return False
        before = self._visible_state()
        self._retry_enabled = False
        self._attempt = 0
        self._clear_stability()
        if not self._loaded:
            self._state = "untracked-connecting"
            self._schedule("connect", self._CONNECT_TIMEOUT_MS)
        elif self._paused:
            self._stop_timer()
            self._state = "paused"
        elif self._buffering:
            self._state = "buffering"
            self._schedule("buffer", self._BUFFER_TIMEOUT_MS)
        else:
            self._stop_timer()
            self._state = "playing"
        self._emit_if_changed(before)
        return True

    def loaded(self, token: int) -> bool:
        if not self._matches(token):
            return False
        before = self._visible_state()
        self._stop_timer()
        self._loaded = True
        self._clear_stability()
        self._state = "playing" if self._live else "idle"
        self._emit_if_changed(before)
        return True

    def progress(self, token: int, position: Any) -> bool:
        if not self._matches(token):
            return False
        if self._state in {"waiting", "untracked-connecting"}:
            return True
        number = self._number(position)
        if number is None:
            return True
        previous, self._last_progress = self._last_progress, number
        advancing = (
            self._live
            and self._loaded
            and self._attempt
            and not self._paused
            and not self._buffering
            and previous is not None
            and number > previous
        )
        if advancing:
            now = monotonic()
            if self._timer_kind != "stable":
                self._stable_started_at = now
                self._schedule("stable", self._STABLE_PLAYBACK_MS)
            self._last_progress_at = now
        return True

    def paused(self, token: int, paused: bool) -> bool:
        if not self._matches(token):
            return False
        if self._state in {"waiting", "untracked-connecting"}:
            return True
        before = self._visible_state()
        self._paused = bool(paused)
        if self._live and self._paused:
            self._stop_timer()
            self._clear_stability()
            self._state = "paused"
        elif self._live and self._loaded:
            if self._buffering:
                self._state = "buffering"
                self._schedule("buffer", self._BUFFER_TIMEOUT_MS)
            else:
                self._state = "playing"
        elif self._live:
            self._state = "connecting"
            self._schedule("connect", self._CONNECT_TIMEOUT_MS)
        self._emit_if_changed(before)
        return True

    def buffering(self, token: int, buffering: bool) -> bool:
        if not self._matches(token):
            return False
        if self._state in {"waiting", "untracked-connecting"}:
            return True
        before = self._visible_state()
        self._buffering = bool(buffering)
        if self._live and self._buffering and not self._paused:
            self._clear_stability()
            self._state = "buffering"
            self._schedule("buffer", self._BUFFER_TIMEOUT_MS)
        elif self._live and self._loaded and not self._paused:
            self._stop_timer()
            self._clear_stability()
            self._state = "playing"
        self._emit_if_changed(before)
        return True

    def failure(self, token: int, reason: str) -> bool:
        if not self._matches(token):
            return False
        if self._state == "waiting":
            return True
        before = self._visible_state()
        was_paused = self._paused
        self._stop_timer()
        self._loaded = False
        self._paused = False
        self._buffering = False
        self._last_progress = None
        self._clear_stability()
        if (
            was_paused
            or not self._retry_enabled
            or not self._live
            or reason not in self._RECOVERABLE_REASONS
        ):
            self._active = False
            self._state = "idle"
        else:
            self._schedule_retry()
        self._emit_if_changed(before)
        return True

    def cancel(self) -> None:
        if self._closed:
            return
        before = self._visible_state()
        self._stop_timer()
        self._active = False
        self._channel_id = ""
        self._live = False
        self._retry_enabled = True
        self._current_token = None
        self._attempt = 0
        self._state = "idle"
        self._loaded = False
        self._paused = False
        self._buffering = False
        self._last_progress = None
        self._clear_stability()
        self._emit_if_changed(before)

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True

    def _schedule_retry(self) -> None:
        if self._attempt >= len(self._RETRY_DELAYS_MS):
            self._active = False
            self._state = "failed"
            self._stop_timer()
            return
        delay = self._RETRY_DELAYS_MS[self._attempt]
        self._attempt += 1
        self._state = "waiting"
        self._schedule("retry", delay)

    def _schedule(self, kind: str, interval: int) -> None:
        self._stop_timer()
        self._timer_kind = kind
        generation = self._timer_generation
        self._timer_slot = lambda: self._timeout(generation)
        self._timer.timeout.connect(self._timer_slot)
        self._timer.start(interval)

    def _stop_timer(self) -> None:
        self._timer.stop()
        if self._timer_slot is not None:
            self._timer.timeout.disconnect(self._timer_slot)
            self._timer_slot = None
        self._timer_generation += 1
        self._timer_kind = None

    def _timeout(self, generation: int) -> None:
        if self._closed or not self._active or generation != self._timer_generation:
            return
        kind = self._timer_kind
        self._stop_timer()
        if kind in {"connect", "buffer"}:
            before = self._visible_state()
            if self._retry_enabled:
                self._schedule_retry()
            else:
                self._active = False
                self._state = "untracked-failed"
            self._emit_if_changed(before)
        elif kind == "retry":
            before = self._visible_state()
            self._current_token = None
            self._loaded = False
            self._paused = False
            self._buffering = False
            self._last_progress = None
            self._clear_stability()
            self._state = "connecting"
            self._emit_if_changed(before)
            self.retry_requested.emit(self._channel_id)
        elif kind == "stable":
            now = monotonic()
            if self._stable_started_at is None or self._last_progress_at is None:
                return
            elapsed_ms = round((now - self._stable_started_at) * 1000)
            if elapsed_ms < self._STABLE_PLAYBACK_MS:
                self._schedule("stable", self._STABLE_PLAYBACK_MS - elapsed_ms)
                return
            before = self._visible_state()
            if now - self._last_progress_at <= self._MAX_PROGRESS_GAP_SECONDS:
                self._attempt = 0
            self._clear_stability()
            self._emit_if_changed(before)

    def _clear_stability(self) -> None:
        self._stable_started_at = None
        self._last_progress_at = None

    def _matches(self, token: int) -> bool:
        return not self._closed and self._active and token == self._current_token

    def _visible_state(self) -> tuple[str, int, str, bool]:
        return (self._state, self._attempt, self.message, self.can_cancel)

    def _emit_if_changed(self, before: tuple[str, int, str, bool]) -> None:
        if self._visible_state() != before:
            self.changed.emit()

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None
