"""Elapsed watch time and the active profile's compact weekly report."""

import time
from datetime import datetime, timedelta
from datetime import time as day_time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from . import theme
from .dialogs import text_label


class WatchTracker:
    def __init__(self, store, *, monotonic=time.monotonic, clock=datetime.now):
        self.store, self.monotonic, self.clock = store, monotonic, clock
        self.channel_id = None
        self.profile_id = None
        self.running = False
        self._last = monotonic()

    def begin(self, channel_id):
        self.finish()
        self.channel_id = channel_id
        self.profile_id = self.store.profile_id

    def set_running(self, running):
        self.flush()
        self.running = bool(running and self.channel_id)

    def flush(self):
        now = self.monotonic()
        seconds = max(0.0, now - self._last)
        self._last = now
        if not self.running or not self.channel_id or seconds <= 0:
            return
        # Monotonic duration ignores seeks and wall-clock changes; local dates
        # split a short checkpoint interval at midnight instead of losing a day.
        end = self.clock()
        start = end - timedelta(seconds=seconds)
        while start < end:
            midnight = datetime.combine(start.date() + timedelta(days=1), day_time())
            stop = min(midnight, end)
            self.store.add_watch_time(
                self.channel_id,
                start.date().isoformat(),
                (stop - start).total_seconds(),
                profile_id=self.profile_id,
            )
            start = stop

    def finish(self):
        self.set_running(False)
        self.channel_id = None


def duration_text(seconds):
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} sa {minutes} dk" if minutes else f"{hours} sa"
    return f"{minutes} dk" if minutes else "1 dk'dan az" if seconds else "0 dk"


class WeekChart(QWidget):
    def __init__(self, days, parent=None):
        super().__init__(parent)
        self.days = days
        self.setMinimumSize(340, 190)
        self.setAccessibleName("Son 7 günün izleme süreleri")
        self.setAccessibleDescription("; ".join(f"{day}: {duration_text(s)}" for day, s in days))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        peak = max(60, *(seconds for _, seconds in self.days))
        width = self.width() / 7
        baseline = self.height() - 28
        for index, (day, seconds) in enumerate(self.days):
            x = index * width
            height = (baseline - 30) * seconds / peak
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(
                QRectF(x + width * 0.25, baseline - height, width * 0.5, max(2, height)), 4, 4
            )
            painter.setPen(QColor(theme.TEXT_SOFT))
            painter.drawText(
                QRectF(x, baseline + 5, width, 22), Qt.AlignCenter, day[8:] + "." + day[5:7]
            )
            painter.drawText(
                QRectF(x, baseline - height - 25, width, 22),
                Qt.AlignCenter,
                f"{seconds / 3600:.1f} sa",
            )
        painter.end()


class StatisticsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("İstatistikler")
        self.resize(540, 540)
        stats = store.watch_statistics()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        profile = store.profile(store.profile_id)
        layout.addWidget(text_label(f"{profile['name']} · İstatistikler", "heading"))
        self.total_label = text_label(
            f"Bu hafta {stats['week_seconds'] / 3600:.1f} sa izledin", "title"
        )
        layout.addWidget(self.total_label)
        layout.addWidget(text_label("Pazartesiden bugüne · gerçek izleme süresi", "muted"))
        layout.addWidget(text_label("SON 7 GÜN", "eyebrow"))
        self.chart = WeekChart(stats["days"])
        layout.addWidget(self.chart)
        layout.addWidget(text_label("BU HAFTA EN ÇOK İZLEDİKLERİN", "eyebrow"))
        for rank, (name, seconds) in enumerate(stats["top"], 1):
            label = text_label(f"{rank}. {name}  ·  {duration_text(seconds)}", "muted")
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        if not stats["top"]:
            layout.addWidget(text_label("İzledikçe burada birikir.", "muted"))
        layout.addWidget(
            text_label(
                f"Canlı: {duration_text(stats['live_seconds'])}  ·  "
                f"Film / dizi: {duration_text(stats['vod_seconds'])}",
                "title",
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Kapat")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
