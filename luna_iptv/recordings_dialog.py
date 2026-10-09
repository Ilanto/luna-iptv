"""Scheduled and completed recordings with guarded playback actions."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .dialogs import text_label
from .motion import IconButton

STATUS = {
    "scheduled": "Planlandı",
    "running": "Kaydediliyor",
    "finished": "Tamamlandı",
    "missed": "Kaçırıldı",
    "cancelled": "İptal edildi",
    "stopped": "Durduruldu",
    "failed": "Başarısız",
    "interrupted": "Yarıda kesildi",
}


class RecordingsDialog(QDialog):
    def __init__(self, service, play, allowed, parent=None):
        super().__init__(parent)
        self.service, self._play, self._allowed = service, play, allowed
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Kayıtlar")
        self.resize(760, 450)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label("Kayıtlar", "heading"))
        note = text_label(
            "Planlanan kayıtlar için Luna açık kalmalı. Kapatınca çalışan kayıt durur.", "muted"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Kanal / Program", "Başlangıç", "Durum"])
        self.list.setRootIsDecorated(False)
        self.list.itemSelectionChanged.connect(self._selection)
        layout.addWidget(self.list)
        self.message = text_label("", "muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.cancel_button = IconButton("İptal et / Durdur", "stop", label=True)
        self.play_button = IconButton("Oynat", "play", label=True)
        self.folder_button = IconButton("Klasörü aç", "external", label=True)
        for button in (self.cancel_button, self.play_button, self.folder_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.cancel_button.clicked.connect(self._cancel)
        self.play_button.clicked.connect(self._watch)
        self.folder_button.clicked.connect(self._folder)
        service.changed.connect(self.refresh)
        self.refresh()

    def selected(self):
        item = self.list.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def refresh(self):
        selected = self.selected()
        self.list.clear()
        for recording in self.service.store.recordings():
            if not self._allowed(recording, False):
                continue
            item = QTreeWidgetItem(
                [
                    f"{recording['channel_name']} · {recording['title']}",
                    datetime.fromtimestamp(recording["start"]).strftime("%d.%m %H:%M"),
                    STATUS.get(recording["status"], recording["status"]),
                ]
            )
            item.setData(0, Qt.UserRole, recording)
            self.list.addTopLevelItem(item)
            if selected and selected["id"] == recording["id"]:
                self.list.setCurrentItem(item)
        self.list.resizeColumnToContents(0)
        self._selection()

    def _selection(self):
        item = self.selected()
        self.cancel_button.setEnabled(bool(item and item["status"] in {"scheduled", "running"}))
        exists = bool(item and item["path"] and Path(item["path"]).is_file())
        self.play_button.setEnabled(exists and item["status"] != "running")
        self.folder_button.setEnabled(exists)
        self.message.setText(item["message"] if item else "")

    def _cancel(self):
        if item := self.selected():
            self.service.cancel(item["id"])

    def _watch(self):
        if item := self.selected():
            self._play(item)

    def _folder(self):
        if (item := self.selected()) and self._allowed(item, True):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item["path"]).parent)))
