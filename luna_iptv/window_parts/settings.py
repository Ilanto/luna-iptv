from __future__ import annotations

from .. import window as _window


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
            f'{self._hidden_category_count} kategori gizli · <a href="edit" style="color: {_window.theme.ACCENT};'
            f' text-decoration: none;">Düzenle</a>'
        )

    def open_settings(self):
        if not self.guard("Ayarları açmak için PIN gir."):
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
                f"Yeni sürüm: Luna {self.updates.available[0]} · İndir", self.open_update_page
            )
            menu.addSeparator()
        rename = menu.addAction("Seçili kaynağı yeniden adlandır")
        rename.setEnabled(source is not None and not self._busy)
        rename.triggered.connect(lambda: self.rename_source(source))
        edit = menu.addAction("Bağlantıyı düzenle")
        edit.setEnabled(source is not None and not self._busy)
        edit.triggered.connect(lambda: self.edit_source(source))
        refresh = menu.addAction("Seçili kaynağı yenile")
        refresh.setEnabled(source is not None and not self._busy)
        refresh.triggered.connect(lambda: self.import_source(source))
        check = menu.addAction("Bağlantıyı kontrol et")
        check.setEnabled(source is not None)
        check.triggered.connect(lambda: self.check_source(source))
        if source is not None:
            snapshot = self.store.source_health(source["id"])
            if snapshot is not None:
                status, checked_at = snapshot
                label = {
                    "available": "Ulaşılabilir",
                    "responding": "Sunucu yanıt veriyor",
                    "unverified": "Akış doğrulanmadı",
                    "unavailable": "Ulaşılamıyor",
                }.get(status, "Bilinmiyor")
                moment = _window.datetime.fromtimestamp(checked_at).strftime("%d.%m.%Y %H:%M")
                last_check = menu.addAction(f"Son kontrol: {label} · {moment}")
                last_check.setEnabled(False)
            else:
                last_check = menu.addAction("Son kontrol: Henüz kontrol edilmedi")
                last_check.setEnabled(False)
        if source is not None and source["type"] == "xtream":
            account = menu.addAction("Hesap durumu")
            account.triggered.connect(lambda: self.open_account(source))
        remove = menu.addAction("Seçili kaynağı kaldır")
        remove.setEnabled(source is not None and not self._busy)
        remove.triggered.connect(lambda: self.remove_source(source))
        menu.addSeparator()
        backup = menu.addAction("Yedekle…", self.export_backup)
        backup.setEnabled(not self._busy)
        restore = menu.addAction("Yedekten geri yükle…", self.restore_backup)
        restore.setEnabled(not self._busy)
        menu.addSeparator()
        menu.addAction("Kısayollar ve hakkında", self.about)
        menu.addAction("Hatırlatıcılar…", self.open_reminders)
        menu.addAction("Kayıtlar…", self.open_recordings)
        return menu

    def export_backup(self):
        from ..backup_dialog import save_backup_dialog

        if not self.guard("Yedek almak için PIN gir."):
            return
        save_backup_dialog(self)

    def restore_backup(self):
        from ..backup_dialog import restore_backup_dialog

        if not self.guard("Yedekten geri yüklemek için PIN gir."):
            return
        restore_backup_dialog(self)

    def update_found(self, version, page):
        self.status(f"Luna {version} çıktı. Kaynak menüsünden indirebilirsin.")

    def open_update_page(self):
        if self.updates.available:
            _window.QDesktopServices.openUrl(_window.QUrl(self.updates.available[1]))

    def about(self):
        _window.QMessageBox.information(
            self,
            "Luna IPTV",
            f"Luna IPTV {_window.__version__}\nÖzgün, kişisel Linux IPTV istemcisi.\n\nCtrl+O  Kaynak ekle\nCtrl+F  Ara\nBoşluk  Oynat / duraklat\nF  Tam ekran\nM  Sesi aç / kapat\n← / →  5 saniye sar\nJ / L  Geri / ileri tara: 2×–16×\nK  Normal oynatmaya dön\nPage Up / Page Down  Önceki / sonraki kanal\n0–9  Kanal numarasıyla geç\nEsc  Tam ekrandan çık\n\nQt + libmpv · Native Wayland ve X11\nHesaplar yalnızca yerel diskte saklanır.",
        )
