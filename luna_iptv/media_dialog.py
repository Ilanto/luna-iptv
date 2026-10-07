"""On-demand catalogue details; playback remains an explicit owner action."""

import re

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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
from .imdb import clean_title
from .library import resumable
from .media_details import MediaDetails
from .models import Channel
from .motion import IconButton
from .preferences import normalize_preferences

_LANGUAGE_PREFERENCES = (
    ("Türkçe", "tr"),
    ("İngilizce", "en"),
    ("Almanca", "de"),
    ("Fransızca", "fr"),
    ("İspanyolca", "es"),
    ("Arapça", "ar"),
    ("Rusça", "ru"),
    ("Japonca", "ja"),
    ("İtalyanca", "it"),
    ("Portekizce", "pt"),
)


POSTER_SIZE = QSize(220, 330)
EPISODE_DURATION_ROLE = Qt.UserRole + 1
EPISODE_PROGRESS_ROLE = Qt.UserRole + 2
EPISODE_WATCHED_ROLE = Qt.UserRole + 3
EPISODE_ROW_HEIGHT = 64


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
                painter.drawText(meta.adjusted(22, 0, 0, 0), Qt.AlignVCenter, "İzlendi")
                meta.setLeft(meta.left() + 22 + metrics.horizontalAdvance("İzlendi") + 16)
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


class PosterBackdrop(QWidget):
    """The card's hero background: the poster blurred into a soft, dark wash.

    Blurring is a down-scale to a few pixels drawn back up with smoothing: one
    tiny image per poster, no per-frame filtering.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wash = None

    def set_poster(self, pixmap):
        self._wash = (
            pixmap.scaled(12, 18, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            if pixmap is not None and not pixmap.isNull()
            else None
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        if self._wash is not None:
            side = max(rect.width(), rect.height() * 1.5)
            painter.setOpacity(0.55)
            painter.drawPixmap(
                QRectF(
                    rect.center().x() - side / 2, rect.center().y() - side * 0.75, side, side * 1.5
                ),
                self._wash,
                QRectF(self._wash.rect()),
            )
            painter.setOpacity(1.0)
        else:
            glow = QRadialGradient(QPointF(rect.width() * 0.7, 0), rect.width() * 0.8)
            glow.setColorAt(0.0, QColor("#1a2350"))
            glow.setColorAt(1.0, QColor(theme.NIGHT))
            painter.fillRect(rect, glow)
        shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = QColor(theme.NIGHT)
        top.setAlphaF(0.35)
        shade.setColorAt(0.0, top)
        shade.setColorAt(1.0, QColor(theme.NIGHT))
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
        self.scroll.setAccessibleName("İçerik ayrıntıları")
        body = QWidget()
        body.setObjectName("cardBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Hero: the poster, blurred and darkened, behind the poster itself.
        self.hero = PosterBackdrop()
        columns = QHBoxLayout(self.hero)
        columns.setContentsMargins(32, 32, 32, 28)
        columns.setSpacing(28)
        self.poster_label = text_label("Afiş yok", "poster")
        self.poster_label.setFixedSize(POSTER_SIZE)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setAccessibleName("İçerik afişi")
        artwork = QVBoxLayout()
        artwork.setSpacing(8)
        artwork.addWidget(self.poster_label)
        self.poster_scope_label = text_label("", "faint")
        self.poster_scope_label.setWordWrap(True)
        self.poster_scope_label.setMaximumWidth(POSTER_SIZE.width())
        artwork.addWidget(self.poster_scope_label)
        artwork.addStretch()
        columns.addLayout(artwork)

        content = QVBoxLayout()
        content.setSpacing(10)
        self.kind_label = text_label("DİZİ" if self._series else "FİLM", "eyebrow")
        content.addWidget(self.kind_label)
        self.title_label = text_label(channel.name, "display")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        content.addWidget(self.title_label)
        self.facts_label = text_label("", "facts")
        self.facts_label.setWordWrap(True)
        self.facts_label.setAccessibleName("Künye")
        content.addWidget(self.facts_label)
        self.description_label = text_label("Açıklama bulunmuyor.", "lead")
        self.description_label.setWordWrap(True)
        self.description_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.description_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(self.description_label)
        content.addSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.play_button = IconButton("Oynat", "play", label=True, size=18)
        self.play_button.setObjectName("primary")
        self.play_button.setMinimumSize(132, 44)
        self.play_button.clicked.connect(self._request_play)
        self.favorite_button = IconButton("", "star", label=True, size=18)
        self.favorite_button.setObjectName("glass")
        self.favorite_button.setMinimumHeight(44)
        self.favorite_button.clicked.connect(self._request_favorite)
        self.set_favorite(False)
        self.imdb_button = IconButton("IMDb'de aç", "external", label=True, size=16)
        self.imdb_button.setObjectName("glass")
        self.imdb_button.setMinimumHeight(46)
        self.imdb_button.setAccessibleName("IMDb sayfasını tarayıcıda aç")
        self.imdb_button.clicked.connect(self._open_imdb)
        self.imdb_button.hide()
        actions.addWidget(self.imdb_button)
        actions.addStretch()
        content.addLayout(actions)
        content.addStretch()
        columns.addLayout(content, 1)
        body_layout.addWidget(self.hero)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(32, 10, 32, 24)
        details_layout.setSpacing(16)

        self.selectors = QFrame()
        self.selectors.setObjectName("panel")
        selector_layout = QVBoxLayout(self.selectors)
        selector_layout.setContentsMargins(16, 12, 16, 12)
        selector_layout.setSpacing(10)
        self.season_combo = QComboBox(self.selectors)
        self.season_combo.setAccessibleName("Sezon")
        self.episode_combo = QComboBox(self.selectors)
        self.episode_combo.setAccessibleName("Bölüm")
        self.season_combo.hide()
        self.episode_combo.hide()
        self.season_tabs = QTabBar()
        self.season_tabs.setObjectName("seasonTabs")
        self.season_tabs.setAccessibleName("Sezon")
        self.season_tabs.setExpanding(False)
        self.season_tabs.setUsesScrollButtons(True)
        self.season_tabs.setElideMode(Qt.ElideNone)
        self.season_tabs.setDrawBase(False)
        self.season_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.season_tabs.setFocusPolicy(Qt.StrongFocus)
        selector_layout.addWidget(self.season_tabs)
        self.episode_list = QListView()
        self.episode_list.setObjectName("episodeList")
        self.episode_list.setAccessibleName("Bölümler")
        self.episode_list.setModel(self.episode_combo.model())
        self.episode_list.setItemDelegate(EpisodeDelegate(self.episode_list))
        self.episode_list.setUniformItemSizes(True)
        self.episode_list.setEditTriggers(QListView.NoEditTriggers)
        self.episode_list.setSelectionMode(QListView.SingleSelection)
        self.episode_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.episode_list.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.episode_list.setMouseTracking(True)
        self.episode_list.setFixedHeight(EPISODE_ROW_HEIGHT)
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
        details_layout.addWidget(self.selectors)

        status_row = QHBoxLayout()
        self.status_label = text_label("", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Ayrıntı durumu")
        self.retry_button = IconButton("Yeniden dene", "retry", label=True, size=16)
        self.retry_button.setObjectName("ghost")
        self.retry_button.clicked.connect(self.retry_requested)
        self.retry_button.hide()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.retry_button)
        details_layout.addLayout(status_row)

        about = QFrame()
        about.setObjectName("panel")
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(18, 14, 18, 16)
        about_layout.setSpacing(10)
        about_layout.addWidget(text_label("KÜNYE", "eyebrow"))
        self.metadata = QFormLayout()
        self.metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.metadata.setHorizontalSpacing(18)
        self.metadata.setVerticalSpacing(8)
        about_layout.addLayout(self.metadata)
        details_layout.addWidget(about)

        self.series_section = QFrame()
        self.series_section.setObjectName("panel")
        series_layout = QVBoxLayout(self.series_section)
        series_layout.setContentsMargins(18, 14, 18, 16)
        series_layout.setSpacing(10)
        series_layout.addWidget(text_label("DİZİ BİLGİLERİ", "eyebrow"))
        self.series_title_label = text_label("", "title")
        self.series_description_label = text_label("Açıklama bulunmuyor.")
        for label in (self.series_title_label, self.series_description_label):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            series_layout.addWidget(label)
        self.series_metadata = QFormLayout()
        self.series_metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.series_metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.series_metadata.setHorizontalSpacing(18)
        self.series_metadata.setVerticalSpacing(8)
        series_layout.addLayout(self.series_metadata)
        series_actions = QHBoxLayout()
        series_actions.setSpacing(8)
        self.series_imdb_button = IconButton("Diziyi IMDb'de aç", "external", label=True, size=15)
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
        series_layout.addLayout(series_actions)
        self.series_section.setVisible(self._series)
        details_layout.addWidget(self.series_section)

        preferences = QFrame()
        preferences.setObjectName("panel")
        preference_layout = QVBoxLayout(preferences)
        preference_layout.setContentsMargins(18, 14, 18, 16)
        preference_layout.setSpacing(10)
        preference_layout.addWidget(text_label("OYNATMA TERCİHLERİ", "eyebrow"))
        preference_row = QHBoxLayout()
        preference_row.setSpacing(10)
        self.audio_combo = QComboBox()
        self.subtitle_combo = QComboBox()
        for label_text, combo in (
            ("Ses dili", self.audio_combo),
            ("Altyazı dili", self.subtitle_combo),
        ):
            label = text_label(label_text, "muted")
            label.setBuddy(combo)
            combo.setAccessibleName(label_text)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            preference_row.addWidget(label)
            preference_row.addWidget(combo, 1)
        preference_layout.addLayout(preference_row)
        self.remember_checkbox = QCheckBox("Bu kaynak için hatırla")
        preference_layout.addWidget(self.remember_checkbox)
        self.preference_note = text_label(
            "Bunlar dil tercihleridir; kullanılabilir ses ve altyazı parçaları "
            "oynatma başlayınca öğrenilir. Önceden ikinci bir yayın bağlantısı açılmaz. "
            "Tercihler yalnızca Oynat ve varsa devam seçimi onaylandığında uygulanır.",
            "faint",
        )
        self.preference_note.setWordWrap(True)
        self.preference_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        preference_layout.addWidget(self.preference_note)
        details_layout.addWidget(preferences)
        details_layout.addStretch()
        body_layout.addWidget(details, 1)
        self.set_playback_preferences({})
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setObjectName("cardFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 14)
        footer_layout.setSpacing(10)
        # Play and favorite live in the always-visible bar, never behind scrolling.
        footer_layout.addWidget(self.play_button)
        footer_layout.addWidget(self.favorite_button)
        footer_layout.addStretch()
        self.close_button = IconButton("Kapat", "close", label=True, size=15)
        self.close_button.setObjectName("ghost")
        self.close_button.clicked.connect(self.close)
        footer_layout.addWidget(self.close_button)
        layout.addWidget(footer)
        # Enter must not unexpectedly start playback while browsing metadata.
        for button in (
            self.play_button,
            self.favorite_button,
            self.close_button,
            self.retry_button,
            self.imdb_button,
            self.series_imdb_button,
            self.series_favorite_button,
        ):
            button.setAutoDefault(False)
        self.setTabOrder(self.scroll, self.imdb_button)
        self.setTabOrder(self.imdb_button, self.series_imdb_button)
        self.setTabOrder(self.series_imdb_button, self.series_favorite_button)
        self.setTabOrder(self.series_favorite_button, self.audio_combo)
        self.setTabOrder(self.audio_combo, self.subtitle_combo)
        self.setTabOrder(self.subtitle_combo, self.remember_checkbox)
        self.setTabOrder(self.remember_checkbox, self.season_tabs)
        self.setTabOrder(self.season_tabs, self.episode_list)
        self.setTabOrder(self.episode_list, self.retry_button)
        self.setTabOrder(self.retry_button, self.play_button)
        self.setTabOrder(self.play_button, self.favorite_button)
        self.setTabOrder(self.favorite_button, self.close_button)
        self.close_button.setFocus()
        # A QObject-bound slot gives Qt a receiver context: destroying the card
        # automatically disconnects it even while the shared cache is working.
        poster_cache.ready.connect(self._poster_ready)
        self.finished.connect(self._release_poster)
        self.set_series_channel(self._series_channel)

    def set_playback_preferences(self, preferences):
        """Seed this card without changing playback or saving preferences."""
        normalized = normalize_preferences(preferences)
        for mode, combo in (("audio", self.audio_combo), ("sub", self.subtitle_combo)):
            combo.clear()
            combo.addItem("Otomatik", {"mode": "auto"})
            if mode == "sub":
                combo.addItem("Kapalı", {"mode": "off"})
            for title, language in _LANGUAGE_PREFERENCES:
                choice = normalize_preferences({mode: {"mode": "track", "lang": language}})
                combo.addItem(title, choice[mode])
            selected = normalized.get(mode, {"mode": "auto"})
            index = next(
                (i for i in range(combo.count()) if combo.itemData(i) == selected),
                -1,
            )
            if index < 0:
                description = (
                    "Kapalı"
                    if selected.get("mode") == "off"
                    else " / ".join(
                        value for value in (selected.get("lang"), selected.get("title")) if value
                    )
                )
                combo.addItem(f"Kayıtlı tercih: {description}", selected)
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
            ("year", "Yıl"),
            ("genre", "Tür"),
            ("duration", "Süre"),
            ("director", "Yönetmen"),
            ("cast", "Oyuncular"),
            (
                "rating",
                "IMDb puanı" if info.get("rating_source", "").casefold() == "imdb" else "Puan",
            ),
        ]
        for key, title in fields:
            value = info.get(key)
            if value or show_missing:
                label = text_label(value or "Belirtilmemiş")
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                label.setAccessibleName(title)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                layout.addRow(text_label(title, "muted"), label)

    @staticmethod
    def _facts(info):
        """One line of the essentials: year · genre · duration · ★ rating."""
        parts = [info.get(key, "") for key in ("year", "genre", "duration")]
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
        selected_id = self._identity(self._current_channel())
        self._details = details
        if self._series:
            self._refresh_series_info()
        previous_season = self.season_combo.currentData()
        self._episodes = details.episodes
        groups = list(dict.fromkeys(episode.group for episode in self._episodes))
        groups.sort(key=self._season_order)
        self.season_combo.blockSignals(True)
        self.season_combo.clear()
        for group in groups:
            self.season_combo.addItem(group or "Sezon belirtilmemiş", group)
        preferred = next(
            (ep.group for ep in self._episodes if self._identity(ep) == selected_id),
            previous_season,
        )
        index = self.season_combo.findData(preferred)
        self.season_combo.setCurrentIndex(index if index >= 0 else (0 if groups else -1))
        self.season_combo.blockSignals(False)
        self.season_combo.setEnabled(bool(groups))
        self._populate_episodes(selected_id=selected_id)

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

    def _refresh_episode_rows(self):
        count = self.episode_combo.count()
        for row in range(count):
            episode = self.episode_combo.itemData(row)
            info = self._details.episode_info.get(episode.provider_key, {}) if self._details else {}
            duration = info.get("duration") or ""
            saved = self._progress_lookup(episode.id) if self._progress_lookup else None
            progress, watched = None, False
            if saved is not None:
                position, total = saved
                watched = total > 0 and position >= total - 10
                if resumable(position, total):
                    progress = position / total
            description = f"Bölüm {row + 1}, {episode.name}"
            if duration:
                description += f", {duration}"
            if watched:
                description += ", İzlendi"
            for role, value in (
                (EPISODE_DURATION_ROLE, duration),
                (EPISODE_PROGRESS_ROLE, progress),
                (EPISODE_WATCHED_ROLE, watched),
                (Qt.AccessibleTextRole, description),
                (Qt.ToolTipRole, description),
            ):
                self.episode_combo.setItemData(row, value, role)
        self.episode_list.setFixedHeight(max(1, min(count, 5)) * EPISODE_ROW_HEIGHT)
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
        title = channel.name if channel else "Bölüm seçilmedi"
        self.title_label.setText(title)
        self.setWindowTitle(title)
        self.description_label.setText(info.get("description") or "Açıklama bulunmuyor.")
        self.facts_label.setText(self._facts(info))
        self.facts_label.setVisible(bool(self.facts_label.text()))
        kind = "BÖLÜM" if self._series else "FİLM"
        group = channel.group if channel and not self._series else ""
        # Turkish capitals: i → İ (str.upper() alone would give a dotless I).
        group = group.replace("i", "İ").upper()
        self.kind_label.setText(f"{kind}  ·  {group}" if group else kind)
        self._fill_metadata(self.metadata, info, show_missing=self._series)
        self._imdb_url = self._imdb_link(info)
        # A film without an IMDb id can still be looked up by its title. Episodes
        # never borrow the series here; the series section offers that lookup.
        self._imdb_query = (
            (*clean_title(channel.name, info.get("year", "")), "movie")
            if not self._imdb_url and channel is not None and not self._series
            else None
        )
        self.imdb_button.setText("IMDb'de aç" if self._imdb_url else "IMDb'de bul")
        self.imdb_button.setVisible(bool(self._imdb_url or self._imdb_query))
        poster = info.get("poster", "")
        scope = "Bölüm afişi" if self._series else "İçerik afişi"
        if self._series and not poster:
            poster = self._details.info.get("poster", "") if self._details else ""
            poster = poster or (self._series_channel.logo if self._series_channel else "")
            if poster:
                scope = "Dizi afişi (bölüm afişi bulunmuyor)"
            elif self._details is None and channel:
                poster = channel.logo
                scope = "Kayıtlı afiş (bölüm veya dizi)"
        elif not self._series:
            poster = poster or self._channel.logo
        self.poster_scope_label.setText(scope if poster and self._series else "")
        self.poster_label.setAccessibleName(scope)
        self._set_poster(poster)
        self.play_button.setEnabled(self.selected_channel() is not None)
        self.favorite_button.setEnabled(self.favorite_channel() is not None)
        if changed:
            self.set_favorite(False)
            self.selection_changed.emit(channel)

    def _refresh_series_info(self):
        info = self._details.info if self._details else {}
        title = self._details.series_title if self._details else ""
        title = title or (self._series_channel.name if self._series_channel else "")
        self.series_title_label.setText(title or "Dizi adı bulunmuyor.")
        self.series_description_label.setText(info.get("description") or "Açıklama bulunmuyor.")
        self._fill_metadata(self.series_metadata, info)
        self._series_imdb_url = self._imdb_link(info)
        self._series_imdb_query = (
            (*clean_title(title, info.get("year", "")), "series")
            if not self._series_imdb_url and title
            else None
        )
        self.series_imdb_button.setText(
            "Diziyi IMDb'de aç" if self._series_imdb_url else "Diziyi IMDb'de bul"
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
            "Diziyi favorilerden çıkar" if favorite else "Diziyi favorilere ekle"
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
            text = "Bölümü favorilerden çıkar" if favorite else "Bölümü favorilere ekle"
        else:
            text = "Favorilerden çıkar" if favorite else "Favorilere ekle"
        self.favorite_button.setText(text)
        self.favorite_button.set_icon_name("star-filled" if favorite else "star")

    def set_status(self, text, retry=False):
        self.status_label.setText(text)
        self.retry_button.setVisible(retry)

    def _open_imdb(self):
        if self._imdb_url:
            QDesktopServices.openUrl(QUrl(self._imdb_url))
        elif self._imdb_query:
            self.imdb_lookup_requested.emit(*self._imdb_query)

    def _release_poster(self, *_):
        if self._poster_url:
            self._poster_cache.release(self._poster_url)

    def _set_poster(self, url):
        if url != self._poster_url:
            self._release_poster()
        self._poster_url = url
        self.poster_label.clear()
        self.poster_label.setText("Afiş yok")
        self.hero.set_poster(None)
        if url:
            self._poster_cache.request_logo(url)
            self._poster_ready(url)

    @Slot(str)
    def _poster_ready(self, url):
        if url != self._poster_url:
            return
        pixmap = self._poster_cache.prepared_logo(url)
        if pixmap is not None and not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            scaled = pixmap.scaled(
                self.poster_label.size() * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            scaled.setDevicePixelRatio(ratio)
            self.poster_label.setPixmap(rounded(scaled, 14))
            self.hero.set_poster(pixmap)
