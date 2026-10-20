"""Private online settings and asynchronous verification controls."""

from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton

from .dialogs import text_label
from .i18n import _
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
        section = self._section(layout, _("ÇEVRİMİÇİ BİLGİ"))
        for key, title in (
            ("tmdb_api_key", _("TMDB API anahtarı")),
            ("opensubtitles_api_key", _("OpenSubtitles API anahtarı")),
            ("opensubtitles_username", _("OpenSubtitles kullanıcı adı (isteğe bağlı)")),
            ("opensubtitles_password", _("OpenSubtitles şifresi (isteğe bağlı)")),
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
            show.setText(_("Göster"))
            show.setAccessibleName(_("{title}: göster veya gizle").format(title=title))
            show.setCheckable(True)
            show.toggled.connect(
                lambda checked, edit=field, button=show: self._show_secret(edit, button, checked)
            )
            self.show_buttons[key] = show
            row.addWidget(show)
            if key.endswith("api_key"):
                button = QToolButton()
                button.setObjectName("onlineControl")
                button.setText(_("Dene"))
                button.setAccessibleName(_("{title}: dene").format(title=title))
                button.setEnabled(bool(field.text().strip()))
                button.clicked.connect(lambda _=False, name=key: self._verify_online(name))
                self.verify_buttons[key] = button
                row.addWidget(button)
                result = text_label("", "muted")
                result.setAccessibleName(_("{title}: doğrulama sonucu").format(title=title))
                self.verify_labels[key] = result
                row.addWidget(result)
            section.addLayout(row)
        self.info_language_combo = self._choice(
            section, _("Bilgi dili"), "info_language", INFO_LANGUAGE_CHOICES
        )
        note = text_label(
            _("Anahtarlar yalnız bu bilgisayarda saklanır ve yedeğe girmez."), "faint"
        )
        note.setWordWrap(True)
        section.addWidget(note)

    @staticmethod
    def _show_secret(field, button, visible):
        field.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        button.setText(_("Gizle") if visible else _("Göster"))

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
