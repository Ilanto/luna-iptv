import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
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

from .accounts import bounded_timestamp, sanitize_profile
from .i18n import _, _n, format_date, format_number


def text_label(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    if name:
        label.setObjectName(name)
    return label


class AccountDialog(QDialog):
    refresh_requested = Signal()
    closed = Signal()

    def __init__(self, source_name, profile=None, parent=None, *, now=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(_("Luna IPTV · {source_name} hesabı").format(source_name=source_name))
        self.setMinimumWidth(500)
        self._now = now
        self.accepts_updates = True
        self._invalidated = False
        self.is_refreshing = False
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.addWidget(text_label(source_name, "heading"))
        intro = text_label(_("Sağlayıcının son hesap durumu"), "muted")
        layout.addWidget(intro)
        form = QFormLayout()
        self.status_value = text_label("")
        self.status_value.setObjectName("accountStatus")
        self.created_value = text_label("")
        self.expiry_value = text_label("")
        self.remaining_value = text_label("")
        self.connections_value = text_label("")
        self.checked_value = text_label("")
        for title, value in [
            (_("Durum"), self.status_value),
            (_("Hesap açılışı"), self.created_value),
            (_("Bitiş"), self.expiry_value),
            (_("Kalan"), self.remaining_value),
            (_("Bağlantılar"), self.connections_value),
            (_("Son kontrol"), self.checked_value),
        ]:
            value.setWordWrap(True)
            form.addRow(title, value)
        layout.addLayout(form)
        self.error_label = text_label("", "muted")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(_("Kapat"))
        self.refresh_button = buttons.addButton(_("Şimdi yenile"), QDialogButtonBox.ActionRole)
        self.refresh_button.setObjectName("primary")
        self.refresh_button.clicked.connect(self.refresh_requested)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.render(profile)

    def render(self, profile) -> None:
        self.error_label.clear()
        if profile is None:
            self.status_value.setText(_("Bilinmiyor"))
            self.status_value.setProperty("state", "unknown")
            self.created_value.setText(_("Bilinmiyor"))
            self.expiry_value.setText(_("Bilinmiyor"))
            self.remaining_value.setText(_("Bilinmiyor · sağlayıcı tarih vermedi."))
            self.connections_value.setText(_("Bilinmiyor / Bilinmiyor · son kontrolde"))
            self.checked_value.setText(_("Henüz başarılı kontrol yok."))
            return

        profile = sanitize_profile(profile)

        labels = {
            "active": _("Aktif"),
            "expired": _("Süresi dolmuş"),
            "disabled": _("Devre dışı"),
            "banned": _("Engellenmiş"),
            "unknown": _("Bilinmiyor"),
        }
        self.status_value.setText(labels.get(profile.status, _("Bilinmiyor")))
        self.status_value.setProperty("state", profile.status)
        self.status_value.style().unpolish(self.status_value)
        self.status_value.style().polish(self.status_value)
        self.created_value.setText(self._date(profile.created_at))
        self.expiry_value.setText(self._date(profile.expires_at))
        self.remaining_value.setText(self._remaining(profile.expires_at))
        active = (
            _("Bilinmiyor")
            if profile.active_connections is None
            else str(profile.active_connections)
        )
        maximum = (
            _("Bilinmiyor") if profile.max_connections is None else str(profile.max_connections)
        )
        self.connections_value.setText(
            _("{active} / {maximum} · son kontrolde").format(active=active, maximum=maximum)
        )
        self.checked_value.setText(self._date(profile.checked_at))

    def _date(self, timestamp) -> str:
        timestamp = bounded_timestamp(timestamp)
        if timestamp is None:
            return _("Bilinmiyor")
        try:
            return format_date(datetime.fromtimestamp(timestamp).astimezone(), include_time=True)
        except (OSError, OverflowError, ValueError):
            return _("Bilinmiyor")

    def _remaining(self, expires_at) -> str:
        expires_at = bounded_timestamp(expires_at)
        if expires_at is None:
            return _("Bilinmiyor · sağlayıcı tarih vermedi.")
        now = self._now if self._now is not None else datetime.now().timestamp()
        seconds = expires_at - now
        if seconds <= 0:
            return _("Süre dolmuş.")
        days = max(1, math.ceil(seconds / 86_400))
        months = days / 30
        month_text = format_number(months, decimals=1)
        return _n(
            "{days} gün · yaklaşık {month_text} ay",
            "{days} günler · yaklaşık {month_text} ay",
            days,
        ).format(days=days, month_text=month_text)

    def set_refreshing(self, refreshing: bool) -> None:
        self.is_refreshing = refreshing
        self.refresh_button.setEnabled(not refreshing)
        self.refresh_button.setText(_("Yenileniyor…") if refreshing else _("Şimdi yenile"))

    def show_error(self, message: str) -> None:
        self.error_label.setText(
            _("Yenileme başarısız: {message} Son bilinen durum korundu.").format(message=message)
        )

    def _invalidate(self) -> None:
        if self._invalidated:
            return
        self._invalidated = True
        self.accepts_updates = False
        self.closed.emit()

    def done(self, result: int) -> None:
        self._invalidate()
        super().done(result)

    def reject(self) -> None:
        self._invalidate()
        super().reject()

    def accept(self) -> None:
        self._invalidate()
        super().accept()

    def closeEvent(self, event) -> None:
        self._invalidate()
        super().closeEvent(event)


class SourceDialog(QDialog):
    def __init__(self, parent=None, location="", source=None):
        super().__init__(parent)
        self._source = dict(source) if source is not None else None
        editing = self._source is not None
        self.setWindowTitle(
            _("Luna IPTV · Kaynağı düzenle") if editing else _("Luna IPTV · Kaynak ekle")
        )
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        layout.addWidget(text_label(_("Kendi yayın dünyanı ekle"), "heading"))
        intro = text_label(
            _(
                "M3U listeni ya da sağlayıcı hesabını bağla.\nKaynakların yalnızca bu bilgisayarda saklanır."
            ),
            "muted",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("Örn. Evdeki yayınlar"))
        self.name.setAccessibleName(_("Kaynak adı"))
        form = QFormLayout()
        form.addRow(_("Kaynak adı"), self.name)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        page = QWidget()
        form = QFormLayout(page)
        row = QHBoxLayout()
        self.location = QLineEdit(location)
        self.location.setPlaceholderText(_("https://… veya yerel M3U dosyası"))
        browse = QPushButton(_("Dosya seç"))
        browse.clicked.connect(self.browse)
        row.addWidget(self.location)
        row.addWidget(browse)
        form.addRow(_("Liste"), row)
        self.epg = QLineEdit()
        self.epg.setPlaceholderText(_("XMLTV adresi veya dosyası · isteğe bağlı"))
        form.addRow(_("Program rehberi"), self.epg)
        self.tabs.addTab(page, _("M3U listesi"))
        page = QWidget()
        form = QFormLayout(page)
        self.host = QLineEdit()
        self.host.setPlaceholderText(_("https://sunucu:port"))
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow(_("Sunucu"), self.host)
        form.addRow(_("Kullanıcı"), self.username)
        form.addRow(_("Şifre"), self.password)
        self.tabs.addTab(page, _("Xtream hesabı"))
        page = QWidget()
        form = QFormLayout(page)
        self.direct = QLineEdit()
        self.direct.setPlaceholderText(_("HTTP(S), RTSP, UDP adresi veya video dosyası"))
        choose = QPushButton(_("Video seç"))
        choose.clicked.connect(self.browse_video)
        row = QHBoxLayout()
        row.addWidget(self.direct)
        row.addWidget(choose)
        form.addRow(_("Yayın"), row)
        self.tabs.addTab(page, _("Tek yayın / dosya"))
        note = text_label(
            _(
                "Hesap bilgileri ve yayın adresleri kullanıcıya özel yerel veritabanında\nsaklanır; disk üzerinde ayrıca şifrelenmez."
            ),
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.feedback = text_label("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText(_("Vazgeç"))
        save = buttons.addButton(
            _("Bağlantıyı doğrula ve kaydet") if editing else _("Kaynağı ekle"),
            QDialogButtonBox.AcceptRole,
        )
        save.setObjectName("primary")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if editing:
            source_type = self._source.get("type", "m3u")
            mode = {"m3u": 0, "xtream": 1, "direct": 2}.get(source_type, 0)
            self.tabs.setCurrentIndex(mode)
            for index in range(self.tabs.count()):
                self.tabs.setTabEnabled(index, index == mode)
            self.name.setText(self._source.get("name", ""))
            self.name.setEnabled(False)
            if mode == 0:
                self.location.setText(self._source.get("location", ""))
                self.epg.setText(self._source.get("epg_url", ""))
            elif mode == 1:
                self.host.setText(self._source.get("location", ""))
                self.username.setText(self._source.get("username", ""))
                self.password.setText(self._source.get("password", ""))
            else:
                self.direct.setText(self._source.get("location", ""))
        elif location:
            self.name.setText(Path(location).stem)
            if Path(location).suffix.lower() not in (".m3u", ".m3u8"):
                self.tabs.setCurrentIndex(2)
                self.direct.setText(location)

    def browse(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, _("M3U listesi seç"), "", _("Yayın listeleri (*.m3u *.m3u8);;Tüm dosyalar (*)")
        )
        if path:
            self.location.setText(path)
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def browse_video(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            _("Video seç"),
            "",
            _("Videolar (*.mp4 *.mkv *.ts *.webm *.mov);;Tüm dosyalar (*)"),
        )
        if path:
            self.direct.setText(path)
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def validate(self):
        data = self.source()
        if not data["name"] or not data["location"]:
            self.feedback.setText(_("Kaynağa bir ad ver ve adresini ya da dosyasını gir."))
            return
        if data["type"] == "xtream" and (not data["username"] or not data["password"]):
            self.feedback.setText(_("Kullanıcı adı ve şifre gerekli."))
            return
        self.accept()

    def source(self):
        mode = self.tabs.currentIndex()
        result = {
            "name": self.name.text().strip(),
            "type": ["m3u", "xtream", "direct"][mode],
            "location": [self.location.text(), self.host.text(), self.direct.text()][mode].strip(),
            "username": self.username.text().strip() if mode == 1 else "",
            "password": self.password.text() if mode == 1 else "",
            "epg_url": self.epg.text().strip() if mode == 0 else "",
        }
        if self._source is not None:
            result["id"] = self._source["id"]
        return result


class GuideDialog(QDialog):
    def __init__(self, current="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Program rehberi"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label(_("XMLTV program rehberi"), "heading"))
        self.location = QLineEdit(current)
        self.location.setPlaceholderText(_("https://…/epg.xml.gz veya yerel XMLTV dosyası"))
        layout.addWidget(self.location)
        choose = QPushButton(_("Dosya seç"))
        layout.addWidget(choose)
        choose.clicked.connect(self.browse)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(_("Kaydet"))
        buttons.button(QDialogButtonBox.Cancel).setText(_("Vazgeç"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, _("XMLTV seç"), "", _("Program rehberi (*.xml *.xmltv *.gz);;Tüm dosyalar (*)")
        )
        if path:
            self.location.setText(path)
