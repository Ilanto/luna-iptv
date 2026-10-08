"""The home page's showcase: what to continue, or a favourite on air, large and cinematic."""

from datetime import datetime, timezone

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .dialogs import text_label
from .library import KIND_LABELS, cover_source, resumable, tile_color
from .motion import IconButton

HERO_HEIGHT = 300
POSTER_RATIO = 2 / 3


def remaining_text(seconds):
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} sa {minutes} dk kaldı" if minutes else f"{hours} sa kaldı"
    return f"{minutes} dk kaldı"


class HeroArt(QWidget):
    """The poster (or a lettered tile) with rounded corners and a thin moonlit edge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = None
        self.name = ""
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_art(self, pixmap, name):
        self.pixmap, self.name = pixmap, name
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 14, 14)
        painter.setClipPath(shape)
        if self.pixmap is not None and not self.pixmap.isNull():
            painter.drawPixmap(rect, self.pixmap, cover_source(self.pixmap, rect))
        else:
            base = QColor(tile_color(self.name or "Luna"))
            glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
            glow.setColorAt(0, base.lighter(160))
            glow.setColorAt(1, base)
            painter.fillRect(rect, glow)
            font = QFont(self.font())
            font.setPixelSize(max(14, int(rect.width() * 0.11)))
            font.setWeight(QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                rect.adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, self.name
            )
        painter.setClipping(False)
        painter.setPen(QColor(255, 213, 138, 110))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(shape)


class HomeHero(QFrame):
    """A wide banner for one channel: blurred backdrop from its artwork, title, progress, play."""

    play = Signal(object)
    details = Signal(object)

    def __init__(self, posters=None, logos=None, now_for=None, parent=None):
        super().__init__(parent)
        self.setObjectName("homeHero")
        self.posters, self.logos = posters, logos
        self.now_for = now_for or (lambda channel: None)
        self.channel = None
        self.mode = ""
        self._backdrop = None
        self._backdrop_key = None
        self.setMinimumHeight(240)
        self.setMaximumHeight(HERO_HEIGHT + 40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(40, 28, 44, 28)
        row.setSpacing(36)
        self.art = HeroArt()
        row.addWidget(self.art, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(8)
        text.addStretch()
        self.eyebrow = text_label("", "eyebrow")
        self.title = text_label("", "heroTitle")
        self.title.setWordWrap(True)
        self.meta = text_label("", "muted")
        self.meta.setWordWrap(True)
        for label in (self.eyebrow, self.title, self.meta):
            text.addWidget(label)
        progress = QHBoxLayout()
        progress.setSpacing(14)
        self.bar = QFrame()
        self.bar.setObjectName("heroTrack")
        self.bar.setFixedHeight(6)
        self.fill = QFrame(self.bar)
        self.fill.setObjectName("heroFill")
        self.left = text_label("", "muted")
        progress.addWidget(self.bar, 1)
        progress.addWidget(self.left)
        text.addSpacing(6)
        text.addLayout(progress)
        actions = QHBoxLayout()
        actions.setSpacing(12)
        self.play_button = IconButton("Devam et", "play", label=True, size=16)
        self.play_button.setObjectName("primary")
        self.details_button = IconButton("Ayrıntılar", "info", label=True, size=16)
        self.details_button.setObjectName("glass")
        for button in (self.play_button, self.details_button):
            button.setMinimumHeight(44)
            button.setCursor(Qt.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch()
        text.addSpacing(10)
        text.addLayout(actions)
        text.addStretch()
        row.addLayout(text, 1)
        self.play_button.clicked.connect(lambda: self.channel and self.play.emit(self.channel))
        self.details_button.clicked.connect(
            lambda: self.channel and self.details.emit(self.channel)
        )
        for cache in (posters, logos):
            if cache is not None:
                cache.ready.connect(self._artwork_ready)
        self.hide()

    def sizeHint(self):
        return QSize(900, HERO_HEIGHT)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.height() - 56
        self.art.setFixedSize(int(height * POSTER_RATIO), height)
        self._place_fill()

    def show_resume(self, channel, position, duration):
        """A film or episode half watched: continue it."""
        self.mode = "resume"
        self.channel = channel
        self.eyebrow.setText("KALDIĞIN YERDEN DEVAM ET")
        self.title.setText(channel.name)
        kind = "Dizi bölümü" if channel.series_id else KIND_LABELS.get(channel.kind)
        self.meta.setText(" · ".join(filter(None, (channel.group, kind))))
        fraction = position / duration if resumable(position, duration) else 0.0
        self._progress(fraction, remaining_text(duration - position) if fraction else "")
        self.play_button.setText("Devam et")
        self.details_button.setText("Ayrıntılar")
        self.details_button.show()
        self._artwork_changed()
        self.show()

    def show_live(self, channel):
        """A favourite channel with something on: watch it now."""
        self.mode = "live"
        self.channel = channel
        programme = self.now_for(channel)
        self.eyebrow.setText("FAVORİN ŞU AN YAYINDA")
        if programme is not None:
            start, end = programme.start.astimezone(), programme.end.astimezone()
            self.title.setText(programme.title)
            self.meta.setText(f"{channel.name} · {start:%H:%M} – {end:%H:%M}")
            span = (programme.end - programme.start).total_seconds()
            done = (datetime.now(timezone.utc) - programme.start).total_seconds()
            fraction = min(1.0, max(0.0, done / span)) if span > 0 else 0.0
            left = (programme.end - datetime.now(timezone.utc)).total_seconds()
            self._progress(fraction, remaining_text(left) if left > 0 else "")
        else:
            self.title.setText(channel.name)
            self.meta.setText(channel.group or "Canlı yayın")
            self._progress(0.0, "")
        self.play_button.setText("İzle")
        self.details_button.hide()
        self._artwork_changed()
        self.show()

    def clear(self):
        self.channel = None
        self.mode = ""
        self.hide()

    def refresh_programme(self):
        if self.mode == "live" and self.channel is not None:
            self.show_live(self.channel)

    def _progress(self, fraction, text):
        self._fraction = fraction
        self.bar.setVisible(bool(fraction))
        self.left.setText(text)
        self.left.setVisible(bool(text))
        self._place_fill()

    def _place_fill(self):
        fraction = getattr(self, "_fraction", 0.0)
        self.fill.setGeometry(0, 0, round(self.bar.width() * fraction), self.bar.height())

    def _cache_for(self, channel):
        return self.posters if channel.kind in ("movie", "series") else self.logos

    def _artwork_changed(self):
        channel = self.channel
        pixmap = None
        cache = self._cache_for(channel) if channel else None
        if channel is not None and channel.logo and cache is not None:
            pixmap = cache.prepared_logo(channel.logo)
            if pixmap is None:
                cache.request_logo(channel.logo)
        poster = pixmap if channel is not None and channel.kind != "live" else None
        self.art.set_art(poster, channel.name if channel else "")
        self.art.setVisible(channel is not None and channel.kind != "live")
        key = (channel.id if channel else None, pixmap is not None)
        if key != self._backdrop_key:
            self._backdrop_key = key
            self._backdrop = self._blurred(pixmap) if pixmap is not None else None
            self.update()

    def _artwork_ready(self, url):
        if self.channel is not None and url == self.channel.logo:
            self._artwork_changed()

    @staticmethod
    def _blurred(pixmap):
        """A cheap blur: shrink to a few dozen pixels, then let smooth scaling spread it."""
        small = pixmap.scaled(36, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        return small.scaled(360, 540, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 22, 22)
        painter.setClipPath(shape)
        base = QColor(tile_color(self.channel.name if self.channel else "Luna"))
        painter.fillRect(rect, QColor(theme.DUSK))
        if self._backdrop is not None:
            painter.setOpacity(1.0)
            painter.drawPixmap(rect, self._backdrop, cover_source(self._backdrop, rect))
            painter.setOpacity(1.0)
        else:
            glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
            glow.setColorAt(0, base.lighter(150))
            glow.setColorAt(0.55, base)
            glow.setColorAt(1, QColor(theme.NIGHT))
            painter.fillRect(rect, glow)
        shade = QLinearGradient(rect.topLeft(), rect.topRight())
        night = QColor(theme.NIGHT)
        for stop, alpha in ((0.0, 30), (0.3, 70), (0.55, 165), (1.0, 225)):
            night.setAlpha(alpha)
            shade.setColorAt(stop, QColor(night))
        painter.fillRect(rect, shade)
        painter.setClipping(False)
        painter.setPen(QColor(theme.LINE))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(shape)
