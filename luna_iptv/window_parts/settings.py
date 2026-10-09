from __future__ import annotations

from .. import window as _window
from ..i18n import _, _n, format_date


class SettingsMixin:
    """Open settings, source menus, backups and updates."""

    def _refresh_theme_details(self):
        """Regenerate the few theme-coloured assets stored outside paint methods."""
        if hasattr(self, "guide_mark"):
            self.guide_mark.setPixmap(
                _window.icons.pixmap("guide", _window.theme.ACCENT, 16, self.devicePixelRatioF())
            )
        if not hasattr(self, "_hidden_category_count"):
            return
        self.hidden_categories_label.setText(
            _n(
                '{count} kategori gizli · <a href="edit" style="color: {color}; text-decoration: none;">Düzenle</a>',
                '{count} kategoriler gizli · <a href="edit" style="color: {color}; text-decoration: none;">Düzenle</a>',
                self._hidden_category_count,
            ).format(count=self._hidden_category_count, color=_window.theme.ACCENT)
        )

    def open_settings(self):
        if not self.guard(_("Ayarları açmak için PIN gir.")):
            return
        if self._settings_dialog is None or not _window.isValid(self._settings_dialog):
            self._settings_dialog = _window.SettingsDialog(
                self.store, self, tray_available=self.tray.available
            )
            self._settings_dialog.refresh_changed.connect(self._wake_refresh)
            self._settings_dialog.timeshift_changed.connect(self._reload_live_cache)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def source_menu(self):
        menu = self.build_source_menu()
        menu.exec(self.cursor().pos())

    def build_source_menu(self):
        source_id = self.source_combo.currentData()
        source = next((s for s in self.store.sources() if s["id"] == source_id), None)
        menu = _window.QMenu(self)
        if self.updates.available:
            menu.addAction(
                _("Yeni sürüm: Luna {version} · İndir").format(version=self.updates.available[0]),
                self.open_update_page,
            )
            menu.addSeparator()
        rename = menu.addAction(_("Seçili kaynağı yeniden adlandır"))
        rename.setEnabled(source is not None and not self._busy)
        rename.triggered.connect(lambda: self.rename_source(source))
        edit = menu.addAction(_("Bağlantıyı düzenle"))
        edit.setEnabled(source is not None and not self._busy)
        edit.triggered.connect(lambda: self.edit_source(source))
        refresh = menu.addAction(_("Seçili kaynağı yenile"))
        refresh.setEnabled(source is not None and not self._busy)
        refresh.triggered.connect(lambda: self.import_source(source))
        check = menu.addAction(_("Bağlantıyı kontrol et"))
        check.setEnabled(source is not None)
        check.triggered.connect(lambda: self.check_source(source))
        if source is not None:
            snapshot = self.store.source_health(source["id"])
            if snapshot is not None:
                status, checked_at = snapshot
                label = {
                    "available": _("Ulaşılabilir"),
                    "responding": _("Sunucu yanıt veriyor"),
                    "unverified": _("Akış doğrulanmadı"),
                    "unavailable": _("Ulaşılamıyor"),
                }.get(status, _("Bilinmiyor"))
                moment = format_date(_window.datetime.fromtimestamp(checked_at), include_time=True)
                last_check = menu.addAction(
                    _("Son kontrol: {label} · {moment}").format(label=label, moment=moment)
                )
                last_check.setEnabled(False)
            else:
                last_check = menu.addAction(_("Son kontrol: Henüz kontrol edilmedi"))
                last_check.setEnabled(False)
        if source is not None and source["type"] == "xtream":
            account = menu.addAction(_("Hesap durumu"))
            account.triggered.connect(lambda: self.open_account(source))
        remove = menu.addAction(_("Seçili kaynağı kaldır"))
        remove.setEnabled(source is not None and not self._busy)
        remove.triggered.connect(lambda: self.remove_source(source))
        menu.addSeparator()
        backup = menu.addAction(_("Yedekle…"), self.export_backup)
        backup.setEnabled(not self._busy)
        restore = menu.addAction(_("Yedekten geri yükle…"), self.restore_backup)
        restore.setEnabled(not self._busy)
        menu.addSeparator()
        menu.addAction(_("Kısayollar ve hakkında"), self.about)
        menu.addAction(_("Hatırlatıcılar…"), self.open_reminders)
        menu.addAction(_("Kayıtlar…"), self.open_recordings)
        return menu

    def export_backup(self):
        from ..backup_dialog import save_backup_dialog

        if not self.guard(_("Yedek almak için PIN gir.")):
            return
        save_backup_dialog(self)

    def restore_backup(self):
        from ..backup_dialog import restore_backup_dialog

        if not self.guard(_("Yedekten geri yüklemek için PIN gir.")):
            return
        restore_backup_dialog(self)

    def update_found(self, version, page):
        self.status(
            _("Luna {version} çıktı. Kaynak menüsünden indirebilirsin.").format(version=version)
        )

    def open_update_page(self):
        if self.updates.available:
            _window.QDesktopServices.openUrl(_window.QUrl(self.updates.available[1]))

    def about(self):
        _window.QMessageBox.information(
            self,
            "Luna IPTV",
            _(
                "Luna IPTV {version}\nÖzgün, kişisel Linux IPTV istemcisi.\n\nCtrl+O  Kaynak ekle\nCtrl+F  Ara\nBoşluk  Oynat / duraklat\nF  Tam ekran\nM  Sesi aç / kapat\n← / →  5 saniye sar\nJ / L  Geri / ileri tara: 2×–16×\nK  Normal oynatmaya dön\nPage Up / Page Down  Önceki / sonraki kanal\n0–9  Kanal numarasıyla geç\nEsc  Tam ekrandan çık\n\nQt + libmpv · Native Wayland ve X11\nHesaplar yalnızca yerel diskte saklanır."
            ).format(version=_window.__version__),
        )
