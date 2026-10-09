"""Single-shot, event-driven catalogue refresh and daily expiry notices."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from datetime import time as civil_time

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .backup import source_incomplete
from .settings import REFRESH_CHOICES, refresh_time, selected_setting


def _local_slot(day, at, tz=None):
    # With no explicit zone, naive.timestamp uses the system's timezone rules
    # for this date (unlike datetime.now().astimezone().tzinfo's fixed offset).
    hour, minute = map(int, at.split(":"))
    return datetime.combine(day, civil_time(hour, minute), tzinfo=tz).timestamp()


def next_refresh_due(last, now, mode="daily", at="05:00", tz=None):
    """Return an epoch deadline. Daily slots follow local dates across DST.

    Nonexistent spring times normalize forward; an ambiguous autumn slot uses
    its first occurrence. A completed slot is never repeated on the same date.
    """
    if mode == "off":
        return None
    if type(last) not in (int, float) or not math.isfinite(last) or last <= 0:
        return now
    if mode == "6h":
        return last + 6 * 3600
    try:
        day = datetime.fromtimestamp(last, tz).date()
        slot = _local_slot(day, at, tz)
        if slot <= last:
            slot = _local_slot(day + timedelta(days=1), at, tz)
        return slot
    except (OverflowError, OSError, ValueError):
        return now


class RefreshScheduler(QObject):
    """Refresh receives (source, quiet=True, on_finished(success, changed)).

    Occupied sources wait for wake() from application lifecycle events, not a
    periodic retry timer. Network failures receive a bounded 30 minute backoff.
    """

    refreshed = Signal()

    def __init__(self, store, refresh, available, status, parent=None, *, clock=time.time):
        super().__init__(parent)
        self._store, self._refresh, self._available, self._status = (
            store,
            refresh,
            available,
            status,
        )
        self._clock = clock
        self._closed = False
        self._active = None
        self._retry_after = {}
        self._expiry_notice_until = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self.check)

    def start(self):
        self.wake()

    def wake(self):
        if not self._closed:
            self._timer.start(0)

    def _mapping(self, key):
        value = self._store.setting(key, {})
        return dict(value) if isinstance(value, dict) else {}

    def _due(self, source, now):
        mode = selected_setting(self._store, "auto_refresh", REFRESH_CHOICES)
        due = next_refresh_due(
            self._mapping("auto_refresh_last").get(source["id"]),
            now,
            mode,
            refresh_time(self._store),
        )
        return max(due, self._retry_after.get(source["id"], 0)) if due is not None else None

    def check(self):
        if self._closed:
            return
        self._timer.stop()
        now = self._clock()
        if self._active is None:
            self._expiry_notices(now)
        if self._active is None and now >= self._expiry_notice_until:
            for source in self._store.sources():
                if source_incomplete(source) or not self._available(source):
                    continue
                due = self._due(source, now)
                if due is None or due > now:
                    continue
                self._active = source["id"]
                source_id = source["id"]

                def finished(success, changed=False, source_id=source_id):
                    if self._closed or self._active != source_id:
                        return
                    self._active = None
                    if success:
                        times = self._mapping("auto_refresh_last")
                        if any(s["id"] == source_id for s in self._store.sources()):
                            times[source_id] = self._clock()
                            self._store.set_setting("auto_refresh_last", times)
                        self._retry_after.pop(source_id, None)
                        self.refreshed.emit()
                    else:
                        self._retry_after[source_id] = self._clock() + 1800
                    if success and changed:
                        self._status("Kaynaklar güncellendi")
                    self.wake()

                try:
                    accepted = self._refresh(source, quiet=True, on_finished=finished)
                except Exception:
                    self._status("Otomatik yenileme tamamlanamadı.")
                    finished(False)
                else:
                    if accepted is False:
                        finished(False)
                break
        self._arm()

    def _arm(self):
        if self._closed:
            return
        now = self._clock()
        tomorrow = datetime.fromtimestamp(now).date() + timedelta(days=1)
        deadlines = [_local_slot(tomorrow, "00:00")]
        if self._expiry_notice_until > now:
            deadlines.append(self._expiry_notice_until)
        if self._active is None:
            for source in self._store.sources():
                if source_incomplete(source):
                    continue
                due = self._due(source, now)
                if due is not None and (due > now or self._available(source)):
                    deadlines.append(max(due, self._expiry_notice_until))
        delay = math.ceil(max(0, min(deadlines) - now) * 1000)
        self._timer.start(min(delay, 2**31 - 1))

    def _expiry_notices(self, now):
        if now < self._expiry_notice_until:
            return
        today = datetime.fromtimestamp(now).date().isoformat()
        notified = self._mapping("auto_refresh_expiry_notified")
        for source in self._store.sources():
            if source["type"] != "xtream" or notified.get(source["id"]) == today:
                continue
            account = self._store.account_profile(source["id"])
            if account is None or account.expires_at is None:
                continue
            remaining = account.expires_at - now
            if 0 < remaining <= 7 * 86400:
                days = math.ceil(remaining / 86400)
                notified[source["id"]] = today
                self._store.set_setting("auto_refresh_expiry_notified", notified)
                self._status(f"{source['name']} aboneliği {days} gün içinde bitiyor")
                # Toasts replace each other. Give this account a full display
                # lifetime before checking the next account or starting refresh.
                # Pending accounts stay unmarked, including across app restarts.
                self._expiry_notice_until = now + 7
                break

    def close(self):
        self._closed = True
        self._active = None
        self._timer.stop()
