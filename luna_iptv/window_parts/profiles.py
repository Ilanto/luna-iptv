from __future__ import annotations

from .. import window as _window


class ProfilesMixin:
    """Switch profiles and enforce parental controls."""

    def guard(self, reason):
        """Ask for the PIN when one is set; every time, nothing is remembered."""
        return _window.ask_pin(self.store, reason, self.tv_mode or self)

    def kids_profile(self):
        profile = self.store.profile(self.store.profile_id)
        return bool(profile and profile["kids"])

    def show_kids_limit(self, reason):
        if reason:
            self.close_tv_mode()
            self.details.dismiss()
            self.dismiss_resume()
            self.mini_player.cancel_pending()
            self.mini_player.leave()
            self.fullscreen.set_active(False)
        self.centralWidget().setEnabled(not reason)
        self.kids_limit_panel.show_reason(reason)

    def extend_kids_time(self):
        if not self.store.pin_hash():
            self.status("Süre eklemek için Ebeveyn denetiminden bir PIN belirle.")
            return
        profile_id = self.store.profile_id
        if not self.guard("Süre eklemek için ebeveyn PIN'ini gir."):
            return
        choice, accepted = _window.QInputDialog.getItem(
            self, "Bugün için süre ekle", "Ek süre", ["15 dk", "30 dk", "60 dk"], 0, False
        )
        if accepted and self.store.profile_id == profile_id:
            self.kids_limits.extend(int(choice.split()[0]), lambda: True)

    def pick_limit_profile(self):
        picker = _window.ProfilePicker(self.store.profiles(), self.store.profile_id, self)
        if picker.exec() == _window.QDialog.Accepted:
            self.switch_profile(picker.chosen)

    def unlock_channel(self, channel, *, approved=False):
        """May this channel open now? approved: the PIN was just asked for it."""
        if (channel.parental_id or channel.id) not in self.model.locked:
            return True
        if self.kids_profile():
            self.status("Bu içerik bu profilde kapalı.")
            return False
        return approved or self.guard(f"“{channel.name}” kilitli. Açmak için PIN gir.")

    def locked_ids(self):
        """Channels behind the PIN: locked one by one or through their category."""
        if not self.store.pin_hash():
            return set()
        channels = self.store.locked_channels()
        groups = self.store.locked_groups()
        return {
            c.id
            for c in self.model.channels
            if c.id in channels or (c.id.split(":", 1)[0], c.group) in groups
        }

    def apply_locks(self, *, filter_now=True):
        self.model.set_locked(self.locked_ids())
        self.proxy.hide_locked = self.kids_profile() and bool(self.model.locked)
        if filter_now:
            self.refresh_categories()
            self.filter_changed()
            self._reminders_changed()
            self.refresh_home()
        if self.current and self.kids_profile():
            origin = self.current.parental_id or self.current.id
            missing_origin = self.current.parental_id and not any(
                c.id == origin for c in self.model.channels
            )
            if origin in self.model.locked or missing_origin:
                self.close_current()

    def set_channel_locked(self, channel, locked):
        if not locked and not self.guard(f"“{channel.name}” kilidini kaldırmak için PIN gir."):
            return
        self.store.set_channel_locked(channel.id, locked)
        self.apply_locks()
        self.status(f"“{channel.name}” kilitlendi." if locked else "Kilit kaldırıldı.")

    def open_parental(self):
        if not self.guard("Ebeveyn denetimini açmak için PIN gir."):
            return
        dialog = _window.ParentalDialog(self.store, self)
        dialog.changed.connect(self.apply_locks)
        dialog.changed.connect(self.refresh_profile_badge)
        dialog.exec()

    def open_profiles(self):
        if not self.guard("Profilleri yönetmek için PIN gir."):
            return
        dialog = _window.ProfilesDialog(self.store, self)
        dialog.deleting.connect(self._deleting_profile)
        dialog.changed.connect(self.profiles_changed)
        dialog.exec()

    def profiles_changed(self):
        """A profile was edited or deleted; the active one may have changed under us."""
        self.load_profile()

    def refresh_profile_badge(self):
        self.profile_button.set_profile(self.store.profile(self.store.profile_id))

    def profile_menu(self):
        menu = self.build_profile_menu()
        menu.exec(self.profile_button.mapToGlobal(self.profile_button.rect().topRight()))
        menu.deleteLater()

    def build_profile_menu(self):
        menu = _window.QMenu(self)
        for profile in self.store.profiles():
            action = menu.addAction(profile["name"])
            action.setCheckable(True)
            action.setChecked(profile["id"] == self.store.profile_id)
            action.triggered.connect(
                lambda checked=False, pid=profile["id"]: self.switch_profile(pid)
            )
        menu.addSeparator()
        menu.addAction("TV modu (F11)", self.toggle_tv_mode)
        menu.addAction("Çoklu izleme", self.open_multiview)
        menu.addAction("İstatistikler", self.open_statistics)
        menu.addAction("Profilleri yönet…", self.open_profiles)
        menu.addAction("Ebeveyn denetimi…", self.open_parental)
        menu.addAction("Kayıtlar…", self.open_recordings)
        return menu

    def open_statistics(self):
        self.watch_tracker.flush()
        if self._statistics_dialog is not None and _window.isValid(self._statistics_dialog):
            self._statistics_dialog.close()
        self._statistics_dialog = _window.StatisticsDialog(self.store, self)
        self._statistics_dialog.show()

    def switch_profile(self, profile_id):
        """Leaving a kids profile or entering a protected one asks for the PIN."""
        target = self.store.profile(profile_id)
        if target is None or profile_id == self.store.profile_id:
            return False
        if (target["protected"] or self.kids_profile()) and not self.guard(
            f"“{target['name']}” profiline geçmek için PIN gir."
        ):
            return False
        self.leave_profile()
        self.store.use_profile(profile_id)
        self.load_profile()
        self.status(f"{target['name']} profili açık.", icon="check")
        return True

    def leave_profile(self):
        """Before another profile becomes active: save and stop what this one watches."""
        self.close_tv_mode()
        self.close_multiview()
        if self._statistics_dialog is not None and _window.isValid(self._statistics_dialog):
            self._statistics_dialog.close()
        self.details.dismiss()  # an open detail card was unlocked for this profile only
        if self.current is not None:
            self.close_current()

    def _deleting_profile(self, profile_id):
        if profile_id == self.store.profile_id:
            self.leave_profile()

    def load_profile(self):
        """Reload everything personal: favorites, folders, history, reminders, locks."""
        if self._statistics_dialog is not None and _window.isValid(self._statistics_dialog):
            self._statistics_dialog.close()
        self.details.dismiss()
        self.reminder_service.reload()
        self.recording_service.tick()
        if self._recordings_dialog is not None and _window.isValid(self._recordings_dialog):
            self._recordings_dialog.refresh()
        self.refresh_library()
        self.refresh_favorites()
        self.refresh_profile_badge()
        self.kids_limits.reload()

    def choose_profile_at_start(self):
        """'Kim izliyor?' when there is more than one profile; protected ones need the PIN."""
        profiles = self.store.profiles()
        if len(profiles) > 1:
            picker = _window.ProfilePicker(profiles, self.store.profile_id, self)
            if picker.exec() == _window.QDialog.Accepted and picker.chosen != self.store.profile_id:
                if self.switch_profile(picker.chosen):
                    return self.store.profile_id
        return self.enter_active_profile()

    def enter_active_profile(self):
        """The profile left active last time; a protected one asks before it opens."""
        profile = self.store.profile(self.store.profile_id)
        if not (profile["protected"] and self.store.pin_hash()):
            return profile["id"]
        if self.guard(f"“{profile['name']}” profiline girmek için PIN gir."):
            return profile["id"]
        fallback = next((p for p in self.store.profiles() if not p["protected"]), None)
        if fallback is None:
            self.quit_application()  # every profile is protected and the PIN was not given
            return None
        self.store.use_profile(fallback["id"])
        self.load_profile()
        self.status(f"{fallback['name']} profili açık.", icon="check")
        return fallback["id"]
