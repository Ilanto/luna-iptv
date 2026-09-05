"""Small explicit choices which never interrupt the current stream on cancel."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QVBoxLayout

from .dialogs import text_label


class ResumeDialog(QDialog):
    def __init__(self, title, position_text, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("İzlemeye devam et")
        self.choice = None
        layout = QVBoxLayout(self)
        title_label = text_label(title, "heading")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(text_label(f"Kaldığın yer: {position_text}", "muted"))
        buttons = QDialogButtonBox()
        self.resume_button = buttons.addButton("Devam et", QDialogButtonBox.ActionRole)
        self.resume_button.setObjectName("primary")
        self.resume_button.setDefault(True)
        self.restart_button = buttons.addButton("Baştan başlat", QDialogButtonBox.ActionRole)
        self.cancel_button = buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        self.resume_button.clicked.connect(lambda: self.choose("resume"))
        self.restart_button.clicked.connect(lambda: self.choose("restart"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose(self, choice):
        self.choice = choice
        self.accept()


class HistoryDialog(QDialog):
    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("İzleme geçmişini temizle")
        layout = QVBoxLayout(self)
        note = text_label("Son izlenenler listesini temizler. Favorilerin ve kaynakların korunur.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.scope = QComboBox()
        self.scope.setAccessibleName("Temizlenecek geçmiş")
        self.scope.addItem("Tüm kaynaklar", None)
        if source:
            self.scope.addItem(source["name"], source["id"])
            self.scope.setCurrentIndex(1)
        layout.addWidget(self.scope)
        self.reset_positions = QCheckBox("Devam etme konumlarını da sıfırla")
        layout.addWidget(self.reset_positions)
        buttons = QDialogButtonBox()
        self.clear_button = buttons.addButton("Geçmişi temizle", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
