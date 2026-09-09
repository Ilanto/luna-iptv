"""On-demand catalogue details; playback remains an explicit owner action."""

import re

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
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


class MediaDetailDialog(QDialog):
    play_requested = Signal(object)
    favorite_requested = Signal(object)
    retry_requested = Signal()
    selection_changed = Signal(object)
    series_favorite_requested = Signal(object)

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
        self._details = None
        self._selection_identity = None
        self._series_channel = channel if channel.kind == "series" else None
        self._series_imdb_url = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("İçerik ayrıntıları")
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        self.poster_label = text_label("Afiş yok", "muted")
        self.poster_label.setFixedSize(240, 360)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setAccessibleName("İçerik afişi")
        artwork = QVBoxLayout()
        artwork.addWidget(self.poster_label)
        self.poster_scope_label = text_label("", "muted")
        self.poster_scope_label.setWordWrap(True)
        artwork.addWidget(self.poster_scope_label)
        artwork.addStretch()
        columns.addLayout(artwork)

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
        self.series_section = QWidget()
        series_layout = QVBoxLayout(self.series_section)
        series_layout.setContentsMargins(0, 12, 0, 0)
        series_layout.setSpacing(12)
        series_layout.addWidget(text_label("Dizi bilgileri", "heading"))
        self.series_title_label = text_label("", "heading")
        self.series_description_label = text_label("Açıklama bulunmuyor.")
        for label in (self.series_title_label, self.series_description_label):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            series_layout.addWidget(label)
        self.series_metadata = QFormLayout()
        self.series_metadata.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.series_metadata.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.series_metadata.setHorizontalSpacing(12)
        self.series_metadata.setVerticalSpacing(8)
        series_layout.addLayout(self.series_metadata)
        self.series_imdb_button = QPushButton("Diziyi IMDb'de aç")
        self.series_imdb_button.clicked.connect(self._open_series_imdb)
        self.series_imdb_button.hide()
        series_layout.addWidget(self.series_imdb_button, 0, Qt.AlignLeft)
        self.series_favorite_button = QPushButton()
        self.series_favorite_button.clicked.connect(self._request_series_favorite)
        self.set_series_favorite(False)
        series_layout.addWidget(self.series_favorite_button, 0, Qt.AlignLeft)
        self.series_section.setVisible(self._series)
        content.addWidget(self.series_section)
        content.addStretch()
        columns.addLayout(content, 1)
        body_layout.addLayout(columns)

        preference_form = QFormLayout()
        preference_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        preference_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        preference_form.setHorizontalSpacing(12)
        preference_form.setVerticalSpacing(8)
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
            preference_form.addRow(label, combo)
        self.remember_checkbox = QCheckBox("Bu kaynak için hatırla")
        preference_form.addRow(self.remember_checkbox)
        body_layout.addLayout(preference_form)
        self.preference_note = text_label(
            "Bunlar dil tercihleridir; kullanılabilir ses ve altyazı parçaları "
            "oynatma başlayınca öğrenilir. Önceden ikinci bir yayın bağlantısı açılmaz. "
            "Tercihler yalnızca Oynat ve varsa devam seçimi onaylandığında uygulanır.",
            "muted",
        )
        self.preference_note.setWordWrap(True)
        self.preference_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body_layout.addWidget(self.preference_note)
        self.set_playback_preferences({})
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
        self.episode_combo.currentIndexChanged.connect(self._refresh_selection)
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
        self.setTabOrder(self.remember_checkbox, self.season_combo)
        self.setTabOrder(self.season_combo, self.episode_combo)
        self.setTabOrder(self.episode_combo, self.retry_button)
        self.setTabOrder(self.retry_button, self.play_button)
        self.setTabOrder(self.play_button, self.favorite_button)
        self.setTabOrder(self.favorite_button, self.close_button)
        self.close_button.setFocus()
        # A QObject-bound slot gives Qt a receiver context: destroying the card
        # automatically disconnects it even while the shared cache is working.
        poster_cache.ready.connect(self._poster_ready)
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
        self._refresh_selection()

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
        self._fill_metadata(self.metadata, info, show_missing=self._series)
        self._imdb_url = self._imdb_link(info)
        self.imdb_button.setVisible(bool(self._imdb_url))
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
        self.series_imdb_button.setVisible(bool(self._series_imdb_url))

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

    def _request_series_favorite(self):
        if self._series_channel is not None:
            self.series_favorite_requested.emit(self._series_channel)

    def _open_series_imdb(self):
        if self._series_imdb_url:
            QDesktopServices.openUrl(QUrl(self._series_imdb_url))

    def set_favorite(self, favorite: bool):
        if self._series:
            text = "Bölümü favorilerden çıkar" if favorite else "Bölümü favorilere ekle"
        else:
            text = "Favorilerden çıkar" if favorite else "Favorilere ekle"
        self.favorite_button.setText(text)

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
