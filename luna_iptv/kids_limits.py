"""Local-day viewing budgets and an overnight bedtime for children's profiles."""

from datetime import datetime, time, timedelta

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from . import icons, theme
from .dialogs import text_label

LIMIT_CHOICES = (
    ("Sınırsız", 0),
    ("30 dk", 30),
    ("1 sa", 60),
    ("1,5 sa", 90),
    ("2 sa", 120),
    ("3 sa", 180),
)


def profile_limits(store, profile_id):
    raw = store.setting(f"kids_limits:{profile_id}", {})
    raw = raw if isinstance(raw, dict) else {}
    minutes = raw.get("minutes", 0)
    if type(minutes) is not int or minutes not in {m for _, m in LIMIT_CHOICES}:
        minutes = 0
    start, end = raw.get("start"), raw.get("end", "07:00")
    try:
        start = time.fromisoformat(start).strftime("%H:%M") if start else None
        end = time.fromisoformat(end).strftime("%H:%M")
    except (ValueError, TypeError):
        start, end = None, "07:00"
    return {"minutes": minutes, "start": start, "end": end}


def bedtime_active(now, start, end):
    if not start or start == end:
        return False
    current = now.strftime("%H:%M")
    return start <= current < end if start < end else current >= start or current < end


class KidsLimits(QObject):
    """Flush watch time before checking; only children's profiles run the timer."""

    def __init__(self, store, flush, stop, panel, toast, parent=None, *, clock=datetime.now):
        super().__init__(parent)
        self.store, self.flush, self.stop, self.panel, self.toast = store, flush, stop, panel, toast
        self.clock = clock
        self.reason = None
        self._blocked_key = None
        self._timer = QTimer(self)
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self.check)

    def reload(self):
        self._timer.stop()
        profile = self.store.profile(self.store.profile_id)
        if profile and profile["kids"]:
            self._timer.start()
        self.check()

    def _extra(self, now):
        value = self.store.setting(f"kids_extra:{self.store.profile_id}", {})
        if (
            not isinstance(value, dict)
            or value.get("day") != now.date().isoformat()
            or type(value.get("minutes")) is not int
            or value["minutes"] < 0
        ):
            return {"day": now.date().isoformat(), "minutes": 0, "until": None}
        try:
            until = datetime.fromisoformat(value["until"])
            if until.tzinfo is not None:
                raise ValueError("Local time required")
        except (KeyError, ValueError, TypeError):
            until = None
        return dict(value, until=until.isoformat() if until else None)

    def remaining(self, now=None):
        now = now or self.clock()
        limits = profile_limits(self.store, self.store.profile_id)
        if not limits["minutes"]:
            return None
        return (limits["minutes"] + self._extra(now)["minutes"]) * 60 - self.store.watch_seconds(
            now.date().isoformat()
        )

    def check(self):
        profile = self.store.profile(self.store.profile_id)
        reason = None
        now = self.clock()
        if profile and profile["kids"]:
            self.flush()
            limits = profile_limits(self.store, profile["id"])
            extra = self._extra(now)
            until = extra.get("until")
            grace = bool(until and now.isoformat() < until)
            remaining = self.remaining(now)
            bedtime = bedtime_active(now, limits["start"], limits["end"])
            warning_remaining = remaining
            if limits["start"] and limits["start"] != limits["end"]:
                deadline = datetime.combine(now.date(), time.fromisoformat(limits["start"]))
                if deadline < now:
                    deadline += timedelta(days=1)
                if grace:
                    deadline = max(deadline if not bedtime else now, datetime.fromisoformat(until))
                seconds = (deadline - now).total_seconds()
                warning_remaining = seconds if remaining is None else min(remaining, seconds)
            if bedtime and not grace:
                reason = "bedtime"
            elif remaining is not None and remaining <= 0:
                reason = "limit"
            elif warning_remaining is not None and warning_remaining <= 300:
                key = f"kids_warning:{profile['id']}"
                day = now.date().isoformat()
                if self.store.setting(key) != day:
                    self.store.set_setting(key, day)
                    self.toast("5 dakikan kaldı")
        else:
            self._timer.stop()
        self.reason = reason
        blocked_key = (self.store.profile_id, now.date(), reason) if reason else None
        if blocked_key != self._blocked_key:
            self._blocked_key = blocked_key
            if reason:
                self.stop()
            self.panel(reason)
        return reason is None

    def extend(self, minutes, authorize):
        """The authorization callback must ask the parent's PIN for each extension."""
        profile_id = self.store.profile_id
        profile = self.store.profile(profile_id)
        if (
            minutes not in (15, 30, 60)
            or not profile
            or not profile["kids"]
            or not self.store.pin_hash()
            or not authorize()
            or self.store.profile_id != profile_id
        ):
            return False
        now = self.clock()
        extra = self._extra(now)
        until = datetime.fromisoformat(extra["until"]) if extra.get("until") else now
        extra["minutes"] += minutes
        extra["until"] = (max(now, until) + timedelta(minutes=minutes)).isoformat()
        self.store.set_setting(f"kids_extra:{profile_id}", extra)
        self.check()
        return True

    def close(self):
        self._timer.stop()


class KidsLimitPanel(QWidget):
    """A quiet moon and two escape routes, covering the entire main window."""

    def __init__(self, parent, extend, switch):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.setSpacing(22)
        layout.addStretch()
        moon = QLabel()
        moon.setPixmap(icons.pixmap("moon", theme.GOLD, 112, self.devicePixelRatioF()))
        moon.setAlignment(Qt.AlignCenter)
        layout.addWidget(moon)
        self.heading = text_label("", "heading")
        self.heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.heading)
        note = text_label("Biraz dinlenelim. Yayınlar burada seni bekliyor.", "muted")
        note.setAlignment(Qt.AlignCenter)
        layout.addWidget(note)
        self.add_button = QPushButton("Ebeveyn: süre ekle")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(extend)
        layout.addWidget(self.add_button, 0, Qt.AlignCenter)
        self.switch_button = QPushButton("Profil değiştir")
        self.switch_button.clicked.connect(switch)
        layout.addWidget(self.switch_button, 0, Qt.AlignCenter)
        layout.addStretch()
        parent.installEventFilter(self)
        self.hide()

    def show_reason(self, reason):
        if not reason:
            self.hide()
            return
        self.heading.setText(
            "Bugünlük bu kadar, iyi geceler" if reason == "bedtime" else "Süren doldu"
        )
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.add_button.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.NIGHT))
        painter.end()
