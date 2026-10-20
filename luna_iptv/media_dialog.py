"""On-demand catalogue details; playback remains an explicit owner action."""

import math
import re

from PySide6.QtCore import (
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListView,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .dialogs import text_label
from .home_hero import HomeHero
from .i18n import N_, _, get_language
from .imdb import clean_title
from .library import cover_source, resumable, tile_color
from .media_details import MediaDetails
from .models import Channel
from .motion import IconButton
from .preferences import normalize_preferences
from .tmdb import merge, trailer_url

_LANGUAGE_PREFERENCES = (
    (N_("Türkçe"), "tr"),
    (N_("İngilizce"), "en"),
    (N_("Almanca"), "de"),
    (N_("Fransızca"), "fr"),
    (N_("İspanyolca"), "es"),
    (N_("Arapça"), "ar"),
    (N_("Rusça"), "ru"),
    (N_("Japonca"), "ja"),
    (N_("İtalyanca"), "it"),
    (N_("Portekizce"), "pt"),
)


POSTER_SIZE = QSize(220, 330)
EPISODE_DURATION_ROLE = Qt.UserRole + 1
EPISODE_PROGRESS_ROLE = Qt.UserRole + 2
EPISODE_WATCHED_ROLE = Qt.UserRole + 3
EPISODE_ROW_HEIGHT = 64


def _duration_label(value):
    """Localize normalized duration metadata only where it is displayed."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?) dk", value)
    return _("{minutes} dk").format(minutes=match[1]) if match else value


def _season_label(group):
    """Keep the source group as item data so episode selection stays stable."""
    match = re.fullmatch(r"Sezon\s+([0-9]+)", group)
    if match:
        return _("Sezon {number}").format(number=match[1])
    return group or _("Sezon belirtilmemiş")


class EpisodeDelegate(QStyledItemDelegate):
    """Paint compact episode rows without creating widgets per episode."""

    def sizeHint(self, option, index):
        return QSize(240, EPISODE_ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        background = (
            theme.ACCENT_TINT
            if selected
            else theme.RAISED
            if option.state & QStyle.State_MouseOver
            else theme.SURFACE
        )
        painter.fillRect(rect, QColor(background))
        if selected:
            painter.fillRect(QRectF(rect.x(), rect.y(), 3, rect.height()), QColor(theme.ACCENT))

        badge = QRectF(rect.x() + 14, rect.y() + 14, 36, 36)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.DUSK))
        painter.drawRoundedRect(badge, 9, 9)
        painter.setFont(option.font)
        painter.setPen(QColor(theme.ACCENT))
        # Channel metadata retains provider order, but has no episode-number field.
        painter.drawText(badge, Qt.AlignCenter, str(index.row() + 1))

        text_rect = rect.adjusted(64, 8, -14, -8)
        metrics = painter.fontMetrics()
        duration = index.data(EPISODE_DURATION_ROLE) or ""
        watched = bool(index.data(EPISODE_WATCHED_ROLE))
        title_rect = QRectF(text_rect)
        if duration or watched:
            title_rect.setHeight(24)
        painter.setPen(QColor(theme.TEXT))
        title = metrics.elidedText(
            index.data(Qt.DisplayRole) or "", Qt.ElideRight, max(0, int(title_rect.width()))
        )
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
        if duration or watched:
            meta = QRectF(text_rect.x(), rect.y() + 34, text_rect.width(), 22)
            if watched:
                painter.drawPixmap(
                    QPointF(meta.x(), meta.y() + 3),
                    icons.pixmap("check", theme.GOLD, 16, painter.device().devicePixelRatioF()),
                )
                painter.setPen(QColor(theme.GOLD))
                painter.drawText(meta.adjusted(22, 0, 0, 0), Qt.AlignVCenter, _("İzlendi"))
                meta.setLeft(meta.left() + 22 + metrics.horizontalAdvance(_("İzlendi")) + 16)
            painter.setPen(QColor(theme.TEXT_SOFT))
            painter.drawText(
                meta,
                Qt.AlignVCenter,
                metrics.elidedText(duration, Qt.ElideRight, max(0, int(meta.width()))),
            )
        progress = index.data(EPISODE_PROGRESS_ROLE)
        if progress is not None:
            painter.fillRect(
                QRectF(rect.x() + 3, rect.bottom() - 3, (rect.width() - 3) * progress, 3),
                QColor(theme.GOLD),
            )
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(theme.ACCENT), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(5.5, 2.5, -2.5, -5.5))
        painter.restore()


def rounded(pixmap, radius):
    """A copy of ``pixmap`` with transparent rounded corners."""
    result = QPixmap(pixmap.size())
    result.setDevicePixelRatio(pixmap.devicePixelRatio())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(QPointF(), pixmap.deviceIndependentSize()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result


class ChipLayout(QLayout):
    """Wrap genre pills to the available width, including on small cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0))

    def minimumSize(self):
        return QSize(0, max((item.sizeHint().height() for item in self._items), default=0))

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def _place(self, rect, apply=False):
        x, y, height = rect.x(), rect.y(), 0
        for item in self._items:
            size = item.sizeHint()
            size.setWidth(min(size.width(), rect.width()))
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, height = rect.x(), y + height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y()


class PlotLabel(QLabel):
    """Keep the complete accessible text while showing at most four lines."""

    overflow_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.expanded = False
        self.setTextFormat(Qt.PlainText)
        self.setObjectName("lead")
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)

    def setText(self, text):
        super().setText(text)
        self._measure()

    def set_expanded(self, expanded):
        self.expanded = expanded
        self._measure()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._measure()

    def _measure(self):
        metrics = self.fontMetrics()
        full = metrics.boundingRect(
            QRect(0, 0, max(1, self.width()), 100000), Qt.TextWordWrap, self.text()
        ).height()
        limit = metrics.lineSpacing() * 4
        self.setFixedHeight(full if self.expanded else min(full, limit))
        self.overflow_changed.emit(full > limit)


class PosterLabel(QLabel):
    """Fit cached artwork into a smaller frame without discarding its pixels."""

    name = "Luna"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 14, 14)
        painter.setClipPath(shape)
        pixmap = self.pixmap()
        if not pixmap.isNull():
            painter.drawPixmap(rect, pixmap, cover_source(pixmap, rect))
        else:
            base = QColor(tile_color(self.name))
            glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
            glow.setColorAt(0, base.lighter(160))
            glow.setColorAt(1, base)
            painter.fillRect(rect, glow)
            painter.setPen(QColor(theme.TEXT_SOFT))
            painter.drawText(rect, Qt.AlignCenter, self.text())
        painter.setClipping(False)
        painter.setPen(QColor(255, 213, 138, 110))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(shape)


class PosterBackdrop(QWidget):
    """A poster wash and night shade matching the home hero."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wash = None
        self.name = "Luna"
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    def set_poster(self, pixmap):
        self._wash = (
            HomeHero._blurred(pixmap) if pixmap is not None and not pixmap.isNull() else None
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        if self._wash is not None:
            painter.drawPixmap(rect, self._wash, cover_source(self._wash, rect))
        else:
            base = QColor(tile_color(self.name))
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


class MediaDetailDialog(QDialog):
    play_requested = Signal(object)
    favorite_requested = Signal(object)
    retry_requested = Signal()
    # title, year, kind ("movie" | "series"): find the page when no IMDb id is known.
    imdb_lookup_requested = Signal(str, str, str)
    selection_changed = Signal(object)
    series_favorite_requested = Signal(object)

    def __init__(self, channel, poster_cache, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.setWindowTitle(channel.name)
        self.resize(980, 700)
        self.setMinimumSize(540, 320)
        self._channel = channel
        self._poster_cache = poster_cache
        self._poster_url = ""
        self._backdrop_url = ""
        self._online_info = {}
        self._provider_details = None
        self._imdb_url = ""
        self._imdb_query = None
        self._episodes = []
        self._series = channel.kind == "series" or bool(channel.series_id)
        self._details = None
        self._progress_lookup = None
        self._selection_identity = None
        self._series_channel = channel if channel.kind == "series" else None
        self._series_imdb_url = ""
        self._series_imdb_query = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName(_("İçerik ayrıntıları"))
        body = QWidget()
        body.setObjectName("cardBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Hero: the poster, blurred and darkened, behind the poster itself.
        self.hero = PosterBackdrop()
        columns = QHBoxLayout(self.hero)
        columns.setContentsMargins(28, 20, 28, 20)
        columns.setSpacing(24)
        self.poster_label = PosterLabel(_("Afiş yok"))
        self.poster_label.setTextFormat(Qt.PlainText)
        self.poster_label.setFixedSize(160, 240)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setAccessibleName(_("İçerik afişi"))
        artwork = QVBoxLayout()
        artwork.setSpacing(8)
        artwork.addWidget(self.poster_label)
        self.poster_scope_label = text_label("", "faint")
        self.poster_scope_label.setWordWrap(True)
        self.poster_scope_label.setMaximumWidth(POSTER_SIZE.width())
        self.poster_scope_label.hide()
        artwork.addWidget(self.poster_scope_label)
        artwork.addStretch()
        columns.addLayout(artwork)

        content = QVBoxLayout()
        content.setSpacing(6)
        content.setAlignment(Qt.AlignVCenter)
        self.kind_label = text_label(_("DİZİ") if self._series else _("FİLM"), "eyebrow")
        content.addWidget(self.kind_label)
        self.title_label = text_label(channel.name, "heroTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        content.addWidget(self.title_label)
        self.facts_label = text_label("", "facts")
        self.facts_label.setWordWrap(True)
        self.facts_label.setAccessibleName(_("Künye"))
        content.addWidget(self.facts_label)
        self.genre_chips = QWidget()
        self.genre_chips.setAccessibleName(_("Türler"))
        self.genre_layout = ChipLayout(self.genre_chips)
        content.addWidget(self.genre_chips)
        self.description_label = PlotLabel()
        self.description_label.setStyleSheet("font-size: 13px;")
        self.description_label.setWordWrap(True)
        self.description_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.description_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(self.description_label)
        self.plot_toggle = IconButton(_("Devamı"), "plus", label=True, size=12)
        self.plot_toggle.setObjectName("ghost")
        self.plot_toggle.setFixedHeight(26)
        self.plot_toggle.setAccessibleName(_("Konunun devamını göster"))
        self.plot_toggle.setCheckable(True)
        self.plot_toggle.toggled.connect(self._toggle_plot)
        self.description_label.overflow_changed.connect(self.plot_toggle.setVisible)
        content.addWidget(self.plot_toggle, 0, Qt.AlignLeft)
        actions = self.hero_actions = QHBoxLayout()
        actions.setSpacing(10)
        self.play_button = IconButton(_("Oynat"), "play", label=True, size=18)
        self.play_button.setObjectName("primary")
        self.play_button.setMinimumSize(132, 44)
        self.play_button.clicked.connect(self._request_play)
        self.favorite_button = IconButton("", "star", label=True, size=18)
        self.favorite_button.setObjectName("glass")
        self.favorite_button.setMinimumHeight(44)
        self.favorite_button.clicked.connect(self._request_favorite)
        self.set_favorite(False)
        self.imdb_button = IconButton(_("IMDb'de aç"), "external", label=True, size=16)
        self.imdb_button.setObjectName("glass")
        self.imdb_button.setMinimumHeight(44)
        self.imdb_button.setAccessibleName(_("IMDb sayfasını tarayıcıda aç"))
        self.imdb_button.clicked.connect(self._open_imdb)
        self.imdb_button.hide()
        actions.addWidget(self.play_button)
        actions.addWidget(self.favorite_button)
        actions.addWidget(self.imdb_button)
        self.trailer_button = IconButton(_("Fragman"), "external", label=True, size=16)
        self.trailer_button.setObjectName("glass")
        self.trailer_button.setAutoDefault(False)
        self.trailer_button.clicked.connect(self._open_trailer)
        self.trailer_button.hide()
        actions.addWidget(self.trailer_button)
        actions.addStretch()
        content.addLayout(actions)
        columns.addLayout(content, 1)
        body_layout.addWidget(self.hero)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(28, 16, 28, 12)
        details_layout.setSpacing(12)

        self.selectors = QFrame()
        self.selectors.setObjectName("panel")
        selector_layout = QVBoxLayout(self.selectors)
        selector_layout.setContentsMargins(16, 12, 16, 12)
        selector_layout.setSpacing(10)
        self.season_combo = QComboBox(self.selectors)
        self.season_combo.setAccessibleName(_("Sezon"))
        self.episode_combo = QComboBox(self.selectors)
        self.episode_combo.setAccessibleName(_("Bölüm"))
        self.season_combo.hide()
        self.episode_combo.hide()
        self.season_tabs = QTabBar()
        self.season_tabs.setObjectName("seasonTabs")
        self.season_tabs.setAccessibleName(_("Sezon"))
        self.season_tabs.setExpanding(False)
        self.season_tabs.setUsesScrollButtons(True)
        self.season_tabs.setElideMode(Qt.ElideNone)
        self.season_tabs.setDrawBase(False)
        self.season_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.season_tabs.setFocusPolicy(Qt.StrongFocus)
        selector_layout.addWidget(self.season_tabs)
        self.episode_list = QListView()
        self.episode_list.setObjectName("episodeList")
        self.episode_list.setAccessibleName(_("Bölümler"))
        self.episode_list.setModel(self.episode_combo.model())
        self.episode_list.setItemDelegate(EpisodeDelegate(self.episode_list))
        self.episode_list.setUniformItemSizes(True)
        self.episode_list.setEditTriggers(QListView.NoEditTriggers)
        self.episode_list.setSelectionMode(QListView.SingleSelection)
        self.episode_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.episode_list.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.episode_list.setMouseTracking(True)
        self.episode_list.setMinimumHeight(EPISODE_ROW_HEIGHT)
        self.episode_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        selector_layout.addWidget(self.episode_list)
        self.season_tabs.currentChanged.connect(self.season_combo.setCurrentIndex)
        self.episode_list.selectionModel().currentChanged.connect(self._select_episode)
        self.episode_list.doubleClicked.connect(self._play_episode)
        season_model = self.season_combo.model()
        for signal in (
            season_model.rowsInserted,
            season_model.rowsRemoved,
            season_model.modelReset,
            season_model.dataChanged,
        ):
            signal.connect(self._sync_seasons)
        self.season_combo.currentIndexChanged.connect(self._populate_episodes)
        self.episode_combo.currentIndexChanged.connect(self._refresh_selection)
        self.selectors.setVisible(self._series)
        self.season_combo.setEnabled(False)
        self.episode_combo.setEnabled(False)

        status_row = QHBoxLayout()
        self.status_label = text_label("", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName(_("Ayrıntı durumu"))
        self.status_label.hide()
        self.retry_button = IconButton(_("Yeniden dene"), "retry", label=True, size=16)
        self.retry_button.setObjectName("ghost")
        self.retry_button.clicked.connect(self.retry_requested)
        self.retry_button.hide()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.retry_button)
        details_layout.addLayout(status_row)
        self.online_status_label = text_label("", "muted")
        self.online_status_label.setWordWrap(True)
        self.online_status_label.hide()
        details_layout.addWidget(self.online_status_label)
        self.tmdb_label = text_label("", "faint")
        self.tmdb_label.setWordWrap(True)
        self.tmdb_label.hide()
        details_layout.addWidget(self.tmdb_label)

        about = self.about = QFrame()
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(0, 0, 0, 4)
        about_layout.setSpacing(8)
        about_layout.addWidget(text_label(_("Künye"), "title"))
        self.metadata = QFormLayout()
        self.metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.metadata.setHorizontalSpacing(18)
        self.metadata.setVerticalSpacing(4)
        about_layout.addLayout(self.metadata)
        details_layout.addWidget(about)
        details_layout.addWidget(self.selectors, 1 if self._series else 0)

        self.series_section = QFrame()
        self.series_section.setObjectName("panel")
        series_layout = QVBoxLayout(self.series_section)
        series_layout.setContentsMargins(18, 8, 18, 8)
        series_layout.setSpacing(10)
        self.series_info_toggle = IconButton(_("Dizi bilgileri"), "info", label=True, size=15)
        self.series_info_toggle.setObjectName("ghost")
        self.series_info_toggle.setFixedHeight(28)
        self.series_info_toggle.setCheckable(True)
        series_layout.addWidget(self.series_info_toggle, 0, Qt.AlignLeft)
        self.series_info_body = QWidget()
        series_body_layout = QVBoxLayout(self.series_info_body)
        series_body_layout.setContentsMargins(0, 0, 0, 0)
        series_body_layout.setSpacing(8)
        series_layout.addWidget(self.series_info_body)
        self.series_info_body.hide()
        self.series_info_toggle.toggled.connect(self.series_info_body.setVisible)
        self.series_title_label = text_label("", "title")
        self.series_description_label = text_label(_("Açıklama bulunmuyor."))
        for label in (self.series_title_label, self.series_description_label):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            series_body_layout.addWidget(label)
        self.series_metadata = QFormLayout()
        self.series_metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.series_metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.series_metadata.setHorizontalSpacing(18)
        self.series_metadata.setVerticalSpacing(8)
        series_body_layout.addLayout(self.series_metadata)
        series_actions = QHBoxLayout()
        series_actions.setSpacing(8)
        self.series_imdb_button = IconButton(
            _("Diziyi IMDb'de aç"), "external", label=True, size=15
        )
        self.series_imdb_button.setObjectName("ghost")
        self.series_imdb_button.clicked.connect(self._open_series_imdb)
        self.series_imdb_button.hide()
        self.series_favorite_button = IconButton("", "star", label=True, size=15)
        self.series_favorite_button.setObjectName("ghost")
        self.series_favorite_button.clicked.connect(self._request_series_favorite)
        self.set_series_favorite(False)
        series_actions.addWidget(self.series_imdb_button)
        series_actions.addWidget(self.series_favorite_button)
        series_actions.addStretch()
        series_body_layout.addLayout(series_actions)
        self.series_section.setVisible(self._series)
        details_layout.addWidget(self.series_section)

        preferences = self.preferences_panel = QFrame()
        preferences.setObjectName("panel")
        preference_layout = QVBoxLayout(preferences)
        preference_layout.setContentsMargins(18, 10, 18, 10)
        preference_layout.setSpacing(10)
        self.preferences_toggle = IconButton(
            _("Oynatma tercihleri"), "sliders", label=True, size=15
        )
        self.preferences_toggle.setObjectName("ghost")
        self.preferences_toggle.setFixedHeight(28)
        self.preferences_toggle.setCheckable(True)
        self.preferences_toggle.setChecked(True)
        preference_layout.addWidget(self.preferences_toggle, 0, Qt.AlignLeft)
        self.preferences_body = QWidget()
        preference_body_layout = QVBoxLayout(self.preferences_body)
        preference_body_layout.setContentsMargins(0, 0, 0, 0)
        preference_body_layout.setSpacing(10)
        preference_layout.addWidget(self.preferences_body)
        self.preferences_toggle.toggled.connect(self.preferences_body.setVisible)
        preference_row = QHBoxLayout()
        preference_row.setSpacing(10)
        self.audio_combo = QComboBox()
        self.subtitle_combo = QComboBox()
        for label_text, combo in (
            (_("Ses dili"), self.audio_combo),
            (_("Altyazı dili"), self.subtitle_combo),
        ):
            label = text_label(label_text, "muted")
            label.setBuddy(combo)
            combo.setAccessibleName(label_text)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            preference_row.addWidget(label)
            preference_row.addWidget(combo, 1)
        preference_body_layout.addLayout(preference_row)
        self.remember_checkbox = QCheckBox(_("Bu kaynak için hatırla"))
        preference_body_layout.addWidget(self.remember_checkbox)
        self.preference_note = text_label(
            _(
                "Seçtiğin diller yayında mevcutsa kullanılır. Tercihler Oynat ve varsa devam seçimi onaylandığında uygulanır."
            ),
            "faint",
        )
        self.preference_note.setWordWrap(True)
        self.preference_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        preference_body_layout.addWidget(self.preference_note)
        details_layout.addWidget(preferences)
        if not self._series:
            details_layout.addStretch()
        body_layout.addWidget(details, 1)
        self.set_playback_preferences({})
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setObjectName("cardFooter")
        footer_layout = self.footer_actions = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 14)
        footer_layout.setSpacing(10)
        footer_layout.addStretch()
        self.close_button = IconButton(_("Kapat"), "close", label=True, size=15)
        self.close_button.setObjectName("ghost")
        self.close_button.clicked.connect(self.close)
        footer_layout.addWidget(self.close_button)
        layout.addWidget(footer)
        # Enter must not unexpectedly start playback while browsing metadata.
        for button in (
            self.series_info_toggle,
            self.preferences_toggle,
            self.plot_toggle,
            self.play_button,
            self.favorite_button,
            self.close_button,
            self.retry_button,
            self.imdb_button,
            self.series_imdb_button,
            self.series_favorite_button,
        ):
            button.setAutoDefault(False)
        self.setTabOrder(self.scroll, self.plot_toggle)
        self.setTabOrder(self.plot_toggle, self.play_button)
        self.setTabOrder(self.play_button, self.favorite_button)
        self.setTabOrder(self.favorite_button, self.imdb_button)
        self.setTabOrder(self.imdb_button, self.season_tabs)
        self.setTabOrder(self.season_tabs, self.episode_list)
        self.setTabOrder(self.episode_list, self.series_info_toggle)
        self.setTabOrder(self.series_info_toggle, self.series_imdb_button)
        self.setTabOrder(self.series_imdb_button, self.series_favorite_button)
        self.setTabOrder(self.series_favorite_button, self.preferences_toggle)
        self.setTabOrder(self.preferences_toggle, self.audio_combo)
        self.setTabOrder(self.audio_combo, self.subtitle_combo)
        self.setTabOrder(self.subtitle_combo, self.remember_checkbox)
        self.setTabOrder(self.remember_checkbox, self.retry_button)
        self.setTabOrder(self.retry_button, self.close_button)
        self.close_button.setFocus()
        # A QObject-bound slot gives Qt a receiver context: destroying the card
        # automatically disconnects it even while the shared cache is working.
        poster_cache.ready.connect(self._poster_ready)
        self.finished.connect(self._release_poster)
        self.set_series_channel(self._series_channel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 800
        target = self.footer_actions if compact else self.hero_actions
        if target.indexOf(self.play_button) < 0:
            for index, button in enumerate((self.play_button, self.favorite_button)):
                target.insertWidget(index, button)
                button.show()
        self.poster_label.setFixedSize(QSize(112, 168) if compact else QSize(160, 240))
        self.poster_scope_label.setMaximumWidth(self.poster_label.width())

    def _toggle_plot(self, expanded):
        self.description_label.set_expanded(expanded)
        self.plot_toggle.setText(_("Daha az") if expanded else _("Devamı"))
        self.plot_toggle.set_icon_name("close" if expanded else "plus")
        self.plot_toggle.setAccessibleName(
            _("Konuyu kısalt") if expanded else _("Konunun devamını göster")
        )

    def _set_genres(self, info):
        while self.genre_layout.count():
            chip = self.genre_layout.takeAt(0).widget()
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
        genres = list(
            dict.fromkeys(
                part.strip() for part in re.split(r"[,/;|]", info.get("genre", "")) if part.strip()
            )
        )
        for genre in genres:
            chip = text_label(genre)
            chip.setStyleSheet(
                f"background: {theme.ACCENT_TINT}; color: {theme.ACCENT_STRONG}; "
                "border-radius: 10px; padding: 3px 10px; font-size: 11px;"
            )
            chip.setToolTip(genre)
            self.genre_layout.addWidget(chip)
        self.genre_chips.setVisible(bool(genres))
        return genres

    def _refresh_play_label(self):
        channel = self.selected_channel()
        saved = self._progress_lookup(channel.id) if channel and self._progress_lookup else None
        label = _("Oynat")
        if not self._series and saved and resumable(*saved):
            label = _("Devam et · {minutes} dk kaldı").format(
                minutes=max(1, math.ceil((saved[1] - saved[0]) / 60))
            )
        self.play_button.setText(label)

    def set_playback_preferences(self, preferences):
        """Seed this card without changing playback or saving preferences."""
        normalized = normalize_preferences(preferences)
        for mode, combo in (("audio", self.audio_combo), ("sub", self.subtitle_combo)):
            combo.clear()
            combo.addItem(_("Otomatik"), {"mode": "auto"})
            if mode == "sub":
                combo.addItem(_("Kapalı"), {"mode": "off"})
            for title, language in _LANGUAGE_PREFERENCES:
                choice = normalize_preferences({mode: {"mode": "track", "lang": language}})
                combo.addItem(_(title), choice[mode])
            selected = normalized.get(mode, {"mode": "auto"})
            index = next(
                (i for i in range(combo.count()) if combo.itemData(i) == selected),
                -1,
            )
            if index < 0:
                description = (
                    _("Kapalı")
                    if selected.get("mode") == "off"
                    else " / ".join(
                        value for value in (selected.get("lang"), selected.get("title")) if value
                    )
                )
                combo.addItem(
                    _("Kayıtlı tercih: {description}").format(description=description), selected
                )
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        self.remember_checkbox.setChecked(normalized.get("remember", True))

    def playback_preferences(self):
        """Return a detached snapshot for the owner's explicit play action."""
        return normalize_preferences(
            {
                "audio": self.audio_combo.currentData(),
                "sub": self.subtitle_combo.currentData(),
                "remember": self.remember_checkbox.isChecked(),
            }
        )

    @staticmethod
    def _fill_metadata(layout, info, *, show_missing=False):
        while layout.rowCount():
            layout.removeRow(0)
        fields = [
            ("year", _("Yıl")),
            ("genre", _("Tür")),
            ("duration", _("Süre")),
            ("director", _("Yönetmen")),
            ("cast", _("Oyuncular")),
            ("country", _("Ülke")),
            ("language", _("Dil")),
            (
                "rating",
                _("IMDb puanı")
                if info.get("rating_source", "").casefold() == "imdb"
                else _("Puan"),
            ),
        ]
        for key, title in fields:
            value = info.get(key)
            if key == "duration" and value:
                value = _duration_label(value)
            if value or show_missing:
                label = text_label(value or _("Belirtilmemiş"))
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                label.setAccessibleName(title)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                caption = text_label(title, "muted")
                layout.addRow(caption, label)
                # Essentials already live in the hero; retain the form's public rows.
                if key in {"year", "genre", "duration", "rating"} or not value:
                    caption.hide()
                    label.hide()

    @staticmethod
    def _facts(info):
        """One line of the essentials: year · duration · ★ rating."""
        parts = [info.get("year", ""), _duration_label(info.get("duration", ""))]
        if info.get("rating"):
            parts.append(f"★ {info['rating']}")
        return "   ·   ".join(part for part in parts if part)

    @staticmethod
    def _imdb_link(info):
        imdb_id = info.get("imdb_id", "")
        return (
            f"https://www.imdb.com/title/{imdb_id}/"
            if re.fullmatch(r"tt[0-9]{7,}", imdb_id)
            else ""
        )

    @staticmethod
    def _identity(channel):
        return (channel.provider_key or channel.id) if channel else None

    def set_details(self, details: MediaDetails):
        self._provider_details = details
        if self._online_info:
            details = MediaDetails(
                info=merge(details.info, self._online_info),
                episodes=details.episodes,
                episode_info=details.episode_info,
                series_title=details.series_title,
            )
        selected_id = self._identity(self._current_channel())
        first_details = self._details is None
        self._details = details
        if self._series and first_details and details.episodes:
            self.preferences_toggle.setChecked(False)
        if self._series:
            self._refresh_series_info()
        previous_season = self.season_combo.currentData()
        self._episodes = details.episodes
        groups = list(dict.fromkeys(episode.group for episode in self._episodes))
        groups.sort(key=self._season_order)
        self.season_combo.blockSignals(True)
        self.season_combo.clear()
        for group in groups:
            self.season_combo.addItem(_season_label(group), group)
        preferred = next(
            (ep.group for ep in self._episodes if self._identity(ep) == selected_id),
            previous_season,
        )
        index = self.season_combo.findData(preferred)
        self.season_combo.setCurrentIndex(index if index >= 0 else (0 if groups else -1))
        self.season_combo.blockSignals(False)
        self.season_combo.setEnabled(bool(groups))
        self._populate_episodes(selected_id=selected_id)

    def set_online_info(self, info):
        self._online_info = info
        self.tmdb_label.setText("TMDB · " + info["tmdb_attribution"] if info else "")
        self.tmdb_label.setVisible(bool(info))
        self.trailer_button.setVisible(bool(info.get("trailer")))
        self.set_details(self._provider_details or MediaDetails())

    def set_online_status(self, text):
        self.online_status_label.setText(text)
        self.online_status_label.setVisible(bool(text))

    def _open_trailer(self):
        url = self._online_info.get("trailer", "")
        if url and trailer_url(url.removeprefix("https://www.youtube.com/watch?v=")) == url:
            QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _season_order(group):
        match = re.fullmatch(r"Sezon\s+([0-9]+)", group)
        return (0, int(match[1])) if match else (1, group.casefold())

    def _populate_episodes(self, _index=None, *, selected_id=None):
        if selected_id is None:
            selected_id = self._identity(self._current_channel())
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        group = self.season_combo.currentData()
        for episode in self._episodes:
            if episode.group == group:
                self.episode_combo.addItem(episode.name, episode)
        for index in range(self.episode_combo.count()):
            if self._identity(self.episode_combo.itemData(index)) == selected_id:
                self.episode_combo.setCurrentIndex(index)
                break
        self.episode_combo.blockSignals(False)
        self.episode_combo.setEnabled(self.episode_combo.count() > 0)
        self._sync_seasons()
        self._refresh_episode_rows()
        self._refresh_selection()

    def _sync_seasons(self, *_):
        if self.season_combo.signalsBlocked():
            return
        with QSignalBlocker(self.season_tabs):
            labels = [self.season_combo.itemText(i) for i in range(self.season_combo.count())]
            if labels != [self.season_tabs.tabText(i) for i in range(self.season_tabs.count())]:
                while self.season_tabs.count():
                    self.season_tabs.removeTab(0)
                for label in labels:
                    self.season_tabs.addTab(label)
            self.season_tabs.setCurrentIndex(self.season_combo.currentIndex())
        self.season_tabs.setEnabled(self.season_combo.count() > 0)

    def set_progress_lookup(self, fn):
        """Use the owner's saved positions; None clears watched indicators."""
        self._progress_lookup = fn
        self._refresh_episode_rows()
        self._refresh_play_label()

    def _refresh_episode_rows(self):
        count = self.episode_combo.count()
        for row in range(count):
            episode = self.episode_combo.itemData(row)
            info = self._details.episode_info.get(episode.provider_key, {}) if self._details else {}
            duration = _duration_label(info.get("duration") or "")
            saved = self._progress_lookup(episode.id) if self._progress_lookup else None
            progress, watched = None, False
            if saved is not None:
                position, total = saved
                watched = total > 0 and position >= total - 10
                if resumable(position, total):
                    progress = position / total
            description = _("Bölüm {number}, {name}").format(number=row + 1, name=episode.name)
            if duration:
                description += f", {duration}"
            if watched:
                description += _(", İzlendi")
            for role, value in (
                (EPISODE_DURATION_ROLE, duration),
                (EPISODE_PROGRESS_ROLE, progress),
                (EPISODE_WATCHED_ROLE, watched),
                (Qt.AccessibleTextRole, description),
                (Qt.ToolTipRole, description),
            ):
                self.episode_combo.setItemData(row, value, role)
        self.episode_list.setMinimumHeight(max(1, min(count, 2)) * EPISODE_ROW_HEIGHT)
        self.episode_list.setEnabled(count > 0)
        self.episode_list.viewport().update()

    def _select_episode(self, current, _previous=None):
        # The shared model also emits while the combo is being repopulated.
        if not self.episode_combo.signalsBlocked():
            self.episode_combo.setCurrentIndex(current.row())

    def _play_episode(self, index):
        if index.isValid():
            self.episode_combo.setCurrentIndex(index.row())
            self._request_play()

    def _current_channel(self) -> Channel | None:
        if self._series and self._details is not None:
            return self.episode_combo.currentData()
        return self._channel if self._channel.kind != "series" else None

    def selected_channel(self) -> Channel | None:
        channel = self._current_channel()
        return channel if channel is not None and channel.url else None

    def favorite_channel(self) -> Channel | None:
        return self._current_channel()

    def _refresh_selection(self, _index=None):
        index = self.episode_combo.model().index(self.episode_combo.currentIndex(), 0)
        with QSignalBlocker(self.episode_list.selectionModel()):
            self.episode_list.setCurrentIndex(index)
        channel = self._current_channel()
        identity = self._identity(channel)
        changed = identity != self._selection_identity
        self._selection_identity = identity
        info = {}
        if self._details is not None:
            if not self._series:
                info = self._details.info
            elif channel:
                info = self._details.episode_info.get(channel.provider_key, {})
        title = channel.name if channel else _("Bölüm seçilmedi")
        self.title_label.setText(title)
        self.setWindowTitle(title)
        if changed:
            self.plot_toggle.setChecked(False)
        self.description_label.setText(info.get("description") or _("Açıklama bulunmuyor."))
        genres = self._set_genres(info)
        self.facts_label.setText(self._facts(info))
        self.facts_label.setVisible(bool(self.facts_label.text()))
        kind = _("DİZİ") if self._series else _("FİLM")
        group = genres[0] if genres else channel.group if channel and not self._series else ""
        # Turkish capitals: i → İ (str.upper() alone would give a dotless I).
        group = (group.replace("i", "İ") if get_language() == "tr" else group).upper()
        self.kind_label.setText(f"{kind}  ·  {group}" if group else kind)
        self._fill_metadata(self.metadata, info, show_missing=self._series)
        self.about.setVisible(
            any(info.get(key) for key in ("director", "cast", "country", "language"))
        )
        self._imdb_url = self._imdb_link(info)
        # A film without an IMDb id can still be looked up by its title. Episodes
        # never borrow the series here; the series section offers that lookup.
        self._imdb_query = (
            (*clean_title(channel.name, info.get("year", "")), "movie")
            if not self._imdb_url and channel is not None and not self._series
            else None
        )
        self.imdb_button.setText(_("IMDb'de aç") if self._imdb_url else _("IMDb'de bul"))
        self.imdb_button.setVisible(bool(self._imdb_url or self._imdb_query))
        poster = info.get("poster", "")
        scope = _("Bölüm afişi") if self._series else _("İçerik afişi")
        if self._series and not poster:
            poster = self._details.info.get("poster", "") if self._details else ""
            poster = poster or (self._series_channel.logo if self._series_channel else "")
            if poster:
                scope = _("Dizi afişi (bölüm afişi bulunmuyor)")
            elif self._details is None and channel:
                poster = channel.logo
                scope = _("Kayıtlı afiş (bölüm veya dizi)")
        elif not self._series:
            poster = poster or self._channel.logo
        self.poster_scope_label.setText(scope if poster and self._series else "")
        self.poster_scope_label.setVisible(bool(self.poster_scope_label.text()))
        self.hero.name = self.poster_label.name = title
        self.poster_label.setAccessibleName(scope)
        self._set_artwork(
            self._online_info.get("poster") or poster, self._online_info.get("backdrop", "")
        )
        self.play_button.setEnabled(self.selected_channel() is not None)
        self._refresh_play_label()
        self.favorite_button.setEnabled(self.favorite_channel() is not None)
        if changed:
            self.set_favorite(False)
            self.selection_changed.emit(channel)

    def _refresh_series_info(self):
        info = self._details.info if self._details else {}
        title = self._details.series_title if self._details else ""
        title = title or (self._series_channel.name if self._series_channel else "")
        self.series_title_label.setText(title or _("Dizi adı bulunmuyor."))
        self.series_description_label.setText(info.get("description") or _("Açıklama bulunmuyor."))
        self._fill_metadata(self.series_metadata, info)
        self._series_imdb_url = self._imdb_link(info)
        self._series_imdb_query = (
            (*clean_title(title, info.get("year", "")), "series")
            if not self._series_imdb_url and title
            else None
        )
        self.series_imdb_button.setText(
            _("Diziyi IMDb'de aç") if self._series_imdb_url else _("Diziyi IMDb'de bul")
        )
        self.series_imdb_button.setVisible(bool(self._series_imdb_url or self._series_imdb_query))

    def _request_play(self):
        if channel := self.selected_channel():
            self.play_requested.emit(channel)

    def _request_favorite(self):
        if channel := self.favorite_channel():
            self.favorite_requested.emit(channel)

    def set_series_channel(self, channel: Channel | None):
        self._series_channel = channel
        self.series_favorite_button.setVisible(channel is not None)
        self.series_favorite_button.setEnabled(channel is not None)
        self._refresh_series_info()
        self._refresh_selection()

    def set_series_favorite(self, favorite: bool):
        self.series_favorite_button.setText(
            _("Diziyi favorilerden çıkar") if favorite else _("Diziyi favorilere ekle")
        )
        self.series_favorite_button.set_icon_name("star-filled" if favorite else "star")

    def _request_series_favorite(self):
        if self._series_channel is not None:
            self.series_favorite_requested.emit(self._series_channel)

    def _open_series_imdb(self):
        if self._series_imdb_url:
            QDesktopServices.openUrl(QUrl(self._series_imdb_url))
        elif self._series_imdb_query:
            self.imdb_lookup_requested.emit(*self._series_imdb_query)

    def set_favorite(self, favorite: bool):
        if self._series:
            text = _("Bölümü favorilerden çıkar") if favorite else _("Bölümü favorilere ekle")
        else:
            text = _("Favorilerden çıkar") if favorite else _("Favorilere ekle")
        self.favorite_button.setText(text)
        self.favorite_button.set_icon_name("star-filled" if favorite else "star")

    def set_status(self, text, retry=False):
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self.retry_button.setVisible(retry)

    def _open_imdb(self):
        if self._imdb_url:
            QDesktopServices.openUrl(QUrl(self._imdb_url))
        elif self._imdb_query:
            self.imdb_lookup_requested.emit(*self._imdb_query)

    def _release_poster(self, *_):
        for url in {self._poster_url, self._backdrop_url} - {""}:
            self._poster_cache.release(url)

    def _set_artwork(self, poster, backdrop):
        if self._backdrop_url and self._backdrop_url != backdrop:
            self._poster_cache.release(self._backdrop_url)
        self._backdrop_url = backdrop
        self._set_poster(poster)
        if backdrop:
            self._poster_cache.request_logo(backdrop)
            self._poster_ready(backdrop)

    def _set_poster(self, url):
        if url != self._poster_url:
            if self._poster_url:
                self._poster_cache.release(self._poster_url)
        self._poster_url = url
        self.poster_label.clear()
        self.poster_label.setText(_("Afiş yok"))
        self.hero.set_poster(None)
        if url:
            self._poster_cache.request_logo(url)
            self._poster_ready(url)

    @Slot(str)
    def _poster_ready(self, url):
        if url not in (self._poster_url, self._backdrop_url):
            return
        pixmap = self._poster_cache.prepared_logo(url)
        if pixmap is not None and not pixmap.isNull():
            if url == self._backdrop_url:
                self.hero.set_poster(pixmap)
                return
            ratio = self.devicePixelRatioF()
            scaled = pixmap.scaled(POSTER_SIZE * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.poster_label.setPixmap(rounded(scaled, 14))
            if not self._backdrop_url:
                self.hero.set_poster(pixmap)
