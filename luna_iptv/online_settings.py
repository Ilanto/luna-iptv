"""Private online settings and asynchronous verification controls."""

from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton

from .dialogs import text_label
from .settings import INFO_LANGUAGE_CHOICES
from .subtitles import OpenSubtitlesClient
from .tasks import Task
from .tmdb import TMDBClient


class OnlineSettings:
    """Own worker lifetimes through Qt receiver-bound slots on the settings dialog."""

    def _online_section(self, layout):
        self._online_tasks = {}
        self.online_fields = {}
        self.verify_buttons = {}
        self.verify_labels = {}
        self.show_buttons = {}
        section = self._section(layout, "ÇEVRİMİÇİ BİLGİ")
        for key, title in (
            ("tmdb_api_key", "TMDB API anahtarı"),
            ("opensubtitles_api_key", "OpenSubtitles API anahtarı"),
            ("opensubtitles_username", "OpenSubtitles kullanıcı adı (isteğe bağlı)"),
            ("opensubtitles_password", "OpenSubtitles şifresi (isteğe bağlı)"),
        ):
            section.addWidget(text_label(title, "muted"))
            row = QHBoxLayout()
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setMaxLength(1024)
            field.setEchoMode(QLineEdit.Password)
            field.setText(self.store.online_secret(key))
            field.textChanged.connect(lambda value, name=key: self._save_online(name, value))
            self.online_fields[key] = field
            row.addWidget(field, 1)
            show = QToolButton()
            show.setObjectName("onlineControl")
            show.setText("Göster")
            show.setAccessibleName(f"{title}: göster veya gizle")
            show.setCheckable(True)
            show.toggled.connect(
                lambda checked, edit=field, button=show: self._show_secret(edit, button, checked)
            )
            self.show_buttons[key] = show
            row.addWidget(show)
            if key.endswith("api_key"):
                button = QToolButton()
                button.setObjectName("onlineControl")
                button.setText("Dene")
                button.setAccessibleName(f"{title}: dene")
                button.setEnabled(bool(field.text().strip()))
                button.clicked.connect(lambda _=False, name=key: self._verify_online(name))
                self.verify_buttons[key] = button
                row.addWidget(button)
                result = text_label("", "muted")
                result.setAccessibleName(f"{title}: doğrulama sonucu")
                self.verify_labels[key] = result
                row.addWidget(result)
            section.addLayout(row)
        self.info_language_combo = self._choice(
            section, "Bilgi dili", "info_language", INFO_LANGUAGE_CHOICES
        )
        note = text_label("Anahtarlar yalnız bu bilgisayarda saklanır ve yedeğe girmez.", "faint")
        note.setWordWrap(True)
        section.addWidget(note)

    @staticmethod
    def _show_secret(field, button, visible):
        field.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        button.setText("Gizle" if visible else "Göster")

    def _save_online(self, key, value):
        self.store.set_online_secret(key, value)
        if key in self.verify_buttons:
            self.verify_buttons[key].setEnabled(
                bool(value.strip()) and key not in self._online_tasks
            )
            self.verify_labels[key].setText("")

    def _verify_online(self, key):
        value = self.online_fields[key].text().strip()
        if not value or key in self._online_tasks:
            return
        client = (TMDBClient if key == "tmdb_api_key" else OpenSubtitlesClient)(
            value, self.store.path.parent
        )
        task = Task(client.verify)
        task.signals.setProperty("online_key", key)
        task.signals.done.connect(self._online_verified)
        task.signals.failed.connect(self._online_failed)
        self._online_tasks[key] = (task, value)
        self.verify_buttons[key].setEnabled(False)
        self.verify_labels[key].setText("…")
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _online_verified(self, valid):
        self._finish_verification(bool(valid))

    @Slot(str)
    def _online_failed(self, _error):
        self._finish_verification(False)

    def _finish_verification(self, valid):
        key = self.sender().property("online_key")
        _task, value = self._online_tasks.pop(key)
        _task.signals.deleteLater()
        current = self.online_fields[key].text().strip()
        self.verify_buttons[key].setEnabled(bool(current))
        self.verify_labels[key].setText(("✓" if valid else "✗") if current == value else "")
