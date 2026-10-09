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
from .i18n import N_, _, _n
from .motion import set_motion_level
from .settings import MOTION_CHOICES, selected_setting

_FILTER = N_("Luna IPTV yedeği (*.luna-backup.json)")


class BackupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Luna IPTV · Yedekle"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(text_label(_("Kişisel kitaplığını yedekle"), "heading"))
        scope = text_label(
            _(
                "Tüm profillerin favorileri, klasörleri, hatırlatıcıları, kategori tercihleri ve kilitleri dahil edilir. Ebeveyn PIN’i hiçbir zaman yedeklenmez."
            ),
            "muted",
        )
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.credentials = QCheckBox(_("Hesap bilgilerini de dahil et"))
        self.credentials.setChecked(False)
        layout.addWidget(self.credentials)
        warning = text_label(
            _(
                "Seçerseniz kullanıcı adı, parola ve erişim bilgisi içerebilen adresler dosyada düz metin olarak saklanır; şifrelenmez. Seçmezseniz bu adresler çıkarılır ve bağlantıyı daha sonra tamamlamanız gerekebilir."
            ),
            "muted",
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.history = QCheckBox(_("İzleme ilerlemesini ve geçmişini dahil et"))
        self.history.setChecked(True)
        layout.addWidget(self.history)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(_("Yedekle"))
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText(_("Vazgeç"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def save_backup_dialog(window):
    if window._busy:
        return
    dialog = BackupDialog(window)
    if dialog.exec() != QDialog.Accepted:
        return
    path, _selected_filter = QFileDialog.getSaveFileName(
        window, _("Yedekle"), f"luna-yedek-{date.today().isoformat()}.luna-backup.json", _(_FILTER)
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
        QMessageBox.warning(window, _("Yedek oluşturulamadı"), str(error))
        return
    except (OSError, sqlite3.Error):
        QMessageBox.warning(
            window,
            _("Yedek oluşturulamadı"),
            _("Dosya yazılamadı. Disk alanını ve izinleri kontrol edin."),
        )
        return
    window.status(_("Yedek kaydedildi."), icon="check")


def restore_backup_dialog(window):
    if window._busy:
        return
    path, _selected_filter = QFileDialog.getOpenFileName(
        window, _("Yedekten geri yükle"), "", _(_FILTER)
    )
    if not path:
        return
    try:
        data = read_backup(path)
        summary = validate_backup(data)
        credentials = _("Var · düz metin") if summary.credentials_present else _("Yok")
        history = str(summary.history) if data["history"] is not None else _("Dahil edilmemiş")
        profile_summary = (
            _(
                "Profil: {profiles}\nHatırlatıcı: {reminders}\nKategori tercihi: {category_prefs}\nGrup / kanal kilidi: {group_locks} / {channel_locks}\n"
            ).format(
                profiles=summary.profiles,
                reminders=summary.reminders,
                category_prefs=summary.category_prefs,
                group_locks=summary.group_locks,
                channel_locks=summary.channel_locks,
            )
            if data["version"] == 2
            else _("Eski yedek: kişisel kayıtlar seçili profile aktarılır.\n")
        )
        message = profile_summary + _(
            "Kaynak: {sources}\nFavori: {favorites}\nFavori klasörü: {favorite_folders}\nKlasör üyeliği: {folder_members}\nKaynağa özel oynatma tercihi: {playback_preferences}\nUygulama ayarı: {app_settings}\nİzleme ilerlemesi / geçmişi: {history}\nHesap bilgileri / erişim içerebilen adresler: {credentials}\nBağlantısı eksik kaynak: {incomplete_sources}\n\nKayıtlar mevcut verilerle birleştirilecek. Eşleşen kayıtlar güncellenecek; yedekte bulunmayan veriler silinmeyecek.\nPIN yedekten aktarılmaz; bu cihazın mevcut PIN’i korunur.\nEksik bağlantıları «Bağlantıyı düzenle» ile tamamlayın; katalogları «Seçili kaynağı yenile» ile indirin.\n\nGeri yüklensin mi?"
        ).format(
            sources=summary.sources,
            favorites=summary.favorites,
            favorite_folders=summary.favorite_folders,
            folder_members=summary.folder_members,
            playback_preferences=summary.playback_preferences,
            app_settings=summary.app_settings,
            history=history,
            credentials=credentials,
            incomplete_sources=summary.incomplete_sources,
        )
        if (
            QMessageBox.question(
                window,
                _("Yedeği geri yükle"),
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        if window._busy:
            window.status(_("Kaynak işlemi tamamlandıktan sonra yedeği yeniden açın."))
            return
        apply_backup(window.store, data)
    except ValueError as error:
        QMessageBox.warning(window, _("Yedek geri yüklenemedi"), str(error))
        return
    except (OSError, sqlite3.Error):
        QMessageBox.warning(
            window,
            _("Yedek geri yüklenemedi"),
            _("Dosya okunamadı veya veriler kaydedilemedi. Mevcut veriler korundu."),
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
    message = _("Yedek geri yüklendi. Kataloglar için «Seçili kaynağı yenile»yi kullanın.")
    if incomplete:
        message += _n(
            " {incomplete} kaynağın eksik bilgilerini «Bağlantıyı düzenle» ile tamamlayın.",
            " {incomplete} kaynakların eksik bilgilerini «Bağlantıyı düzenle» ile tamamlayın.",
            incomplete,
        ).format(incomplete=incomplete)
    window.status(message)
