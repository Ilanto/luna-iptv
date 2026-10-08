import math
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from time import monotonic

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QPersistentModelIndex,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from . import icons, theme
from .card_motion import CardView
from .motion import animation_ms, motion_level, on_motion_changed


def search_key(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
        if not unicodedata.combining(c)
    )


PROGRESS_ROLE = Qt.UserRole + 2
LOCKED_ROLE = Qt.UserRole + 3


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
        self.locked = frozenset()  # channel ids behind the parental PIN
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
        if role == LOCKED_ROLE:
            return channel.id in self.locked
        if role == Qt.AccessibleTextRole:
            locked = ", kilitli" if channel.id in self.locked else ""
            return channel.name + ", " + channel.group + locked

    def reset(self, channels, favorites, progress=None):
        self.beginResetModel()
        self.channels = channels
        self.favorites = favorites
        self.progress = dict(progress or {})
        self._rows = {channel.id: row for row, channel in enumerate(channels)}
        self.search_keys = [search_key(c.name + " " + c.group) for c in channels]
        self.endResetModel()

    def set_locked(self, channel_ids):
        """Swap the set of locked channels and repaint every card."""
        self.locked = frozenset(channel_ids)
        if self.channels:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.channels) - 1, 0), [LOCKED_ROLE]
            )

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


def ordered_channels(channels, prefs):
    """Channels with each source's and section's chosen category order applied.

    Every (source, kind) keeps its slots in the list, so other sources and sections do not
    move; inside it, ordered categories come first in the chosen order (catalogue order
    within each), then channels of categories without a position, in catalogue order.
    One Python sort, so the views never sort row by row.
    """
    positions = {
        key: pref["position"] for key, pref in prefs.items() if pref["position"] is not None
    }
    if not positions:
        return channels
    buckets = {}
    ordered_kinds = {(source, kind) for source, kind, _ in positions}
    for row, channel in enumerate(channels):
        bucket = channel.id.split(":", 1)[0], channel.kind
        if bucket in ordered_kinds:
            buckets.setdefault(bucket, []).append(row)
    result = list(channels)
    for (source, kind), rows in buckets.items():

        def key(row, source=source, kind=kind):
            position = positions.get((source, kind, channels[row].group))
            return (position is None, position or 0, row)

        for slot, row in zip(rows, sorted(rows, key=key), strict=True):
            result[slot] = channels[row]
    return result


class ChannelFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.section = "live"
        self.query = ""
        self._query_key = ""
        self.group = ""
        self.source = ""
        # While a query is typed the live, film and series sections are all searched
        # (favorites and history stay scoped to their own list); kind narrows results.
        self.kind = ""
        self.recent = {}
        self.folder_ids = None
        self.hide_locked = False  # a kids profile never sees locked channels
        self.hidden_categories = {}
        self.category_positions = {}

    def set_category_prefs(self, prefs):
        self.hidden_categories = {kind: set() for kind in ("live", "movie", "series")}
        self.category_positions = {}
        for (source, kind, group), pref in prefs.items():
            if pref["hidden"]:
                self.hidden_categories.setdefault(kind, set()).add((source, group))
            if pref["position"] is not None:
                self.category_positions[source, kind, group] = pref["position"]
        self.invalidate()

    @staticmethod
    def category_key(channel):
        return channel.id.split(":", 1)[0], channel.kind, channel.group

    def category_hidden(self, channel):
        return (channel.id.split(":", 1)[0], channel.group) in self.hidden_categories.get(
            channel.kind, ()
        )

    def _hidden_in_section(self, channel):
        return self.section not in ("favorites", "recent") and self.category_hidden(channel)

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

    def _in_personal_list(self, channel):
        if self.section == "favorites":
            return channel.id in self.sourceModel().favorites and (
                self.folder_ids is None or channel.id in self.folder_ids
            )
        if self.section == "recent":
            return channel.id in self.recent
        return True

    @property
    def searching(self):
        return bool(self._query_key)

    def filterAcceptsRow(self, row, parent):
        channel = self.sourceModel().channels[row]
        if self._hidden_in_section(channel):
            return False
        if self.hide_locked and channel.id in self.sourceModel().locked:
            return False
        if self.source and not channel.id.startswith(self.source + ":"):
            return False
        if self._query_key:
            if not self._in_personal_list(channel):
                return False
            if (
                channel.series_id
                and channel.kind == "movie"
                and self.section
                not in (
                    "favorites",
                    "recent",
                )
            ):
                return False  # episodes are reached through their series
            if self.kind and channel.kind != self.kind:
                return False
            return self._query_key in self.sourceModel().search_keys[row]
        if self.section == "favorites":
            if not self._in_personal_list(channel):
                return False
        elif self.section == "recent":
            if channel.id not in self.recent:
                return False
        elif channel.kind != self.section or (channel.series_id and channel.kind == "movie"):
            return False
        if self.group and channel.group != self.group:
            return False
        return not self._query_key or self._query_key in self.sourceModel().search_keys[row]

    def search_counts(self):
        """How many search results each kind has, ignoring the kind filter."""
        counts = {"live": 0, "movie": 0, "series": 0}
        model = self.sourceModel()
        if not self._query_key:
            return counts
        for row, channel in enumerate(model.channels):
            personal = self.section in ("favorites", "recent")
            if (
                channel.kind in counts
                and not self._hidden_in_section(channel)
                and not (self.hide_locked and channel.id in model.locked)
                and (personal or not (channel.series_id and channel.kind == "movie"))
                and (not self.source or channel.id.startswith(self.source + ":"))
                and self._in_personal_list(channel)
                and self._query_key in model.search_keys[row]
            ):
                counts[channel.kind] += 1
        return counts

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


class CardGrid(CardView):
    """Wrapping grid of cards whose columns always fill the available width.

    Live channels use wide logo cards; movies and series use poster cards.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.poster_mode = False
        self.loading = False
        self._skeleton_timer = QTimer(self)
        self._skeleton_timer.setInterval(33)
        self._skeleton_timer.timeout.connect(self.viewport().update)
        on_motion_changed(self._sync_skeleton)
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

    def setModel(self, model):
        previous = self.model()
        if previous is not None:
            for signal in (previous.modelReset, previous.rowsInserted, previous.rowsRemoved):
                signal.disconnect(self._sync_skeleton)
        super().setModel(model)
        if model is not None:
            for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved):
                signal.connect(self._sync_skeleton)
        self._sync_skeleton()

    def set_loading(self, loading):
        self.loading = bool(loading)
        self._sync_skeleton()

    def _show_skeleton(self):
        return self.loading and (self.model() is None or self.model().rowCount() == 0)

    def _sync_skeleton(self, *_):
        if self._show_skeleton() and self.isVisible() and motion_level() == "full":
            self._skeleton_timer.start()
        else:
            self._skeleton_timer.stop()
        self.viewport().update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_skeleton()

    def hideEvent(self, event):
        self._skeleton_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._show_skeleton():
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        cell = self.gridSize()
        if cell.width() <= 0 or cell.height() <= 0:
            return
        phase = (monotonic() % 1.4) / 1.4 if motion_level() == "full" else 0
        half = CARD_GAP / 2
        for y in range(0, self.viewport().height(), cell.height()):
            for column in range(self.columns()):
                card = QRectF(column * cell.width(), y, cell.width(), cell.height())
                card.adjust(half, half, -half, -half)
                if not card.intersects(QRectF(event.rect())):
                    continue
                painter.setBrush(QColor(theme.SURFACE))
                painter.drawRoundedRect(card, 16, 16)
                art_height = card.width() * 1.5 if self.poster_mode else LOGO_HEIGHT
                shapes = QPainterPath()
                shapes.addRoundedRect(
                    card.adjusted(12, 12, -12, -(card.height() - art_height + 8)), 10, 10
                )
                shapes.addRoundedRect(
                    QRectF(
                        card.left() + 14,
                        card.top() + art_height + 10,
                        max(1, card.width() - 28),
                        12,
                    ),
                    4,
                    4,
                )
                shapes.addRoundedRect(
                    QRectF(
                        card.left() + 14,
                        card.top() + art_height + 32,
                        max(1, card.width() * 0.55),
                        9,
                    ),
                    4,
                    4,
                )
                painter.fillPath(shapes, QColor(theme.RAISED))
                if motion_level() == "full":
                    x = card.left() + (phase * 3 - 1) * card.width()
                    shimmer = QLinearGradient(
                        x, card.top(), x + card.width(), card.top() + card.width() * 0.5
                    )
                    light = QColor(theme.ACCENT)
                    light.setAlpha(28)
                    shimmer.setColorAt(0, QColor(0, 0, 0, 0))
                    shimmer.setColorAt(0.5, light)
                    shimmer.setColorAt(1, QColor(0, 0, 0, 0))
                    painter.fillPath(shapes, shimmer)

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
        self._pending_artwork = OrderedDict()
        self._arrived_at = OrderedDict()
        self._fading = {}
        self._motion = getattr(parent, "card_motion", None)
        for cache in (logos, posters):
            if cache is not None and hasattr(cache, "ready"):
                cache.ready.connect(self._artwork_ready)

    def clear_fades(self):
        self._pending_artwork.clear()
        old, self._fading = self._fading, {}
        for index in old:
            self._motion.repaint(index)

    def _remember_artwork(self, index, channel, image, locked):
        if self._motion is None or locked or not channel.logo or motion_level() != "full":
            return
        if image is None:
            self._pending_artwork[QPersistentModelIndex(index)] = channel
            while len(self._pending_artwork) > 256:
                self._pending_artwork.popitem(last=False)
        elif self._pending_artwork:
            self._pending_artwork.pop(QPersistentModelIndex(index), None)

    def _artwork_ready(self, url):
        if self._motion is None:
            return
        view = self.parent()
        for index, channel in tuple(self._pending_artwork.items()):
            if channel.logo != url:
                continue
            if not index.isValid() or not view.isVisible():
                del self._pending_artwork[index]
                continue
            if not view.visualRect(index).intersects(view.viewport().rect()):
                del self._pending_artwork[index]
                continue
            if self.artwork(channel, bool(index.data(LOCKED_ROLE))) is None:
                # The other cache may emit the same URL before this card's artwork arrives.
                continue
            del self._pending_artwork[index]
            if animation_ms(220, 0):
                self._arrived_at[url] = self._motion.clock()
                self._arrived_at.move_to_end(url)
                while len(self._arrived_at) > 256:
                    self._arrived_at.popitem(last=False)
                self._fading[index] = url
                while len(self._fading) > 256:
                    self._motion.repaint(next(iter(self._fading)))
                    del self._fading[next(iter(self._fading))]
                self._motion.start()
            self._motion.repaint(index)

    def artwork_opacity(self, index, url):
        duration = animation_ms(220, 0)
        if (
            not duration
            or not self._fading
            or self._fading.get(QPersistentModelIndex(index)) != url
        ):
            return 1.0
        at = self._arrived_at.get(url)
        if at is None:
            return 1.0
        return min(1.0, max(0.0, (self._motion.clock() - at) * 1000 / duration))

    def advance_fades(self):
        view = self.parent()
        for index, url in tuple(self._fading.items()):
            self._motion.repaint(index)
            if (
                not index.isValid()
                or self.artwork_opacity(index, url) >= 1
                or not view.visualRect(index).intersects(view.viewport().rect())
            ):
                del self._fading[index]
        return bool(self._fading)

    def artwork(self, channel, locked=False):
        if locked:
            return None  # no poster or logo for content behind the PIN
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
        lift = 0.0
        if motion_level() == "full":
            lift = 1.0 if selected else self._motion.progress(index) if self._motion else 0.0
        if lift:
            # The cell clips the glow; even unusually large cards stay inside their gap.
            painter.setClipRect(option.rect, Qt.IntersectClip)
            scale = 1 + min(0.03, (CARD_GAP - 2) / max(card.width(), card.height())) * lift
            centre = card.center()
            painter.translate(centre)
            painter.scale(scale, scale)
            painter.translate(-centre)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, round(60 * lift)))
            painter.drawRoundedRect(card.translated(0, 2), 16, 16)
            painter.setBrush(Qt.NoBrush)
            for spread, alpha in ((3, 10), (2, 18), (1, 28)):
                glow = QColor(theme.ACCENT)
                glow.setAlpha(round(alpha * lift))
                painter.setPen(QPen(glow, 1))
                painter.drawRoundedRect(
                    card.adjusted(-spread, -spread, spread, spread), 16 + spread, 16 + spread
                )
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

    def _lock(self, painter, locked, rect):
        """A gold padlock in the top-left corner of locked artwork."""
        if not locked:
            return
        badge = QRectF(rect.left() + 10, rect.top() + 10, 26, 26)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(11, 16, 32, 200))
        painter.drawEllipse(badge)
        ratio = painter.device().devicePixelRatioF()
        lock = icons.pixmap("lock", theme.GOLD, 15, ratio)
        painter.drawPixmap(badge.adjusted(5.5, 5.5, -5.5, -5.5), lock, QRectF(lock.rect()))
        painter.restore()

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
        locked = bool(index.data(LOCKED_ROLE))
        poster = self.artwork(channel, locked)
        self._remember_artwork(index, channel, poster, locked)
        opacity = self.artwork_opacity(index, channel.logo)
        if poster is None or opacity < 1:
            self._stage(painter, channel, art, shape, hovered)
            self._wordmark(painter, option, channel.name, art.adjusted(14, 14, -14, -14), 12, True)
        if poster is not None:
            painter.save()
            painter.setOpacity(opacity)
            # Cover the 2:3 frame; posters of other shapes are cropped, never stretched.
            painter.drawPixmap(art, poster, cover_source(poster, art))
            if hovered:
                painter.fillRect(art, QColor(255, 255, 255, 18))
            painter.restore()
        self._watched(painter, index, art)
        self._star(painter, index, art)
        self._lock(painter, locked, art)
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
        locked = bool(index.data(LOCKED_ROLE))
        logo = self.artwork(channel, locked)
        self._remember_artwork(index, channel, logo, locked)
        opacity = self.artwork_opacity(index, channel.logo)
        if logo is not None and opacity < 1:
            painter.save()
            painter.setOpacity(1 - opacity)
            self._wordmark(painter, option, channel.name, stage.adjusted(16, 0, -16, 0), 14)
            painter.restore()
        painter.save()
        painter.setOpacity(opacity)
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
        painter.restore()
        self._star(painter, index, stage)
        self._lock(painter, locked, stage)
        painter.restore()

        programme = None if locked else self.now_for(channel)
        kind = KIND_LABELS.get(channel.kind, "")
        if programme:
            start, end = programme.start.astimezone(), programme.end.astimezone()
            heading, detail_text = (
                programme.title,
                f"{start:%H:%M} – {end:%H:%M}  ·  {channel.name}",
            )
        elif logo is not None and opacity >= 1 and channel.kind not in POSTER_KINDS:
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
