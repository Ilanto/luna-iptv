from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


def text_label(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    if name:
        label.setObjectName(name)
    return label


class SourceDialog(QDialog):
    def __init__(self, parent=None, location=""):
        super().__init__(parent)
        self.setWindowTitle("Luna IPTV · Kaynak ekle")
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        layout.addWidget(text_label("Kendi yayın dünyanı ekle", "heading"))
        intro = text_label(
            "M3U listeni ya da sağlayıcı hesabını bağla.\nKaynakların yalnızca bu bilgisayarda saklanır.",
            "muted",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Örn. Evdeki yayınlar")
        self.name.setAccessibleName("Kaynak adı")
        form = QFormLayout()
        form.addRow("Kaynak adı", self.name)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        page = QWidget()
        form = QFormLayout(page)
        row = QHBoxLayout()
        self.location = QLineEdit(location)
        self.location.setPlaceholderText("https://… veya yerel M3U dosyası")
        browse = QPushButton("Dosya seç")
        browse.clicked.connect(self.browse)
        row.addWidget(self.location)
        row.addWidget(browse)
        form.addRow("Liste", row)
        self.epg = QLineEdit()
        self.epg.setPlaceholderText("XMLTV adresi veya dosyası · isteğe bağlı")
        form.addRow("Program rehberi", self.epg)
        self.tabs.addTab(page, "M3U listesi")
        page = QWidget()
        form = QFormLayout(page)
        self.host = QLineEdit()
        self.host.setPlaceholderText("https://sunucu:port")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Sunucu", self.host)
        form.addRow("Kullanıcı", self.username)
        form.addRow("Şifre", self.password)
        self.tabs.addTab(page, "Xtream hesabı")
        page = QWidget()
        form = QFormLayout(page)
        self.direct = QLineEdit()
        self.direct.setPlaceholderText("HTTP(S), RTSP, UDP adresi veya video dosyası")
        choose = QPushButton("Video seç")
        choose.clicked.connect(self.browse_video)
        row = QHBoxLayout()
        row.addWidget(self.direct)
        row.addWidget(choose)
        form.addRow("Yayın", row)
        self.tabs.addTab(page, "Tek yayın / dosya")
        note = text_label(
            "Hesap bilgileri ve yayın adresleri kullanıcıya özel yerel veritabanında\nsaklanır; disk üzerinde ayrıca şifrelenmez.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.feedback = text_label("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Vazgeç")
        save = buttons.addButton("Kaynağı ekle", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if location:
            self.name.setText(Path(location).stem)
            if Path(location).suffix.lower() not in (".m3u", ".m3u8"):
                self.tabs.setCurrentIndex(2)
                self.direct.setText(location)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "M3U listesi seç", "", "Yayın listeleri (*.m3u *.m3u8);;Tüm dosyalar (*)"
        )
        if path:
            self.location.setText(path)
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def browse_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Video seç", "", "Videolar (*.mp4 *.mkv *.ts *.webm *.mov);;Tüm dosyalar (*)"
        )
        if path:
            self.direct.setText(path)
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def validate(self):
        data = self.source()
        if not data["name"] or not data["location"]:
            self.feedback.setText("Kaynağa bir ad ver ve adresini ya da dosyasını gir.")
            return
        if data["type"] == "xtream" and (not data["username"] or not data["password"]):
            self.feedback.setText("Kullanıcı adı ve şifre gerekli.")
            return
        self.accept()

    def source(self):
        mode = self.tabs.currentIndex()
        return {
            "name": self.name.text().strip(),
            "type": ["m3u", "xtream", "direct"][mode],
            "location": [self.location.text(), self.host.text(), self.direct.text()][mode].strip(),
            "username": self.username.text().strip() if mode == 1 else "",
            "password": self.password.text() if mode == 1 else "",
            "epg_url": self.epg.text().strip() if mode == 0 else "",
        }


class GuideDialog(QDialog):
    def __init__(self, current="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Program rehberi")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label("XMLTV program rehberi", "heading"))
        self.location = QLineEdit(current)
        self.location.setPlaceholderText("https://…/epg.xml.gz veya yerel XMLTV dosyası")
        layout.addWidget(self.location)
        choose = QPushButton("Dosya seç")
        layout.addWidget(choose)
        choose.clicked.connect(self.browse)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "XMLTV seç", "", "Program rehberi (*.xml *.xmltv *.gz);;Tüm dosyalar (*)"
        )
        if path:
            self.location.setText(path)
