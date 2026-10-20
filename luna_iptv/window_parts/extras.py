from __future__ import annotations

from .. import window as _window
from ..i18n import _


class ExtrasMixin:
    """Connect recordings, reminders and alternate viewing modes."""

    def open_multiview(self, channel=None):
        """Open a separate live grid; close_current saves progress before stopping."""
        if self._closed or (channel is not None and channel.kind != "live"):
            return None
        if self.kids_profile():
            # Watch time and bedtime are counted on the main player only.
            self.status(_("Çoklu izleme çocuk profilinde kapalı."))
            return None
        created = self.multiview is None
        if created:
            self.multiview = _window.MultiViewWindow(self)
            self.multiview.closed.connect(self._multiview_closed)
        view = self.multiview
        if channel is not None and not view.add_channel(channel):
            if created and _window.isValid(view):
                view.close()
            return None
        if created and self.current is not None:
            self.close_current()
            self.status(_("Çoklu izleme açıldı; ana oynatıcı bant genişliği için durduruldu."))
        view.show()
        view.raise_()
        view.activateWindow()
        return view

    def _multiview_closed(self):
        self.multiview = None

    def close_multiview(self):
        if self.multiview is not None:
            self.multiview.close()

    def _add_reminder_menu(self, menu, channel):
        index = self._guide_index.get(channel.id.split(":", 1)[0])
        upcoming = index.upcoming(channel.tvg_id, 20) if index and channel.kind == "live" else []
        if upcoming:
            reminders = menu.addMenu(_("Hatırlatıcı kur"))
            for programme in upcoming:
                label = f"{programme.start.astimezone():%d.%m %H:%M} · {programme.title}"
                reminders.addAction(
                    label.replace("&", "&&"),
                    lambda programme=programme: self.remind_programme(channel, programme),
                )
        menu.addAction(_("Hatırlatıcılar…"), self.open_reminders)

    def record_current(self):
        if not self.recording_service.available:
            self.status(_("Kayıt için ffmpeg gerekli. ffmpeg kurup yeniden dene."))
            return
        if not self.current or self.current.kind != "live" or not self._playback_active:
            return
        channel = self.current
        programme = self.programme_now(channel)
        if programme is None:
            minutes, accepted = _window.QInputDialog.getInt(
                self, _("Kaydet"), _("Kayıt süresi (dakika)"), 60, 1, 1440
            )
            if not accepted:
                return
            now = _window.datetime.now(_window.timezone.utc)
            programme = _window.Programme(
                channel.tvg_id, _("Canlı yayın"), now, now + _window.timedelta(minutes=minutes), ""
            )
        self.record_programme(channel, programme)

    def record_programme(self, channel, programme):
        try:
            identity = self.recording_service.add(channel, programme)
        except ValueError as error:
            self.status(str(error))
            return None
        if identity is not None:
            item = next(r for r in self.store.recordings() if r["id"] == identity)
            self.status(
                item["message"]
                or (_("Kayıt başladı.") if item["status"] == "running" else _("Kayıt planlandı."))
            )
        return identity

    def _recording_allowed(self, item, prompt):
        channel = next((c for c in self.store.channels() if c.id == item["channel_id"]), None)
        # Missing source metadata cannot safely establish the original lock policy.
        if channel is None:
            return not self.kids_profile() and (
                not prompt or self.guard(_("Kaydı açmak için PIN gir."))
            )
        if prompt:
            return self.unlock_channel(channel)
        return not (self.kids_profile() and channel.id in self.model.locked)

    def play_recording(self, item):
        if not self._recording_allowed(item, True):
            return
        path = _window.Path(item["path"])
        if not path.is_file():
            self.status(_("Kayıt dosyası bulunamadı."))
            return
        channel = _window.Channel(
            f"recording:{item['id']}",
            item["title"],
            path.resolve().as_uri(),
            kind="movie",
            group=_("Kayıtlar"),
            parental_id=item["channel_id"],
        )
        self.request_play(channel, approved=True)

    def open_recordings(self):
        if self._recordings_dialog is None or not _window.isValid(self._recordings_dialog):
            self._recordings_dialog = _window.RecordingsDialog(
                self.recording_service, self.play_recording, self._recording_allowed, self
            )
        self._recordings_dialog.refresh()
        self._recordings_dialog.show()
        self._recordings_dialog.raise_()
        self._recordings_dialog.activateWindow()
        return self._recordings_dialog

    def play_catchup(self, channel, programme):
        if not _window.can_catchup(channel, programme):
            self.status(_("Bu program sağlayıcının geçmiş yayın aralığında değil."))
            return
        source = self.source_for(channel)
        if not source or source["type"] != "xtream" or not self.unlock_channel(channel):
            return
        client = _window.XtreamClient(source["location"], source["username"], source["password"])
        duration = _window.math.ceil((programme.end - programme.start).total_seconds() / 60)
        url = client.timeshift_url(
            _window.unquote(channel.provider_key[5:]), programme.start, duration
        )
        item = _window.Channel(
            f"catchup:{channel.id}:{int(programme.start.timestamp())}",
            programme.title,
            url,
            kind="movie",
            group=_("Geçmiş yayın"),
            headers=dict(channel.headers),
            parental_id=channel.id,
        )
        self.request_play(item, approved=True)

    def remind_programme(self, channel, programme):
        try:
            reminder_id = self.reminder_service.add(channel, programme)
        except ValueError as error:
            self.status(str(error))
            return None
        if reminder_id is not None:
            self.status(
                _("Hatırlatıcı kuruldu: {title}, {time:%H:%M}").format(
                    title=programme.title, time=programme.start.astimezone()
                ),
                icon="check",
            )
        return reminder_id

    def _reminders_changed(self):
        """Rebuild the guide rows if the guide is on screen."""
        if self.library_pages.currentWidget() is self.guide_view:
            self.refresh_guide_view()

    def cancel_reminder(self, reminder_id):
        self.reminder_service.remove(reminder_id)

    def open_reminders(self):
        if self._reminders_dialog is None or not _window.isValid(self._reminders_dialog):
            self._reminders_dialog = _window.RemindersDialog(
                self.reminder_service, self.store, self
            )
        self._reminders_dialog.refresh()
        self._reminders_dialog.show()
        self._reminders_dialog.raise_()
        self._reminders_dialog.activateWindow()
        return self._reminders_dialog

    def _watch_reminder(self, channel_id):
        if self._closed:
            return
        channel = next((item for item in self.store.channels() if item.id == channel_id), None)
        if channel is None:
            self.status(_("Bu kanal artık kaynakta bulunmuyor."))
            return
        self.leave_mini_player()
        _window.RemoteControl(self).raise_window()
        self.request_play(channel)

    def toggle_tv_mode(self):
        """Lend the existing video stack to the couch interface."""
        if self.tv_mode is not None:
            self.close_tv_mode()
            return
        if not self.kids_limits.check():
            return
        self.close_multiview()
        self.leave_mini_player()
        self.details.dismiss()
        self.dismiss_resume()
        self.tv_mode = _window.TvModeWindow(self)
        self.tv_mode.closed.connect(self._tv_mode_closed)
        self.tv_mode.showFullScreen()

    def _tv_mode_closed(self):
        self.tv_mode = None

    def close_tv_mode(self):
        if self.tv_mode is not None:
            self.tv_mode.close()

    def toggle_mini_player(self):
        self.close_tv_mode()
        if self.mini_player.active or self.mini_player.pending:
            self.leave_mini_player()
        elif self._fullscreen:
            self.mini_player.enter_after_fullscreen(
                self._fullscreen_return_geometry, self._fullscreen_return_maximized
            )
            self.toggle_fullscreen()
        else:
            self.mini_player.enter()

    def leave_mini_player(self):
        if self._fullscreen:
            self.toggle_fullscreen()
        self.mini_player.leave()
        # Late fullscreen acknowledgements must restore the latest windowed mode.
        self._fullscreen_return_maximized = self.isMaximized()
        self._fullscreen_return_geometry = (
            self.normalGeometry() if self.isMaximized() else self.geometry()
        )

    def toggle_fullscreen(self):
        if not self._fullscreen:
            self.mini_player.cancel_pending()
            self._fullscreen_return_maximized = self.isMaximized()
            self._fullscreen_return_geometry = (
                self.normalGeometry() if self.isMaximized() else self.geometry()
            )
        self._fullscreen = not self._fullscreen
        self.video_stack.updateGeometry()
        if self.mini_player.active:
            self.mini_player.set_fullscreen(self._fullscreen)
        else:
            self.fullscreen.set_active(self._fullscreen)
        self.showFullScreen() if self._fullscreen else self._restore_windowed_state()

    def _restore_windowed_state(self):
        self.showNormal()
        if self.mini_player.active:
            self.mini_player.restore_mini_geometry()
        elif hasattr(self, "_fullscreen_return_geometry"):
            self.setGeometry(self._fullscreen_return_geometry)
            if self._fullscreen_return_maximized:
                self.showMaximized()
        self.watch_panel.sync(animate=False)

    def leave_fullscreen(self):
        if self._fullscreen:
            self.toggle_fullscreen()
        elif self.mini_player.active or self.mini_player.pending:
            self.leave_mini_player()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == _window.QEvent.PaletteChange:
            self._refresh_theme_details()
        if (
            event.type() == _window.QEvent.WindowStateChange
            and hasattr(self, "_fullscreen")
            and self.isFullScreen() != self._fullscreen
        ):
            # Wayland configure acknowledgements can arrive after a newer F/Esc
            # request. Reconcile on the next event turn, outside Qt's state update.
            _window.QTimer.singleShot(0, self._reconcile_fullscreen)

    def _reconcile_fullscreen(self):
        if self._closed or self.isFullScreen() == self._fullscreen:
            return
        self.showFullScreen() if self._fullscreen else self._restore_windowed_state()
