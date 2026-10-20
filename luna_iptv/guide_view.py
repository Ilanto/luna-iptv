"""The programme guide: channels down the side, time across the top, a gold "now" line.

The grid paints only the rows and minutes on screen and asks the guide index for
just that window, so thousands of channels scroll as smoothly as a dozen.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

from PySide6.QtCore import QDate, QElapsedTimer, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .catchup import can_catchup
from .dialogs import text_label
from .i18n import _, _n, day_label, format_number, month_name, weekday_name
from .library import search_key, tile_color
from .motion import IconButton, motion_level, on_motion_changed

ROW = 64
HEADER = 40
LOGO_COLUMN = 190
PX_PER_MINUTE = 5


def reminded_key(channel, programme):
    """How reminders identify a programme: the channel and its start in Unix seconds."""
    return channel.id, int(programme.start.timestamp())


def local_day(day):
    """UTC bounds of a local calendar day; 23 or 25 hours long on DST changes."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


class GuideGrid(QAbstractScrollArea):
    """Painted, virtualised timetable. Emits the programme or channel a person picks."""

    programme_clicked = Signal(object, object)  # channel, programme
    channel_clicked = Signal(object)

    def __init__(self, logos=None, parent=None):
        super().__init__(parent)
        self.setObjectName("guideGrid")
        self.logos = logos
        self.rows = []  # (channel, GuideIndex)
        self.day_start, self.day_end = local_day(datetime.now().date())
        self.matches = set()  # id(programme) of search hits
        self.reminded = set()  # (channel_id, start timestamp) with a reminder
        self._hover = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.horizontalScrollBar().setSingleStep(PX_PER_MINUTE * 15)
        self.verticalScrollBar().setSingleStep(ROW // 2)
        self._logo_timer = QTimer(self)
        self._logo_timer.setSingleShot(True)
        self._logo_timer.setInterval(60)
        self._logo_timer.timeout.connect(self._request_logos)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        if logos is not None:
            logos.ready.connect(lambda _url: self.viewport().update())
        # The "now" line moves; a minute's resolution is plenty.
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self._refresh_now)
        self._now = datetime.now(timezone.utc)
        self._pulse_clock = QElapsedTimer()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        on_motion_changed(self._motion_changed)

    def _refresh_now(self):
        # Keep line/badge geometry fixed between minute updates: pulse frames
        # then invalidate only the halo, without leaving trails or moving text.
        self._now = datetime.now(timezone.utc)
        self.viewport().update()

    @staticmethod
    def pulse_alpha(elapsed_ms):
        return 18 + 22 * (1 - math.cos(math.tau * elapsed_ms / 2400)) / 2

    def _now_strip(self):
        x = self.x_for(self._now)
        if not (self.day_start <= self._now < self.day_end):
            return QRect()
        if not LOGO_COLUMN <= x <= self.viewport().width():
            return QRect()
        return QRect(
            math.floor(x) - 8, HEADER - 7, 17, self.viewport().height() - HEADER + 7
        ).intersected(self.viewport().rect())

    def _pulse_tick(self):
        strip = self._now_strip()
        if not strip.isEmpty():
            self.viewport().update(strip)

    def _motion_changed(self):
        if motion_level() == "full" and self.isVisible():
            self._pulse_clock.start()
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
        self._pulse_tick()  # erase the halo immediately when motion is reduced

    # --- data -------------------------------------------------------------------
    def set_rows(self, rows):
        self.rows = list(rows)
        self._update_ranges()
        self._scrolled()

    def set_day(self, day):
        self.day_start, self.day_end = local_day(day)
        self.viewport().update()

    def day_minutes(self):
        return round((self.day_end - self.day_start).total_seconds() / 60)

    def _update_ranges(self):
        width = self.day_minutes() * PX_PER_MINUTE
        self.horizontalScrollBar().setRange(
            0, max(0, width - (self.viewport().width() - LOGO_COLUMN))
        )
        self.horizontalScrollBar().setPageStep(max(1, self.viewport().width() - LOGO_COLUMN))
        self.verticalScrollBar().setRange(
            0, max(0, len(self.rows) * ROW - (self.viewport().height() - HEADER))
        )
        self.verticalScrollBar().setPageStep(max(1, self.viewport().height() - HEADER))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_ranges()
        self._scrolled()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_now()
        self._clock.start()
        self._motion_changed()
        self._logo_timer.start()

    def hideEvent(self, event):
        self._clock.stop()
        self._pulse_timer.stop()
        self._logo_timer.stop()
        super().hideEvent(event)

    def _scrolled(self, *_):
        self._logo_timer.start()
        self.viewport().update()

    def _visible_rows(self):
        first = self.verticalScrollBar().value() // ROW
        count = (self.viewport().height() - HEADER) // ROW + 2
        return range(first, min(len(self.rows), first + count))

    def _request_logos(self):
        if self.logos is None:
            return
        urls = [self.rows[i][0].logo for i in self._visible_rows() if self.rows[i][0].logo]
        self.logos.request_visible(urls)

    # --- geometry ----------------------------------------------------------------
    def x_for(self, moment):
        minutes = (moment - self.day_start).total_seconds() / 60
        return LOGO_COLUMN + minutes * PX_PER_MINUTE - self.horizontalScrollBar().value()

    def moment_at(self, x):
        minutes = (x - LOGO_COLUMN + self.horizontalScrollBar().value()) / PX_PER_MINUTE
        return self.day_start + timedelta(minutes=minutes)

    def row_y(self, row):
        return HEADER + row * ROW - self.verticalScrollBar().value()

    def scroll_to(self, moment, *, lead_minutes=30):
        minutes = (moment - self.day_start).total_seconds() / 60 - lead_minutes
        self.horizontalScrollBar().setValue(round(max(0, minutes) * PX_PER_MINUTE))

    def scroll_to_row(self, row):
        top = row * ROW
        bar = self.verticalScrollBar()
        if not bar.value() <= top <= bar.value() + bar.pageStep() - ROW:
            bar.setValue(max(0, top - ROW))

    def programme_rect(self, row, programme):
        left = max(self.x_for(programme.start), LOGO_COLUMN)
        right = self.x_for(programme.end)
        y = self.row_y(row)
        return QRectF(left + 2, y + 4, right - left - 4, ROW - 8)

    def hit(self, position):
        """(channel, programme or None) under a viewport position, or None."""
        if position.y() < HEADER:
            return None
        row = int((position.y() - HEADER + self.verticalScrollBar().value()) // ROW)
        if not 0 <= row < len(self.rows):
            return None
        channel, index = self.rows[row]
        if position.x() < LOGO_COLUMN:
            return channel, None
        moment = self.moment_at(position.x())
        for programme in index.between(channel.tvg_id, moment, moment + timedelta(seconds=1)):
            return channel, programme
        return channel, None

    # --- painting ----------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.viewport().width(), self.viewport().height()
        painter.fillRect(self.viewport().rect(), QColor(theme.NIGHT))
        view_start = self.moment_at(LOGO_COLUMN)
        view_end = self.moment_at(width)
        now = self._now
        font = QFont(self.font())
        for row in self._visible_rows():
            channel, index = self.rows[row]
            y = self.row_y(row)
            if row % 2:
                painter.fillRect(QRectF(LOGO_COLUMN, y, width, ROW), QColor(theme.DUSK))
            for programme in index.between(channel.tvg_id, view_start, view_end):
                self._paint_programme(painter, font, row, channel, programme, now)
        self._paint_ruler(painter, font, width)
        painter.fillRect(QRectF(0, HEADER, LOGO_COLUMN, height), QColor(theme.DUSK))
        for row in self._visible_rows():
            self._paint_channel(painter, font, row)
        x = self.x_for(now)
        if LOGO_COLUMN <= x <= width and self.day_start <= now < self.day_end:
            if self._pulse_timer.isActive():
                halo = QColor(theme.GOLD)
                alpha = self.pulse_alpha(self._pulse_clock.elapsed())
                for stroke, opacity in ((12, alpha / 3), (8, alpha / 2), (4, alpha)):
                    halo.setAlpha(round(opacity))
                    painter.setPen(QPen(halo, stroke))
                    painter.drawLine(QPointF(x, HEADER - 1), QPointF(x, height))
            painter.setPen(QPen(QColor(theme.GOLD), 2))
            painter.drawLine(QPointF(x, HEADER - 6), QPointF(x, height))
            badge = QRectF(x - 26, 6, 52, 20)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.GOLD))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setPen(QColor(theme.ACCENT_INK))
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignCenter, now.astimezone().strftime("%H:%M"))
        painter.fillRect(QRectF(0, 0, LOGO_COLUMN, HEADER), QColor(theme.DUSK))

    def _paint_ruler(self, painter, font, width):
        painter.fillRect(QRectF(LOGO_COLUMN, 0, width, HEADER), QColor(theme.DUSK))
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        step = 30
        first = (self.horizontalScrollBar().value() // (step * PX_PER_MINUTE)) * step
        for minutes in range(first, self.day_minutes() + step, step):
            moment = self.day_start + timedelta(minutes=minutes)
            x = self.x_for(moment)
            if x > width:
                break
            if x < LOGO_COLUMN:
                continue
            painter.setPen(QColor(theme.LINE))
            painter.drawLine(QPointF(x, HEADER - 8), QPointF(x, HEADER))
            painter.setPen(QColor(theme.TEXT_SOFT if minutes % 60 == 0 else theme.TEXT_MUTED))
            painter.drawText(
                QRectF(x + 6, 0, 80, HEADER), Qt.AlignVCenter, moment.astimezone().strftime("%H:%M")
            )

    def _paint_channel(self, painter, font, row):
        channel, _ = self.rows[row]
        y = self.row_y(row)
        box = QRectF(12, y + 8, LOGO_COLUMN - 24, ROW - 16)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        painter.fillPath(path, QColor(tile_color(channel.name)))
        logo = self.logos.prepared_logo(channel.logo) if self.logos and channel.logo else None
        if logo is not None:
            size = (
                logo.deviceIndependentSize()
                .toSize()
                .scaled(box.adjusted(10, 6, -10, -6).size().toSize(), Qt.KeepAspectRatio)
            )
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(box.center())
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(target, logo, QRectF(logo.rect()))
        else:
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                box.adjusted(8, 0, -8, 0),
                Qt.AlignCenter,
                painter.fontMetrics().elidedText(
                    channel.name, Qt.ElideRight, int(box.width()) - 16
                ),
            )

    def _paint_programme(self, painter, font, row, channel, programme, now):
        rect = self.programme_rect(row, programme)
        if rect.width() < 2:
            return
        live = programme.start <= now < programme.end
        past = programme.end <= now
        hovered = self._hover == (row, id(programme))
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        fill = theme.ACCENT_TINT if live else (theme.RAISED if hovered else theme.SURFACE)
        painter.fillPath(path, QColor(fill))
        if id(programme) in self.matches:
            painter.setPen(QPen(QColor(theme.GOLD), 2))
            painter.drawPath(path)
        elif live:
            painter.setPen(QPen(QColor(theme.ACCENT), 1))
            painter.drawPath(path)
        if live:
            span = (programme.end - programme.start).total_seconds()
            done = (now - programme.start).total_seconds() / span if span > 0 else 0
            painter.fillRect(
                QRectF(rect.left() + 8, rect.bottom() - 6, (rect.width() - 16) * done, 3),
                QColor(theme.GOLD),
            )
        if rect.width() < 28:
            return
        text = rect.adjusted(12, 6, -12, -10)
        if reminded_key(channel, programme) in self.reminded:
            painter.setPen(QColor(theme.GOLD))
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(QRectF(text.right() - 14, text.top(), 14, 18), Qt.AlignRight, "●")
            text.setRight(text.right() - 16)
        font.setPointSize(10)
        font.setBold(live)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED if past else theme.TEXT))
        painter.drawText(
            QRectF(text.left(), text.top(), text.width(), 22),
            Qt.AlignLeft | Qt.AlignVCenter,
            painter.fontMetrics().elidedText(programme.title, Qt.ElideRight, int(text.width())),
        )
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        start, end = programme.start.astimezone(), programme.end.astimezone()
        painter.drawText(
            QRectF(text.left(), text.top() + 22, text.width(), 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            painter.fontMetrics().elidedText(
                f"{start:%H:%M} – {end:%H:%M}", Qt.ElideRight, int(text.width())
            ),
        )

    # --- interaction -------------------------------------------------------------
    def mouseMoveEvent(self, event):
        found = self.hit(event.position())
        hover = None
        if found and found[1] is not None:
            row = int((event.position().y() - HEADER + self.verticalScrollBar().value()) // ROW)
            hover = (row, id(found[1]))
        if hover != self._hover:
            self._hover = hover
            self.viewport().setCursor(Qt.PointingHandCursor if found else Qt.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.viewport().update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        found = self.hit(event.position()) if event.button() == Qt.LeftButton else None
        if found:
            channel, programme = found
            if programme is None:
                self.channel_clicked.emit(channel)
            else:
                self.programme_clicked.emit(channel, programme)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ShiftModifier:
            delta = event.angleDelta().y() or event.angleDelta().x()
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
            return
        super().wheelEvent(event)

    def sizeHint(self):
        return QSize(800, 600)


class ProgrammeCard(QFrame):
    """What a programme is, and what can be done with it: watch now or be reminded."""

    watch = Signal(object)
    remind = Signal(object, object)
    record = Signal(object, object)
    catchup = Signal(object, object)

    def __init__(
        self, channel, programme, *, can_remind=False, reminded=False, now=None, parent=None
    ):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("programmeCard")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.channel, self.programme = channel, programme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)
        now = now or datetime.now(timezone.utc)
        start, end = programme.start.astimezone(), programme.end.astimezone()
        minutes = round((programme.end - programme.start).total_seconds() / 60)
        state = (
            _("ŞİMDİ YAYINDA")
            if programme.start <= now < programme.end
            else (_("BİTTİ") if programme.end <= now else _("YAKINDA"))
        )
        layout.addWidget(text_label(f"{channel.name.upper()}  ·  {state}", "eyebrow"))
        title = text_label(programme.title, "title")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(
            text_label(
                _("{weekday} {day} {month} · {start} – {end}  ·  {minutes} dk").format(
                    weekday=weekday_name(start, short=False),
                    day=f"{start.day:02d}",
                    month=month_name(start),
                    start=start.strftime("%H:%M"),
                    end=end.strftime("%H:%M"),
                    minutes=minutes,
                ),
                "facts",
            )
        )
        if programme.description:
            description = text_label(programme.description, "muted")
            description.setWordWrap(True)
            layout.addWidget(description)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.watch_button = IconButton(_("Kanalı aç"), "play", label=True, size=16)
        self.watch_button.setObjectName("primary")
        self.watch_button.setMinimumHeight(38)
        self.watch_button.clicked.connect(self._watch)
        actions.addWidget(self.watch_button)
        self.remind_button = None
        if can_remind and programme.start > now:
            self.remind_button = IconButton(
                _("Hatırlatıcı kurulu") if reminded else _("Hatırlat"),
                "recent",
                label=True,
                size=16,
            )
            self.remind_button.setObjectName("glass")
            self.remind_button.setMinimumHeight(38)
            self.remind_button.setEnabled(not reminded)
            self.remind_button.clicked.connect(self._remind)
            actions.addWidget(self.remind_button)
        actions.addStretch()
        layout.addLayout(actions)
        extra = QHBoxLayout()
        self.record_button = None
        self.catchup_button = None
        if channel.kind == "live" and programme.end > now:
            self.record_button = IconButton(_("Kaydet"), "record", label=True, size=16)
            self.record_button.clicked.connect(self._record)
            extra.addWidget(self.record_button)
        if can_catchup(channel, programme, now):
            self.catchup_button = IconButton(_("İzle (geçmiş)"), "recent", label=True, size=16)
            self.catchup_button.clicked.connect(self._catchup)
            extra.addWidget(self.catchup_button)
        extra.addStretch()
        layout.addLayout(extra)
        self.setFixedWidth(420)

    def _record(self):
        self.record.emit(self.channel, self.programme)
        self.close()

    def _catchup(self):
        self.catchup.emit(self.channel, self.programme)
        self.close()

    def _watch(self):
        self.watch.emit(self.channel)
        self.close()

    def _remind(self):
        self.remind.emit(self.channel, self.programme)
        self.close()


class GuideView(QWidget):
    """The Rehber page: day choice, programme search and the timetable."""

    watch_channel = Signal(object)
    remind_programme = Signal(object, object)
    record_programme = Signal(object, object)
    catchup_programme = Signal(object, object)
    add_guide = Signal()
    show_reminders = Signal()

    DAYS_BACK = 1
    DAYS_AHEAD = 6

    def __init__(self, logos=None, parent=None):
        super().__init__(parent)
        self.can_remind = False
        self._rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 16, 0)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(text_label(_("Rehber"), "display"))
        self.count_label = text_label("", "count")
        self.count_label.setFixedHeight(22)
        header.addWidget(self.count_label, 0, Qt.AlignVCenter)
        header.addStretch()
        self.reminders_button = IconButton(_("Hatırlatıcılar"), "recent", label=True, size=16)
        self.reminders_button.setObjectName("ghost")
        self.reminders_button.clicked.connect(self.show_reminders)
        self.reminders_button.hide()
        header.addWidget(self.reminders_button)
        self.archive_date = QDateEdit(QDate.currentDate())
        self.archive_date.setDisplayFormat(_("dd.MM.yyyy"))
        self.archive_date.setCalendarPopup(True)
        self.archive_date.setAccessibleName(_("Geçmiş yayın tarihi"))
        self.archive_date.setToolTip(_("Geçmiş yayın tarihi"))
        self.archive_date.dateChanged.connect(lambda value: self.show_day(value.toPython()))
        self.archive_date.hide()
        header.addWidget(self.archive_date)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Program ara…"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(_("Program ara"))
        self.search.setMaximumWidth(300)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self.next_match)
        header.addWidget(self.search, 1)
        layout.addLayout(header)
        days = QHBoxLayout()
        days.setSpacing(6)
        self.day_buttons = {}
        today = datetime.now().date()
        for offset in range(-self.DAYS_BACK, self.DAYS_AHEAD + 1):
            day = today + timedelta(days=offset)
            label = _("Dün") if offset == -1 else day_label(day, today)
            button = QPushButton(label)
            button.setObjectName("chip")
            button.setCheckable(True)
            button.setFixedHeight(30)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, d=day: self.show_day(d))
            days.addWidget(button)
            self.day_buttons[day] = button
        days.addSpacing(8)
        self.now_button = QPushButton(_("Şimdi"))
        self.now_button.setObjectName("chipMore")
        self.now_button.setFixedHeight(30)
        self.now_button.setCursor(Qt.PointingHandCursor)
        self.now_button.clicked.connect(self.go_now)
        days.addWidget(self.now_button)
        days.addStretch()
        layout.addLayout(days)
        self.grid = GuideGrid(logos)
        self.grid.programme_clicked.connect(self.open_programme)
        self.grid.channel_clicked.connect(self.watch_channel)
        layout.addWidget(self.grid, 1)
        self.empty = QFrame()
        self.empty.setObjectName("panel")
        empty = QVBoxLayout(self.empty)
        empty.setContentsMargins(28, 28, 28, 28)
        empty.addWidget(text_label(_("Program rehberi yok"), "title"), 0, Qt.AlignHCenter)
        note = text_label(
            _("Bir XMLTV rehberi ekle; kanalların saat saat yayın akışı burada görünsün."), "muted"
        )
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignCenter)
        empty.addWidget(note)
        add = IconButton(_("Rehber ekle"), "plus", label=True, size=16)
        add.setObjectName("primary")
        add.setMinimumHeight(40)
        add.clicked.connect(self.add_guide)
        empty.addWidget(add, 0, Qt.AlignHCenter)
        layout.addWidget(self.empty)
        layout.addStretch()
        self.day = today
        self.day_buttons[today].setChecked(True)

    def set_rows(self, rows, reminded=()):
        """Channels with a guide, each paired with its source's GuideIndex."""
        self._rows = list(rows)
        self.grid.reminded = set(reminded)
        has_guide = bool(self._rows)
        self.grid.setVisible(has_guide)
        self.empty.setVisible(not has_guide)
        self.reminders_button.setVisible(self.can_remind)
        archive_days = max((c.tv_archive_duration for c, _ in rows if c.tv_archive), default=0)
        self.archive_date.blockSignals(True)
        today = QDate.currentDate()
        self.archive_date.setDateRange(
            today.addDays(-max(1, archive_days)), today.addDays(self.DAYS_AHEAD)
        )
        self.archive_date.setDate(QDate(self.day.year, self.day.month, self.day.day))
        self.archive_date.blockSignals(False)
        self.archive_date.setVisible(archive_days > 1)
        self._apply_search()

    def show_day(self, day):
        self.day = day
        self.archive_date.blockSignals(True)
        self.archive_date.setDate(QDate(day.year, day.month, day.day))
        self.archive_date.blockSignals(False)
        for other, button in self.day_buttons.items():
            button.setChecked(other == day)
        self.grid.set_day(day)
        self._apply_search()
        if day == datetime.now().date():
            self.grid.scroll_to(datetime.now(timezone.utc))
        else:
            self.grid.horizontalScrollBar().setValue(18 * 60 * PX_PER_MINUTE)  # evening

    def go_now(self):
        self.show_day(datetime.now().date())

    def _search_changed(self, *_):
        self._apply_search()
        self.next_match(first=True)

    def _apply_search(self):
        key = search_key(self.search.text().strip())
        if not key:
            self.grid.matches = set()
            self.grid.set_rows(self._rows)
            self.count_label.setText(
                _n("{count} kanal", "{count} kanallar", len(self._rows)).format(
                    count=format_number(len(self._rows), decimals=0, grouping=True)
                )
            )
            return
        start, end = self.grid.day_start, self.grid.day_end
        rows, matches = [], set()
        for channel, index in self._rows:
            hits = [
                p for p in index.between(channel.tvg_id, start, end) if key in index.title_key(p)
            ]
            if hits:
                rows.append((channel, index))
                matches.update(id(p) for p in hits)
        self.grid.matches = matches
        self.grid.set_rows(rows)
        self.count_label.setText(
            _n("{count} program", "{count} programlar", len(matches)).format(
                count=format_number(len(matches), decimals=0, grouping=True)
            )
        )

    def next_match(self, first=False):
        """Bring the next search hit (or the first one) into view."""
        if not self.grid.matches:
            return
        start = self.grid.moment_at(LOGO_COLUMN)
        best = None
        for row, (channel, index) in enumerate(self.grid.rows):
            for programme in index.between(channel.tvg_id, self.grid.day_start, self.grid.day_end):
                if id(programme) in self.grid.matches and (first or programme.start > start):
                    if best is None or programme.start < best[1].start:
                        best = (row, programme)
        if best is not None:
            self.grid.scroll_to_row(best[0])
            self.grid.scroll_to(best[1].start, lead_minutes=15)

    def open_programme(self, channel, programme):
        card = ProgrammeCard(
            channel,
            programme,
            can_remind=self.can_remind,
            reminded=reminded_key(channel, programme) in self.grid.reminded,
            parent=self,
        )
        card.watch.connect(self.watch_channel)
        card.remind.connect(self.remind_programme)
        card.record.connect(self.record_programme)
        card.catchup.connect(self.catchup_programme)
        card.move(self.cursor().pos())
        card.show()
        return card
