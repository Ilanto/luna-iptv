"""A TV-style banner over the video: number, channel, what is on and what comes next."""

from datetime import datetime, timezone

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from .dialogs import text_label
from .library import KIND_LABELS

SHOW_SECONDS = 4.5
MARGIN = 14
MAX_WIDTH = 760


class ChannelBanner(QFrame):
    """Shown for a few seconds when a channel starts, and with the fullscreen controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("channelBanner")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 14, 22, 14)
        row.setSpacing(18)
        self.number = text_label("", "bannerNumber")
        self.number.setAlignment(Qt.AlignCenter)
        self.number.setMinimumWidth(48)
        row.addWidget(self.number)
        self.divider = QFrame()
        self.divider.setObjectName("bannerDivider")
        self.divider.setFixedWidth(1)
        row.addWidget(self.divider)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.name = text_label("", "bannerName")
        self.title = text_label("", "bannerTitle")
        self.time = text_label("", "muted")
        for label in (self.name, self.title, self.time):
            label.setTextFormat(Qt.PlainText)
            text.addWidget(label)
        progress = QFrame()
        progress.setObjectName("heroTrack")
        progress.setFixedHeight(4)
        self.track = progress
        self.fill = QFrame(progress)
        self.fill.setObjectName("heroFill")
        text.addSpacing(4)
        text.addWidget(progress)
        row.addLayout(text, 1)
        self.following = text_label("", "muted")
        self.following.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.following.setMaximumWidth(200)
        row.addWidget(self.following, 0, Qt.AlignBottom)
        # No opacity effect: effects over the OpenGL video are costly and unreliable on Wayland,
        # and a TV banner may simply appear.
        self._fraction = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_banner)
        self.hide()

    def set_content(self, channel, number=None, programme=None, following=None):
        self.number.setText(str(number) if number else "")
        self.number.setVisible(bool(number))
        self.divider.setVisible(bool(number))
        self.name.setText(channel.name)
        self.setAccessibleName(f"{number} {channel.name}" if number else channel.name)
        if programme is not None:
            start, end = programme.start.astimezone(), programme.end.astimezone()
            self.title.setText(programme.title)
            self.time.setText(f"{start:%H:%M} – {end:%H:%M}")
            span = (programme.end - programme.start).total_seconds()
            done = (datetime.now(timezone.utc) - programme.start).total_seconds()
            self._fraction = min(1.0, max(0.0, done / span)) if span > 0 else 0.0
        else:
            kind = "Dizi bölümü" if channel.series_id else KIND_LABELS.get(channel.kind, "")
            self.title.setText(channel.group or kind)
            self.time.setText(kind if channel.group else "")
            self._fraction = 0.0
        self.title.setVisible(bool(self.title.text()))
        self.time.setVisible(bool(self.time.text()))
        self.track.setVisible(programme is not None)
        if following is not None:
            moment = following.start.astimezone()
            self.following.setText(f"Sonra · {moment:%H:%M}\n{following.title}")
        else:
            self.following.setText("")
        self.following.setVisible(following is not None)
        self._place_fill()

    def show_for(self, seconds=SHOW_SECONDS):
        """Show; hide again after ``seconds`` (None keeps it until hide_banner).

        The window decides when not to (the mini player); a width rule here would also hide
        it while the player panel is still sliding in.
        """
        self._timer.stop()
        self.show()
        self.raise_()
        if seconds is not None:
            self._timer.start(round(seconds * 1000))

    def hide_banner(self):
        self._timer.stop()
        self.hide()

    def place(self, area, bottom=None):
        """Lay out over ``area`` (a rect in the parent), its foot at ``bottom`` if given."""
        width = min(MAX_WIDTH, max(0, area.width() - 2 * MARGIN))
        height = self.sizeHint().height()
        foot = (bottom if bottom is not None else area.bottom() - MARGIN) - height
        self.setGeometry(area.left() + MARGIN, max(area.top(), foot), width, height)
        self._place_fill()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_fill()

    def _place_fill(self):
        self.fill.setGeometry(0, 0, round(self.track.width() * self._fraction), 4)


class BannerPlacer(QObject):
    """Keeps the banner over the video as the panel, fullscreen controls or mini player move."""

    def __init__(self, banner, video, controls, fullscreen_active, suppressed=lambda: False):
        super().__init__(banner)
        self.banner, self.video, self.controls = banner, video, controls
        self.fullscreen_active = fullscreen_active
        self.suppressed = suppressed  # the mini player: too small for a banner
        for widget in (video, controls):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide):
            self.place()
        return False

    def place(self):
        if self.suppressed():
            self.banner.hide_banner()
            return
        area = self.video.geometry()
        bottom = None
        if self.fullscreen_active() and not self.controls.isHidden():
            bottom = self.controls.geometry().top() - 10
        self.banner.place(area, bottom)
