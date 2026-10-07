import math
import unicodedata
from datetime import datetime, timezone

from PySide6.QtCore import QAbstractListModel, QEvent, QRectF, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from . import icons, theme


def search_key(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
        if not unicodedata.combining(c)
    )


PROGRESS_ROLE = Qt.UserRole + 2


def resumable(position, duration):
    """A saved position worth resuming: past the first seconds and not at the end."""
    return (
        math.isfinite(position)
        and math.isfinite(duration)
        and duration > 0
        and 5 < position < duration - 10
    )


class ChannelModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = []
        self.favorites = set()
        self.search_keys = []
        self.progress = {}
        self._rows = {}

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.channels)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.channels):
            return None
        channel = self.channels[index.row()]
        if role == Qt.DisplayRole:
            return channel.name
        if role == Qt.UserRole:
            return channel
        if role == Qt.UserRole + 1:
            return channel.id in self.favorites
        if role == PROGRESS_ROLE:
            if channel.kind == "live" or channel.id not in self.progress:
                return None
            position, duration = self.progress[channel.id]
            return position / duration if resumable(position, duration) else None
        if role == Qt.AccessibleTextRole:
            return channel.name + ", " + channel.group

    def reset(self, channels, favorites, progress=None):
        self.beginResetModel()
        self.channels = channels
        self.favorites = favorites
        self.progress = dict(progress or {})
        self._rows = {channel.id: row for row, channel in enumerate(channels)}
        self.search_keys = [search_key(c.name + " " + c.group) for c in channels]
        self.endResetModel()

    def replace_progress(self, progress):
        """Swap in freshly loaded positions (after history is reset) and repaint every card."""
        self.progress = dict(progress)
        if self.channels:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.channels) - 1, 0), [PROGRESS_ROLE]
            )

    def set_progress(self, channel_id, position, duration):
        """Keep a card's progress bar current while it plays, without a full reset."""
        self.progress[channel_id] = (float(position), float(duration))
        row = self._rows.get(channel_id)
        if row is not None:
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, [PROGRESS_ROLE])


class ChannelFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.section = "live"
        self.query = ""
        self._query_key = ""
        self.group = ""
        self.source = ""
        self.recent = {}

    def set_recent_ids(self, channel_ids):
        self.recent = {channel_id: rank for rank, channel_id in enumerate(channel_ids)}
        self.invalidate()

    def lessThan(self, left, right):
        if self.section == "recent":
            channels = self.sourceModel().channels
            return self.recent.get(channels[left.row()].id, len(self.recent)) < self.recent.get(
                channels[right.row()].id, len(self.recent)
            )
        return left.row() < right.row()

    def filterAcceptsRow(self, row, parent):
        channel = self.sourceModel().channels[row]
        if self.source and not channel.id.startswith(self.source + ":"):
            return False
        if self.section == "favorites":
            if channel.id not in self.sourceModel().favorites:
                return False
        elif self.section == "recent":
            if channel.id not in self.recent:
                return False
        elif channel.kind != self.section or (channel.series_id and channel.kind == "movie"):
            return False
        if self.group and channel.group != self.group:
            return False
        return not self._query_key or self._query_key in self.sourceModel().search_keys[row]

    def refresh(self):
        self._query_key = search_key(self.query)
        self.sort(0 if self.section == "recent" else -1)
        if hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateFilter()


KIND_LABELS = {"live": "Canlı yayın", "movie": "Film / video", "series": "Dizi"}
# Night-sky hues behind each logo; a channel name always maps to the same one.
TILE_HUES = ("#16245a", "#231c55", "#132f4a", "#2c1c48", "#173444", "#1f2452")
CARD_WIDTH = 228
CARD_HEIGHT = 158
CARD_GAP = 14
LOGO_HEIGHT = 92
# Movies and series: portrait poster cards (2:3 artwork above a caption).
POSTER_WIDTH = 164
POSTER_ART_HEIGHT = 246
POSTER_HEIGHT = POSTER_ART_HEIGHT + 62
POSTER_KINDS = ("movie", "series")


def cover_source(pixmap, target):
    """The part of ``pixmap`` that fills ``target`` without stretching: a centred crop."""
    source = QRectF(pixmap.rect())
    ratio = target.width() / target.height()
    if source.width() / source.height() > ratio:
        width = source.height() * ratio
        return QRectF(source.center().x() - width / 2, 0, width, source.height())
    height = source.width() / ratio
    return QRectF(0, source.center().y() - height / 2, source.width(), height)


def tile_color(name):
    return TILE_HUES[sum(map(ord, name)) % len(TILE_HUES)]


class CardGrid(QListView):
    """Wrapping grid of cards whose columns always fill the available width.

    Live channels use wide logo cards; movies and series use poster cards.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.poster_mode = False
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        self.setSelectionRectVisible(False)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        # A permanent thin scrollbar keeps the width stable while columns are fitted.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fit_columns()

    def card_size(self):
        if self.poster_mode:
            return QSize(POSTER_WIDTH, POSTER_HEIGHT)
        return QSize(CARD_WIDTH, CARD_HEIGHT)

    def set_poster_mode(self, enabled):
        if enabled != self.poster_mode:
            self.poster_mode = enabled
            self._fit_columns()

    def columns(self):
        return max(1, self.viewport().width() // (self.card_size().width() + CARD_GAP))

    def _fit_columns(self):
        # One pixel short of the full width: Qt wraps a cell that ends exactly
        # on the viewport edge onto the next row.
        width = max(1, (self.viewport().width() - 1) // self.columns())
        if self.poster_mode:
            # Posters keep 2:3 as columns widen, so the caption below always fits.
            height = round((width - CARD_GAP) * 1.5) + POSTER_HEIGHT - POSTER_ART_HEIGHT
        else:
            height = self.card_size().height()
        cell = QSize(width, height + CARD_GAP)
        if cell != self.gridSize():
            self.setGridSize(cell)

    def viewportEvent(self, event):
        # The viewport, not the view, knows the final width once scrollbars settle.
        if event.type() == QEvent.Resize:
            self._fit_columns()
        return super().viewportEvent(event)


class ChannelDelegate(QStyledItemDelegate):
    """Paints channels as wide logo cards and movies or series as poster cards."""

    def __init__(self, parent=None, *, logos=None, posters=None, now_for=None):
        super().__init__(parent)
        self.logos = logos
        self.posters = posters
        self.now_for = now_for or (lambda channel: None)

    def artwork(self, channel):
        cache = self.posters if channel.kind in POSTER_KINDS and self.posters else self.logos
        return cache.prepared_logo(channel.logo) if cache and channel.logo else None

    def sizeHint(self, option, index):
        view = self.parent()
        grid = view.gridSize() if isinstance(view, QListView) else QSize()
        return grid if grid.isValid() else QSize(CARD_WIDTH + CARD_GAP, CARD_HEIGHT + CARD_GAP)

    def paint(self, painter, option, index):
        channel = index.data(Qt.UserRole)
        if channel is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        half = CARD_GAP / 2
        card = QRectF(option.rect).adjusted(half, half, -half, -half)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        shape = QPainterPath()
        shape.addRoundedRect(card, 16, 16)
        painter.fillPath(shape, QColor(theme.RAISED if hovered and not selected else theme.SURFACE))
        view = self.parent()
        if getattr(view, "poster_mode", False):
            self._paint_poster(painter, option, index, channel, card, shape, hovered)
        else:
            self._paint_logo(painter, option, index, channel, card, shape, hovered or selected)
        self._paint_outline(painter, option, card, selected)
        painter.restore()

    def _stage(self, painter, channel, rect, shape, lit):
        base = QColor(tile_color(channel.name))
        glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
        glow.setColorAt(0.0, base.lighter(150 if lit else 130))
        glow.setColorAt(1.0, base)
        painter.fillRect(rect, glow)

    def _wordmark(self, painter, option, text, area, size, wrap=False):
        font = QFont(option.font)
        font.setPointSize(size)
        font.setWeight(QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        if wrap:
            painter.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, text)
        else:
            painter.drawText(
                area,
                Qt.AlignCenter,
                painter.fontMetrics().elidedText(text, Qt.ElideRight, int(area.width())),
            )

    def _star(self, painter, index, rect):
        if index.data(Qt.UserRole + 1):
            star = icons.pixmap("star-filled", theme.GOLD, 16, painter.device().devicePixelRatioF())
            painter.drawPixmap(
                QRectF(rect.right() - 28, rect.top() + 11, 16, 16), star, QRectF(star.rect())
            )

    def _watched(self, painter, index, art):
        """How much of a film or episode has been watched, along the artwork's foot."""
        fraction = index.data(PROGRESS_ROLE)
        if fraction is None:
            return
        inset = min(10.0, art.width() * 0.06)
        track = QRectF(art.left() + inset, art.bottom() - inset - 4, art.width() - inset * 2, 4)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(11, 16, 32, 190))
        painter.drawRoundedRect(track.adjusted(-1, -1, 1, 1), 2.5, 2.5)
        painter.setBrush(QColor(theme.GOLD))
        painter.drawRoundedRect(
            QRectF(track.left(), track.top(), track.width() * fraction, 4), 2, 2
        )
        painter.restore()

    def _caption(self, painter, option, card, top, heading, detail_text):
        left, right = card.left() + 14, card.right() - 14
        font = QFont(option.font)
        font.setPointSize(10)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        title = QRectF(left, top, right - left, 22)
        painter.drawText(
            title,
            Qt.AlignLeft | Qt.AlignVCenter,
            painter.fontMetrics().elidedText(heading, Qt.ElideRight, int(title.width())),
        )
        font.setPointSize(8)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        detail = QRectF(left, title.bottom(), right - left, 18)
        painter.drawText(
            detail,
            Qt.AlignLeft | Qt.AlignVCenter,
            painter.fontMetrics().elidedText(detail_text, Qt.ElideRight, int(detail.width())),
        )
        return left, right

    def _paint_poster(self, painter, option, index, channel, card, shape, hovered):
        art = QRectF(card.left(), card.top(), card.width(), card.width() * 1.5)
        painter.save()
        painter.setClipPath(shape)
        poster = self.artwork(channel)
        if poster is not None:
            # Cover the 2:3 frame; posters of other shapes are cropped, never stretched.
            painter.drawPixmap(art, poster, cover_source(poster, art))
            if hovered:
                painter.fillRect(art, QColor(255, 255, 255, 18))
        else:
            self._stage(painter, channel, art, shape, hovered)
            self._wordmark(painter, option, channel.name, art.adjusted(14, 14, -14, -14), 12, True)
        self._watched(painter, index, art)
        self._star(painter, index, art)
        painter.restore()
        self._caption(
            painter,
            option,
            card,
            art.bottom() + 8,
            channel.name,
            channel.group or KIND_LABELS.get(channel.kind, ""),
        )

    def _paint_logo(self, painter, option, index, channel, card, shape, lit):
        stage = QRectF(card.left(), card.top(), card.width(), LOGO_HEIGHT)
        painter.save()
        painter.setClipPath(shape)
        self._stage(painter, channel, stage, shape, lit)
        logo = self.artwork(channel)
        if logo is not None and channel.kind in POSTER_KINDS:
            # A poster in a wide card (favorites, history): artwork left, name right.
            height = stage.height() - 16
            poster = QRectF(stage.left() + 12, stage.top() + 8, height * 2 / 3, height)
            painter.drawPixmap(poster, logo, cover_source(logo, poster))
            self._watched(painter, index, poster)
            area = QRectF(
                poster.right() + 12,
                stage.top(),
                stage.right() - poster.right() - 24,
                stage.height(),
            )
            self._wordmark(painter, option, channel.name, area, 11, True)
        elif logo is not None:
            room = stage.adjusted(22, 14, -22, -14).size().toSize()
            size = logo.deviceIndependentSize().toSize().scaled(room, Qt.KeepAspectRatio)
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(stage.center())
            painter.drawPixmap(target, logo, QRectF(logo.rect()))
        else:
            self._wordmark(painter, option, channel.name, stage.adjusted(16, 0, -16, 0), 14)
        self._star(painter, index, stage)
        painter.restore()

        programme = self.now_for(channel)
        kind = KIND_LABELS.get(channel.kind, "")
        if programme:
            start, end = programme.start.astimezone(), programme.end.astimezone()
            heading, detail_text = (
                programme.title,
                f"{start:%H:%M} – {end:%H:%M}  ·  {channel.name}",
            )
        elif logo is not None and channel.kind not in POSTER_KINDS:
            heading, detail_text = channel.name, channel.group or kind
        else:
            # The stage already shows the name; do not repeat it.
            heading, detail_text = channel.group or kind, kind if channel.group else ""
        left, right = self._caption(painter, option, card, stage.bottom() + 9, heading, detail_text)
        if programme:
            span = (programme.end - programme.start).total_seconds()
            done = (datetime.now(timezone.utc) - programme.start).total_seconds()
            fraction = min(1.0, max(0.0, done / span)) if span > 0 else 0.0
            track = QRectF(left, card.bottom() - 13, right - left, 3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT_TINT))
            painter.drawRoundedRect(track, 1.5, 1.5)
            selected = bool(option.state & QStyle.State_Selected)
            painter.setBrush(QColor(theme.GOLD if selected else theme.ACCENT))
            painter.drawRoundedRect(
                QRectF(left, track.top(), track.width() * fraction, 3), 1.5, 1.5
            )

    def _paint_outline(self, painter, option, card, selected):
        painter.setBrush(Qt.NoBrush)
        if selected:
            painter.setPen(QPen(QColor(theme.GOLD), 2))
            painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), 15, 15)
        elif option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(theme.ACCENT), 1))
            painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        else:
            painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
            painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
