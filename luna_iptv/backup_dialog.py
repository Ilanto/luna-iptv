"""Backup options and the preview-before-restore flow."""

import sqlite3
from datetime import date

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
)

from . import theme
from .backup import (
    apply_backup,
    export_backup,
    read_backup,
    source_incomplete,
    validate_backup,
    write_backup,
)
from .dialogs import text_label
from .motion import set_motion_level
from .settings import MOTION_CHOICES, selected_setting

_FILTER = "Luna IPTV yedeği (*.luna-backup.json)"


class BackupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Luna IPTV · Yedekle")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(text_label("Kişisel kitaplığını yedekle", "heading"))
        scope = text_label(
            "Tüm profillerin favorileri, klasörleri, hatırlatıcıları, kategori tercihleri "
            "ve kilitleri dahil edilir. Ebeveyn PIN’i hiçbir zaman yedeklenmez.",
            "muted",
        )
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.credentials = QCheckBox("Hesap bilgilerini de dahil et")
        self.credentials.setChecked(False)
        layout.addWidget(self.credentials)
        warning = text_label(
            "Seçerseniz kullanıcı adı, parola ve erişim bilgisi içerebilen adresler "
            "dosyada düz metin olarak saklanır; şifrelenmez. "
            "Seçmezseniz bu adresler çıkarılır ve bağlantıyı daha sonra tamamlamanız gerekebilir.",
            "muted",
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.history = QCheckBox("İzleme ilerlemesini ve geçmişini dahil et")
        self.history.setChecked(True)
        layout.addWidget(self.history)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Yedekle")
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Vazgeç")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def save_backup_dialog(window):
    if window._busy:
        return
    dialog = BackupDialog(window)
    if dialog.exec() != QDialog.Accepted:
        return
    path, _ = QFileDialog.getSaveFileName(
        window, "Yedekle", f"luna-yedek-{date.today().isoformat()}.luna-backup.json", _FILTER
    )
    if not path:
        return
    try:
        data = export_backup(
            window.store,
            include_credentials=dialog.credentials.isChecked(),
            include_history=dialog.history.isChecked(),
        )
        write_backup(path, data)
    except ValueError as error:
        QMessageBox.warning(window, "Yedek oluşturulamadı", str(error))
        return
    except (OSError, sqlite3.Error):
        QMessageBox.warning(
            window,
            "Yedek oluşturulamadı",
            "Dosya yazılamadı. Disk alanını ve izinleri kontrol edin.",
        )
        return
    window.status("Yedek kaydedildi.", icon="check")


def restore_backup_dialog(window):
    if window._busy:
        return
    path, _ = QFileDialog.getOpenFileName(window, "Yedekten geri yükle", "", _FILTER)
    if not path:
        return
    try:
        data = read_backup(path)
        summary = validate_backup(data)
        credentials = "Var · düz metin" if summary.credentials_present else "Yok"
        history = str(summary.history) if data["history"] is not None else "Dahil edilmemiş"
        profile_summary = (
            f"Profil: {summary.profiles}\n"
            f"Hatırlatıcı: {summary.reminders}\n"
            f"Kategori tercihi: {summary.category_prefs}\n"
            f"Grup / kanal kilidi: {summary.group_locks} / {summary.channel_locks}\n"
            if data["version"] == 2
            else "Eski yedek: kişisel kayıtlar seçili profile aktarılır.\n"
        )
        message = (
            profile_summary + f"Kaynak: {summary.sources}\n"
            f"Favori: {summary.favorites}\n"
            f"Favori klasörü: {summary.favorite_folders}\n"
            f"Klasör üyeliği: {summary.folder_members}\n"
            f"Kaynağa özel oynatma tercihi: {summary.playback_preferences}\n"
            f"Uygulama ayarı: {summary.app_settings}\n"
            f"İzleme ilerlemesi / geçmişi: {history}\n"
            f"Hesap bilgileri / erişim içerebilen adresler: {credentials}\n"
            f"Bağlantısı eksik kaynak: {summary.incomplete_sources}\n\n"
            "Kayıtlar mevcut verilerle birleştirilecek. Eşleşen kayıtlar güncellenecek; "
            "yedekte bulunmayan veriler silinmeyecek.\n"
            "PIN yedekten aktarılmaz; bu cihazın mevcut PIN’i korunur.\n"
            "Eksik bağlantıları «Bağlantıyı düzenle» ile tamamlayın; "
            "katalogları «Seçili kaynağı yenile» ile indirin.\n\nGeri yüklensin mi?"
        )
        if (
            QMessageBox.question(
                window,
                "Yedeği geri yükle",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        if window._busy:
            window.status("Kaynak işlemi tamamlandıktan sonra yedeği yeniden açın.")
            return
        apply_backup(window.store, data)
    except ValueError as error:
        QMessageBox.warning(window, "Yedek geri yüklenemedi", str(error))
        return
    except (OSError, sqlite3.Error):
        QMessageBox.warning(
            window,
            "Yedek geri yüklenemedi",
            "Dosya okunamadı veya veriler kaydedilemedi. Mevcut veriler korundu.",
        )
        return
    window.load_profile()
    window._wake_refresh()
    set_motion_level(selected_setting(window.store, "motion_level", MOTION_CHOICES))
    app = QApplication.instance()
    if app is not None:  # a restored accent or base theme applies right away
        theme.apply_theme(app, window.store)
    restored_ids = {source["id"] for source in data["sources"]}
    incomplete = sum(
        bool(source_incomplete(source))
        for source in window.store.sources()
        if source["id"] in restored_ids
    )
    message = "Yedek geri yüklendi. Kataloglar için «Seçili kaynağı yenile»yi kullanın."
    if incomplete:
        message += f" {incomplete} kaynağın eksik bilgilerini «Bağlantıyı düzenle» ile tamamlayın."
    window.status(message)
