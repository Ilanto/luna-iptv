"""On-demand catalogue details; playback remains an explicit owner action."""

import re

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .dialogs import text_label
from .media_details import MediaDetails
from .models import Channel


class MediaDetailDialog(QDialog):
    play_requested = Signal(object)
    favorite_requested = Signal(object)
    retry_requested = Signal()

    def __init__(self, channel, poster_cache, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.setWindowTitle(channel.name)
        self.resize(800, 600)
        self.setMinimumSize(540, 320)
        self._channel = channel
        self._poster_cache = poster_cache
        self._poster_url = ""
        self._imdb_url = ""
        self._episodes = []
        self._series = channel.kind == "series" or bool(channel.series_id)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("İçerik ayrıntıları")
        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        self.poster_label = text_label("Afiş yok", "muted")
        self.poster_label.setFixedSize(240, 360)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setAccessibleName("İçerik afişi")
        columns.addWidget(self.poster_label, 0, Qt.AlignTop)

        content = QVBoxLayout()
        content.setSpacing(12)
        self.title_label = text_label(channel.name, "heading")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        content.addWidget(self.title_label)
        self.description_label = text_label("Açıklama bulunmuyor.")
        self.description_label.setWordWrap(True)
        self.description_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.description_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content.addWidget(self.description_label)
        self.metadata = QFormLayout()
        self.metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.metadata.setHorizontalSpacing(12)
        self.metadata.setVerticalSpacing(8)
        content.addLayout(self.metadata)
        self.imdb_button = QPushButton("IMDb'de aç")
        self.imdb_button.setAccessibleName("IMDb sayfasını tarayıcıda aç")
        self.imdb_button.clicked.connect(self._open_imdb)
        self.imdb_button.hide()
        content.addWidget(self.imdb_button, 0, Qt.AlignLeft)
        content.addStretch()
        columns.addLayout(content, 1)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        self.selectors = QWidget()
        selector_layout = QFormLayout(self.selectors)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        self.season_combo = QComboBox()
        self.season_combo.setAccessibleName("Sezon")
        self.episode_combo = QComboBox()
        self.episode_combo.setAccessibleName("Bölüm")
        for text, combo in (("&Sezon", self.season_combo), ("&Bölüm", self.episode_combo)):
            label = text_label(text, "muted")
            label.setBuddy(combo)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            selector_layout.addRow(label, combo)
        self.season_combo.currentIndexChanged.connect(self._populate_episodes)
        self.episode_combo.currentIndexChanged.connect(self._update_play_button)
        self.selectors.setVisible(self._series)
        self.season_combo.setEnabled(False)
        self.episode_combo.setEnabled(False)
        layout.addWidget(self.selectors)

        status_row = QHBoxLayout()
        self.status_label = text_label("", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Ayrıntı durumu")
        self.retry_button = QPushButton("Yeniden dene")
        self.retry_button.clicked.connect(self.retry_requested)
        self.retry_button.hide()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.retry_button)
        layout.addLayout(status_row)

        actions = QHBoxLayout()
        self.play_button = QPushButton("Oynat")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self._request_play)
        self.favorite_button = QPushButton()
        self.favorite_button.clicked.connect(self._request_favorite)
        self.set_favorite(False)
        self.close_button = QPushButton("Kapat")
        self.close_button.clicked.connect(self.close)
        actions.addWidget(self.play_button)
        actions.addWidget(self.favorite_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        # Enter must not unexpectedly start playback while browsing metadata.
        for button in (
            self.play_button,
            self.favorite_button,
            self.close_button,
            self.retry_button,
            self.imdb_button,
        ):
            button.setAutoDefault(False)
        self.setTabOrder(self.scroll, self.imdb_button)
        self.setTabOrder(self.imdb_button, self.season_combo)
        self.setTabOrder(self.season_combo, self.episode_combo)
        self.setTabOrder(self.episode_combo, self.retry_button)
        self.setTabOrder(self.retry_button, self.play_button)
        self.setTabOrder(self.play_button, self.favorite_button)
        self.setTabOrder(self.favorite_button, self.close_button)
        self.close_button.setFocus()
        # A QObject-bound slot gives Qt a receiver context: destroying the card
        # automatically disconnects it even while the shared cache is working.
        poster_cache.ready.connect(self._poster_ready)
        self._set_poster(channel.logo)
        self._update_play_button()

    def set_details(self, details: MediaDetails):
        info = details.info
        self.description_label.setText(info.get("description") or "Açıklama bulunmuyor.")
        while self.metadata.rowCount():
            self.metadata.removeRow(0)
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
            if value := info.get(key):
                label = text_label(value)
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                label.setAccessibleName(title)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.metadata.addRow(text_label(title, "muted"), label)
        imdb_id = info.get("imdb_id", "")
        self._imdb_url = (
            f"https://www.imdb.com/title/{imdb_id}/"
            if re.fullmatch(r"tt[0-9]{7,}", imdb_id)
            else ""
        )
        self.imdb_button.setVisible(bool(self._imdb_url))
        self._set_poster(info.get("poster") or self._channel.logo)

        selected = self.selected_channel()
        selected_id = selected.id if selected else self._channel.id
        previous_season = self.season_combo.currentData()
        self._episodes = details.episodes
        groups = list(dict.fromkeys(episode.group for episode in self._episodes))
        groups.sort(key=self._season_order)
        self.season_combo.blockSignals(True)
        self.season_combo.clear()
        for group in groups:
            self.season_combo.addItem(group or "Sezon belirtilmemiş", group)
        preferred = next(
            (ep.group for ep in self._episodes if ep.id == selected_id), previous_season
        )
        index = self.season_combo.findData(preferred)
        self.season_combo.setCurrentIndex(index if index >= 0 else (0 if groups else -1))
        self.season_combo.blockSignals(False)
        self.season_combo.setEnabled(bool(groups))
        self._populate_episodes()
        for index in range(self.episode_combo.count()):
            if self.episode_combo.itemData(index).id == selected_id:
                self.episode_combo.setCurrentIndex(index)
                break

    @staticmethod
    def _season_order(group):
        match = re.fullmatch(r"Sezon\s+([0-9]+)", group)
        return (0, int(match[1])) if match else (1, group.casefold())

    def _populate_episodes(self):
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        group = self.season_combo.currentData()
        for episode in self._episodes:
            if episode.group == group:
                self.episode_combo.addItem(episode.name, episode)
        self.episode_combo.blockSignals(False)
        self.episode_combo.setEnabled(self.episode_combo.count() > 0)
        self._update_play_button()

    def selected_channel(self) -> Channel | None:
        if self._series and self.episode_combo.count():
            channel = self.episode_combo.currentData()
        else:
            channel = self._channel
        return channel if channel.kind != "series" and channel.url else None

    def _update_play_button(self):
        self.play_button.setEnabled(self.selected_channel() is not None)

    def _request_play(self):
        if channel := self.selected_channel():
            self.play_requested.emit(channel)

    def _request_favorite(self):
        self.favorite_requested.emit(self._channel)

    def set_favorite(self, favorite: bool):
        self.favorite_button.setText("Favorilerden çıkar" if favorite else "Favorilere ekle")

    def set_status(self, text, retry=False):
        self.status_label.setText(text)
        self.retry_button.setVisible(retry)

    def _open_imdb(self):
        if self._imdb_url:
            QDesktopServices.openUrl(QUrl(self._imdb_url))

    def _set_poster(self, url):
        self._poster_url = url
        self.poster_label.clear()
        self.poster_label.setText("Afiş yok")
        if url:
            self._poster_cache.request_logo(url)
            self._poster_ready(url)

    @Slot(str)
    def _poster_ready(self, url):
        if url != self._poster_url:
            return
        pixmap = self._poster_cache.prepared_logo(url)
        if pixmap is not None and not pixmap.isNull():
            self.poster_label.setPixmap(
                pixmap.scaled(self.poster_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
