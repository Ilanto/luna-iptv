"""Small comforts while watching: a sleep timer, the next episode and channel numbers."""

import math
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from .dialogs import text_label

SLEEP_CHOICES = (15, 30, 45, 60, 90, 120)
NEXT_EPISODE_SECONDS = 10
NUMBER_PAUSE_MS = 1300


class SleepTimer(QObject):
    """Stops playback after some minutes, or when the programme or film ends."""

    changed = Signal()
    expired = Signal()

    def __init__(self, parent=None, *, clock=time.time):
        super().__init__(parent)
        self._clock = clock
        self.deadline = None
        self.mode = None  # "minutes" or "end"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

    @property
    def active(self):
        return self.deadline is not None

    def start(self, minutes):
        self._arm(self._clock() + minutes * 60, "minutes")

    def start_until(self, moment):
        """Stop at a moment in Unix seconds, such as the end of the programme."""
        self._arm(moment, "end")

    def _arm(self, deadline, mode):
        self.deadline, self.mode = deadline, mode
        self._timer.start(max(0, math.ceil((deadline - self._clock()) * 1000)))
        self.changed.emit()

    def extend(self, minutes):
        if self.deadline is not None:
            self._arm(self.deadline + minutes * 60, "minutes")

    def cancel(self):
        if self.deadline is None:
            return
        self._timer.stop()
        self.deadline = self.mode = None
        self.changed.emit()

    def remaining_minutes(self):
        if self.deadline is None:
            return 0
        return max(0, math.ceil((self.deadline - self._clock()) / 60))

    def label(self):
        """Short text for the button: '25 dk'."""
        return f"{self.remaining_minutes()} dk" if self.active else ""

    def _fire(self):
        if self.deadline is None:
            return
        if self._clock() < self.deadline - 0.5:  # a clock jump; wait for the rest
            self._arm(self.deadline, self.mode)
            return
        self.deadline = self.mode = None
        self.changed.emit()
        self.expired.emit()


class Countdown(QObject):
    """A visible countdown before something happens on its own, e.g. the next episode."""

    tick = Signal(int)
    due = Signal(object)

    def __init__(self, parent=None, seconds=NEXT_EPISODE_SECONDS):
        super().__init__(parent)
        self.seconds = seconds
        self.left = 0
        self.payload = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._step)

    @property
    def running(self):
        return self.payload is not None

    def start(self, payload):
        self.payload, self.left = payload, self.seconds
        self._timer.start()
        self.tick.emit(self.left)

    def cancel(self):
        self._timer.stop()
        self.payload = None

    def finish_now(self):
        payload = self.payload
        self.cancel()
        if payload is not None:
            self.due.emit(payload)

    def _step(self):
        self.left -= 1
        if self.left <= 0:
            self.finish_now()
        else:
            self.tick.emit(self.left)


class NumberEntry(QObject):
    """Digits typed one after another become a channel number after a short pause."""

    typing = Signal(str)
    chosen = Signal(int)

    def __init__(self, parent=None, pause_ms=NUMBER_PAUSE_MS):
        super().__init__(parent)
        self.digits = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(pause_ms)
        self._timer.timeout.connect(self.commit)

    def digit(self, value):
        if len(self.digits) >= 5:
            return
        self.digits += str(value)
        self._timer.start()
        self.typing.emit(self.digits)

    def commit(self):
        self._timer.stop()
        digits, self.digits = self.digits, ""
        if digits and int(digits) > 0:
            self.chosen.emit(int(digits))

    def cancel(self):
        self._timer.stop()
        self.digits = ""


def next_episode(channels, current):
    """The episode after ``current`` in catalogue order, which follows seasons."""
    if current is None or not current.series_id or current.kind != "movie":
        return None
    episodes = [c for c in channels if c.series_id == current.series_id and c.kind == "movie"]
    ids = [c.id for c in episodes]
    if current.id not in ids:
        return None
    index = ids.index(current.id)
    return episodes[index + 1] if index + 1 < len(episodes) else None


class WatchNotice(QFrame):
    """A slim banner above the video with up to two actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("watchNotice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 8, 8)
        layout.setSpacing(8)
        self.text = text_label("")
        self.text.setWordWrap(True)
        layout.addWidget(self.text, 1)
        self.primary = QPushButton()
        self.primary.setObjectName("primary")
        self.secondary = QPushButton()
        for button in (self.primary, self.secondary):
            button.setCursor(Qt.PointingHandCursor)
            button.setAutoDefault(False)
            button.hide()
            layout.addWidget(button)
        self._actions = {}
        self.primary.clicked.connect(lambda: self._run(self.primary))
        self.secondary.clicked.connect(lambda: self._run(self.secondary))
        self.hide()

    def _run(self, button):
        action = self._actions.get(button)
        if action is not None:
            action()

    def present(self, text, primary=None, secondary=None):
        """Show text with optional (label, callback) actions."""
        self.text.setText(text)
        self.setAccessibleName(text)
        for button, action in ((self.primary, primary), (self.secondary, secondary)):
            if action is None:
                button.hide()
                self._actions.pop(button, None)
            else:
                button.setText(action[0])
                self._actions[button] = action[1]
                button.show()
        self.show()

    def set_text(self, text):
        self.text.setText(text)
        self.setAccessibleName(text)
