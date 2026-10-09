from __future__ import annotations

from .. import window as _window


class LibraryMixin:
    """Browse channels, categories, favorites and folders."""

    def refresh_library(self, select_source=None):
        previous = select_source if select_source is not None else self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("Tüm kaynaklar", "")
        for source in self.store.sources():
            self.source_combo.addItem(source["name"], source["id"])
        index = self.source_combo.findData(previous)
        self.source_combo.setCurrentIndex(max(index, 0))
        self.source_combo.blockSignals(False)
        self.proxy.source = self.source_combo.currentData() or ""
        self.model.reset(
            _window.ordered_channels(self.store.channels(), self.store.category_prefs()),
            self.store.favorites(),
            self.store.progress_map(),
        )
        self.model.set_unseen_series(self.store.unseen_series())
        if self.store.pin_hash():
            self.store.lock_adult_groups()  # new adult categories from this catalogue
        self.apply_locks(filter_now=False)
        self.proxy.set_recent_ids(self.store.recent_ids())
        if self.current:
            stored_current = next((c for c in self.model.channels if c.id == self.current.id), None)
            if self.current.parental_id:
                self._current_persistent = True
            elif stored_current is None:
                self._current_persistent = False
                self.favorite_button.setEnabled(False)
            else:
                self.current = stored_current
                self._current_persistent = True
            self.video_title.setText(self.current.name)
            self.update_guide()
        self.refresh_categories()
        self.filter_changed()
        self.details.invalidate()
        has_channels = bool(self.model.channels)
        self.welcome_title.setText("Yayının hazır." if has_channels else "Ekran senin.")
        self.welcome_subtitle.setText(
            "Soldan bir yayın seç.\nİzleme alanın burada."
            if has_channels
            else "Kendi listeni ekle. Sevdiğin yayını seç.\nGerisini Luna’ya bırak."
        )
        self.welcome_action.setText("Başka kaynak ekle" if has_channels else "İlk kaynağını ekle")
        self.refresh_home()
        self._reminders_changed()  # the visible guide must not keep removed channels

    def refresh_home(self):
        if not self._closed and self.library_pages.currentWidget() is self.home_view:
            self.home_view.refresh()

    def set_section(self, section):
        if section == "guide":
            self.show_guide()
            return
        target = self.home_view if section == "home" else self.browse
        changed = self.library_pages.currentWidget() is not target or (
            target is self.browse and self.proxy.section != section
        )
        self.page_transition.begin(changed)
        if section == "home":
            for key, button in self.nav_buttons.items():
                button.setChecked(key == "home")
            self.library_pages.setCurrentWidget(self.home_view)
            self.refresh_home()
            self.page_transition.end()
            return
        self.library_pages.setCurrentWidget(self.browse)
        if section != "favorites":
            self._folder_id = None
            self.proxy.folder_ids = None
        self.proxy.section = section
        self.history_clear_button.setVisible(section == "recent")
        for key, b in self.nav_buttons.items():
            b.setChecked(key == section)
        self.section_title.setText(
            {
                "live": "Canlı TV",
                "movie": "Filmler",
                "series": "Diziler",
                "favorites": "Favoriler",
                "recent": "Son izlenenler",
            }[section]
        )
        self.search.clear()
        self.proxy.set_recent_ids(self.store.recent_ids())
        self.refresh_categories()
        self.filter_changed()
        self.page_transition.end()

    def source_changed(self):
        self.proxy.source = self.source_combo.currentData() or ""
        self.refresh_categories()
        self.filter_changed()

    def refresh_categories(self):
        self.proxy.set_category_prefs(self.store.category_prefs())
        current = self.category.currentData() or ""
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("Tüm kategoriler", "")
        hidden = self.model.locked if self.proxy.hide_locked else frozenset()
        counts = _window.Counter()
        positions = {}
        hidden_groups = set()
        personal = self.proxy.section in ("favorites", "recent")
        for c in self.model.channels:
            if (
                c.id in hidden
                or (self.proxy.source and not c.id.startswith(self.proxy.source + ":"))
                or (not personal and c.kind != self.proxy.section)
                or (c.series_id and c.kind == "movie")
            ):
                continue
            key = self.proxy.category_key(c)
            if not personal and self.proxy.category_hidden(c):
                hidden_groups.add(key)
                continue
            if not c.group:
                continue
            counts[c.group] += 1
            position = self.proxy.category_positions.get(key) if not personal else None
            if position is not None:
                positions[c.group] = min(positions.get(c.group, position), position)
        groups = sorted(
            counts,
            key=lambda g: (g not in positions, positions.get(g, 0), -counts[g], g.casefold()),
        )
        for group in groups:
            self.category.addItem(group, group)
        index = self.category.findData(current)
        self.category.setCurrentIndex(index if index >= 0 else 0)
        self.category.blockSignals(False)
        self.category_bar.edit_button.setVisible(not personal)
        self.category_bar.set_items(
            [(group, group, counts[group]) for group in groups],
            self.category.currentData() or "",
        )
        self.category_bar.edit_button.setEnabled(bool(self.store.sources()))
        self._hidden_category_count = len(hidden_groups)
        self._refresh_theme_details()
        self.refresh_folders()

    def edit_categories(self, *_):
        if self.proxy.section not in ("live", "movie", "series"):
            return
        if not self.guard("Kategori düzenini değiştirmek için PIN gir."):
            return
        dialog = _window.CategoryEditor(self.store, self.proxy.source, self.proxy.section, self)
        if dialog.exec() == _window.QDialog.Accepted:
            self.refresh_library()  # reorders from the catalogue, so a reset restores it
        dialog.deleteLater()

    def refresh_folders(self):
        folders = self.store.folders()
        if self._folder_id not in {folder_id for folder_id, _ in folders}:
            self._folder_id = None
        self.proxy.folder_ids = (
            self.store.folder_items(self._folder_id) if self._folder_id is not None else None
        )
        favorites = {
            c.id
            for c in self.model.channels
            if c.id in self.model.favorites
            and not (self.proxy.hide_locked and c.id in self.model.locked)
            and (not self.proxy.source or c.id.startswith(self.proxy.source + ":"))
        }
        self.folder_bar.set_items(
            [
                (str(folder_id), name, len(self.store.folder_items(folder_id) & favorites))
                for folder_id, name in folders
            ],
            str(self._folder_id) if self._folder_id is not None else "",
            total=len(favorites),
        )
        for value, button in self.folder_bar.buttons().items():
            if value:
                button.setContextMenuPolicy(_window.Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(
                    lambda pos, folder_id=int(value), anchor=button: self.folder_context_menu(
                        folder_id, anchor.mapToGlobal(pos)
                    )
                )

    def choose_folder(self, value):
        self._folder_id = int(value) if value else None
        self.refresh_folders()
        self.filter_changed()

    def create_folder(self, channel=None):
        name, accepted = _window.QInputDialog.getText(self, "Yeni klasör", "Klasör adı:")
        if not accepted:
            return
        try:
            folder_id = self.store.create_folder(name)
        except ValueError as error:
            _window.QMessageBox.warning(self, "Klasör oluşturulamadı", str(error))
            return
        if channel is not None:
            self.set_folder_membership(folder_id, channel, True)
        else:
            self.refresh_folders()
            self.filter_changed()
        self.status(f"“{name.strip()}” klasörü oluşturuldu.", icon="check")
        return folder_id

    def rename_folder(self, folder_id):
        name = dict(self.store.folders()).get(folder_id)
        if name is None:
            return
        name, accepted = _window.QInputDialog.getText(
            self, "Klasörü yeniden adlandır", "Klasör adı:", _window.QLineEdit.Normal, name
        )
        if not accepted:
            return
        try:
            self.store.rename_folder(folder_id, name)
        except ValueError as error:
            _window.QMessageBox.warning(self, "Klasör yeniden adlandırılamadı", str(error))
            return
        self.refresh_folders()

    def delete_folder(self, folder_id):
        name = dict(self.store.folders()).get(folder_id)
        if name is None:
            return
        if (
            _window.QMessageBox.question(
                self,
                "Klasörü sil",
                f"“{name}” klasörü silinsin mi?\nFavorilerin korunacak.",
                _window.QMessageBox.Yes | _window.QMessageBox.No,
                _window.QMessageBox.No,
            )
            != _window.QMessageBox.Yes
        ):
            return
        self.store.delete_folder(folder_id)
        self.refresh_folders()
        self.filter_changed()

    def build_folder_menu(self, folder_id):
        menu = _window.QMenu(self)
        menu.addAction("Yeniden adlandır…", lambda: self.rename_folder(folder_id))
        menu.addAction("Sil", lambda: self.delete_folder(folder_id))
        return menu

    def folder_context_menu(self, folder_id, position):
        menu = self.build_folder_menu(folder_id)
        menu.exec(position)
        menu.deleteLater()

    def build_channel_menu(self, channel):
        menu = _window.QMenu(self)
        favorite = channel.id in self.store.favorites()
        menu.addAction(
            "Favorilerden çıkar" if favorite else "Favorilere ekle",
            lambda: self.toggle_channel_favorite(channel),
        )
        folders = menu.addMenu("Klasöre ekle")
        memberships = self.store.folders_of(channel.id)
        for folder_id, name in self.store.folders():
            action = folders.addAction(name)
            action.setCheckable(True)
            action.setChecked(folder_id in memberships)
            action.triggered.connect(
                lambda checked, fid=folder_id: self.set_folder_membership(fid, channel, checked)
            )
        folders.addSeparator()
        folders.addAction("Yeni klasör…", lambda: self.create_folder(channel))
        self._add_reminder_menu(menu, channel)
        if channel.kind == "live":
            menu.addAction("Çoklu izlemeye ekle", lambda: self.open_multiview(channel))
        if self.store.pin_hash() and not self.kids_profile():
            menu.addSeparator()
            if channel.id in self.store.locked_channels():
                menu.addAction("Kilidi kaldır…", lambda: self.set_channel_locked(channel, False))
            elif channel.id not in self.model.locked:
                menu.addAction("Kilitle", lambda: self.set_channel_locked(channel, True))
        return menu

    def channel_context_menu(self, position):
        channel = self.channel_list.indexAt(position).data(_window.Qt.UserRole)
        if channel is None:
            return
        menu = self.build_channel_menu(channel)
        menu.exec(self.channel_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def set_folder_membership(self, folder_id, channel, member):
        self.store.set_in_folder(folder_id, channel.id, member)
        self.refresh_favorites()

    def choose_category(self, group):
        index = self.category.findData(group)
        self.category.setCurrentIndex(index if index >= 0 else 0)
        self.category_bar.set_current(self.category.currentData() or "")

    def choose_search_kind(self, kind):
        if kind == "guide":
            # Programmes live in the Rehber page: carry the words there.
            query = self.search.text().strip()
            self.set_section("guide")
            self.guide_view.search.setText(query)
            return
        self.proxy.kind = kind
        self.filter_changed()

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().casefold().strip()
        favorites = self.proxy.section == "favorites"
        self.proxy.group = "" if favorites else self.category.currentData() or ""
        searching = bool(self.proxy.query)
        if not searching:
            self.proxy.kind = ""
        self.proxy.refresh()
        count = self.proxy.rowCount()
        if searching:
            counts = self.proxy.search_counts()
            kinds = [
                ("live", "Canlı", counts["live"]),
                ("movie", "Film", counts["movie"]),
                ("series", "Dizi", counts["series"]),
            ]
            programmes = self.programme_hits(self.search.text())
            if programmes:
                kinds.append(("guide", "Rehberde", programmes))
            self.kind_bar.set_items(kinds, self.proxy.kind)
            self.count_label.setText(f"{count:,} sonuç".replace(",", "."))
        else:
            self.count_label.setText(f"{count:,} yayın".replace(",", "."))
        self.category_bar.setVisible(not searching and not favorites)
        self.hidden_categories_label.setVisible(
            bool(self._hidden_category_count)
            and not searching
            and self.proxy.section in ("live", "movie", "series")
        )
        self.folder_row.setVisible(favorites)
        self.kind_bar.setVisible(searching)
        kind = self.proxy.kind if searching else self.proxy.section
        self.channel_list.set_poster_mode(kind in ("movie", "series"))
        loading = self.channel_list.loading
        self.no_results.setVisible(count == 0 and not loading)
        self.channel_list.setVisible(count > 0 or loading)
        self.no_results.setText(
            "Aramana uygun yayın yok.\nFiltreleri değiştirebilirsin."
            if self.model.channels
            else "Henüz yayın yok.\nBir kaynak ekleyerek başla."
        )

    def activate_index(self, index):
        self.open_channel(index.data(_window.Qt.UserRole))

    def open_channel(self, channel):
        """Open a channel through the shared playback or PIN-guarded detail path."""
        if not channel:
            return
        if channel.kind == "live":
            self.details.dismiss()
            self.request_play(channel)
        elif self.unlock_channel(channel):
            self.details.open(channel)

    def _proxy_row(self, channel_id):
        for row in range(self.proxy.rowCount()):
            if self.proxy.index(row, 0).data(_window.Qt.UserRole).id == channel_id:
                return row
        return None

    def confirm_clear_history(self):
        if self._history_dialog is not None and _window.isValid(self._history_dialog):
            self._history_dialog.raise_()
            return
        source = next(
            (s for s in self.store.sources() if s["id"] == self.source_combo.currentData()), None
        )
        dialog = _window.HistoryDialog(source, self)
        self._history_dialog = dialog

        def finished(result):
            if self._closed or self._history_dialog is not dialog:
                return
            self._history_dialog = None
            if result == _window.QDialog.Accepted:
                self.clear_history(
                    dialog.scope.currentData(), reset_progress=dialog.reset_positions.isChecked()
                )

        dialog.finished.connect(finished)
        dialog.open()

    def clear_history(self, source_id=None, *, reset_progress=False):
        self.store.clear_history(source_id, reset_progress=reset_progress)
        if self.current and (source_id is None or self.current.id.startswith(source_id + ":")):
            self._record_recent = False
            if reset_progress:
                self._record_progress = False
        if reset_progress:
            self.dismiss_resume()
            self.model.replace_progress(self.store.progress_map())
        self.proxy.set_recent_ids(self.store.recent_ids())
        self.filter_changed()
        self.refresh_home()
        self.status(
            "İzleme geçmişi temizlendi."
            + (
                " Devam etme konumları sıfırlandı."
                if reset_progress
                else " Kaldığın yerler korundu."
            )
        )

    def toggle_favorite(self):
        if not self.current or not self._current_persistent:
            return
        self.toggle_channel_favorite(self.current)

    def toggle_channel_favorite(self, channel):
        favorite = channel.id not in self.store.favorites()
        self.store.set_favorite(channel.id, favorite)
        self.refresh_favorites()
        self.status(
            f"{channel.name} favorilere eklendi."
            if favorite
            else f"{channel.name} favorilerden çıkarıldı.",
            icon="check",
        )

    def refresh_favorites(self):
        self.model.favorites = self.store.favorites()
        if self.model.rowCount():
            self.model.dataChanged.emit(
                self.model.index(0), self.model.index(self.model.rowCount() - 1)
            )
        if self.current:
            favorite = self.current.id in self.model.favorites
            self.favorite_button.setText("★" if favorite else "☆")
        self.refresh_home()
        self.details.refresh_favorite()
        self.refresh_folders()
        self.filter_changed()
