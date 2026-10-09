"""Subtitle selection and playback-token guards for asynchronous results."""

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)
from shiboken6 import isValid

from .dialogs import text_label
from .media_controller import source_fingerprint
from .motion import IconButton
from .subtitles import OpenSubtitlesClient, SubtitleCache, episode_query


class SubtitleDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Altyazı bul")
        self.resize(620, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.addWidget(text_label("Altyazı bul", "heading"))
        self.query = QLineEdit(title)
        self.query.setAccessibleName("Film veya dizi adı")
        row = QHBoxLayout()
        row.addWidget(self.query, 1)
        self.search_button = IconButton("Ara", "search", label=True)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.results = QListWidget()
        self.results.setAccessibleName("Dil, sürüm ve indirme sayısı")
        self.results.setWordWrap(True)
        layout.addWidget(self.results, 1)
        self.note = text_label("Türkçe, ardından İngilizce altyazılar · OpenSubtitles", "muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        footer = QHBoxLayout()
        footer.addStretch()
        self.download_button = IconButton("İndir ve kullan", "tracks", label=True)
        self.download_button.setEnabled(False)
        self.download_button.setObjectName("primary")
        footer.addWidget(self.download_button)
        close = IconButton("Kapat", "close", label=True)
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)
        for button in (self.search_button, self.download_button, close):
            button.setAutoDefault(False)
        self.results.currentRowChanged.connect(
            lambda row: self.download_button.setEnabled(row >= 0 and self.search_button.isEnabled())
        )

    def set_busy(self, busy, note):
        self.query.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        self.results.setEnabled(not busy)
        self.download_button.setEnabled(not busy and self.results.currentRow() >= 0)
        self.note.setText(note)

    def set_results(self, results):
        self.results.clear()
        for subtitle in results:
            language = {"tr": "Türkçe", "en": "English"}.get(subtitle.language, subtitle.language)
            item = QListWidgetItem(
                f"{language} · {subtitle.release} · {subtitle.downloads} indirme"
            )
            item.setData(Qt.UserRole, subtitle)
            self.results.addItem(item)
        self.set_busy(False, "Bir altyazı seç." if results else "Altyazı bulunamadı.")


class SubtitleController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self._restored_token = None
        self._selection_generation = 0

    def available(self):
        window = self.window
        return bool(
            not window._closed
            and window.current
            and window.current.kind == "movie"
            and window._playback_active
            and not window._idle
            and window.store.online_secret("opensubtitles_api_key").strip()
        )

    def _current(self, channel, token, key):
        w = self.window
        return bool(
            not w._closed
            and w._playback_active
            and w.current
            and w.current.id == channel.id
            and w._playback_token == token
            and w.store.online_secret("opensubtitles_api_key") == key
        )

    def open(self):
        if not self.available():
            return
        if self.dialog is not None and isValid(self.dialog):
            self.dialog.reject()
        w = self.window
        channel, token = w.current, w._playback_token
        key = w.store.online_secret("opensubtitles_api_key")
        client = OpenSubtitlesClient(
            key,
            w.store.path.parent,
            w.store.online_secret("opensubtitles_username"),
            w.store.online_secret("opensubtitles_password"),
        )
        title, info = "", {}
        source = w.source_for(channel)
        if source:
            fingerprint = source_fingerprint(source)
            series = next(
                (
                    c
                    for c in w.store.channels(source["id"])
                    if c.kind == "series" and c.series_id == channel.series_id
                ),
                None,
            )
            cached = w.store.media_details(channel.id, fingerprint)
            if cached is None and series:
                cached = w.store.media_details(series.id, fingerprint)
            if cached:
                details = cached[0]
                title = details.series_title
                info = (
                    details.episode_info.get(channel.provider_key, {})
                    if channel.series_id
                    else details.info
                )
            title = title or (series.name if series else "")
        dialog = SubtitleDialog(episode_query(channel, title, info)["query"], w)
        self.dialog = dialog

        def visible():
            return self.dialog is dialog and isValid(dialog) and self._current(channel, token, key)

        def finished(_result):
            if self.dialog is dialog:
                self.dialog = None

        def failed(error):
            if visible():
                dialog.set_busy(False, error)

        def search():
            if not visible():
                return
            query = dialog.query.text().strip()
            if not query:
                return
            dialog.set_busy(True, "Altyazılar aranıyor…")
            w.run_task(
                lambda: client.search(channel, query, info),
                lambda results: dialog.set_results(results) if visible() else None,
                "",
                busy=False,
                failure=failed,
            )

        def downloaded(path):
            if visible():
                w.track_preferences.external_subtitle()
                w.player.command(["sub-add", str(path), "select"])
                dialog.accept()

        def download():
            item = dialog.results.currentItem()
            if not visible() or item is None:
                return
            selected = item.data(Qt.UserRole)
            self._selection_generation += 1
            dialog.set_busy(True, "Altyazı indiriliyor…")

            def work():
                path = client.download(selected)
                client.cache.remember(channel.id, path)
                return path

            w.run_task(work, downloaded, "", busy=False, failure=failed)

        dialog.finished.connect(finished)
        dialog.search_button.clicked.connect(search)
        dialog.query.returnPressed.connect(search)
        dialog.download_button.clicked.connect(download)
        dialog.show()
        search()

    def loaded(self):
        w = self.window
        if not self.available() or self._restored_token == w._playback_token:
            return
        channel, token = w.current, w._playback_token
        self._restored_token = token
        key = w.store.online_secret("opensubtitles_api_key")
        cache = SubtitleCache(w.store.path.parent)
        generation = self._selection_generation

        def loaded(path):
            if (
                path is not None
                and self._current(channel, token, key)
                and generation == self._selection_generation
            ):
                w.track_preferences.external_subtitle()
                w.player.command(["sub-add", str(path), "select"])

        w.run_task(
            lambda: cache.remembered(channel.id), loaded, "", busy=False, failure=lambda _: None
        )

    def dismiss(self):
        if self.dialog is not None and isValid(self.dialog):
            self.dialog.reject()
