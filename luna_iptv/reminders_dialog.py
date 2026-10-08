"""Upcoming programme reminders in the shared night theme."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .dialogs import text_label


class RemindersDialog(QDialog):
    def __init__(self, service, store, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Hatırlatıcılar")
        self.resize(560, 420)
        self._service = service
        self._store = store
        layout = QVBoxLayout(self)
        layout.addWidget(text_label("Hatırlatıcılar", "heading"))
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self._scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Kapat")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        service.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        channels = {channel.id: channel.name for channel in self._store.channels()}
        reminders = self._service.reminders()
        if not reminders:
            rows.addWidget(text_label("Henüz bir hatırlatıcı yok.", "muted"))
        for item in reminders:
            panel = QFrame()
            panel.setObjectName("panel")
            row = QHBoxLayout(panel)
            text = QVBoxLayout()
            title = text_label(item["title"])
            title.setWordWrap(True)
            text.addWidget(title)
            channel = channels.get(item["channel_id"], "Kanal artık kaynakta yok")
            moment = datetime.fromtimestamp(item["start"]).strftime("%d.%m.%Y · %H:%M")
            detail = text_label(f"{channel}\n{moment}", "muted")
            detail.setWordWrap(True)
            text.addWidget(detail)
            row.addLayout(text, 1)
            remove = QPushButton("Kaldır")
            remove.setAccessibleName(f"Hatırlatıcıyı kaldır: {item['title']}")
            remove.clicked.connect(lambda checked=False, rid=item["id"]: self._service.remove(rid))
            row.addWidget(remove)
            rows.addWidget(panel)
        rows.addStretch()
        previous = self._scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self._scroll.setWidget(content)
