from __future__ import annotations

from .. import window as _window


class LifecycleMixin:
    """Dispatch shared actions and manage window shutdown."""

    def status(self, message, retry=None, *, icon=None):
        if self.tv_mode is not None:
            self.tv_mode.notify(message)
        self.mini_status.setText(message)
        self.mini_status.setToolTip(message)
        self.message.setText(message)
        self._retry = retry
        self.retry_button.setVisible(retry is not None)
        self._sync_message_bar()
        if retry is not None or not self.recovery_cancel_button.isHidden():
            self.toast.dismiss(immediate=True)
        elif not message.startswith("Hazır"):
            self.toast.show_message(message, icon=icon)

    def _sync_message_bar(self):
        actions = self._retry is not None or not self.recovery_cancel_button.isHidden()
        self.message_bar.setVisible(actions and not self.fullscreen.active)

    def retry(self):
        if self._retry:
            self._retry()

    def start_session(self):
        if self._closed:
            return
        self.choose_profile_at_start()
        self.restore_last_channel()
        # Only the real app asks GitHub; tests build windows without a session.
        _window.QTimer.singleShot(20000, self.updates.check)

    def shortcut_action(self, key, callback):
        if isinstance(_window.QApplication.focusWidget(), _window.QLineEdit) and key not in (
            "Ctrl+O",
            "Ctrl+F",
            "Escape",
        ):
            return
        if key in ("Right", "Left") and not self._seekable:
            return
        self.fullscreen.reveal()
        callback()

    def quit_application(self):
        self._quitting = True
        self.close()

    @staticmethod
    def _session_ending():
        """Logout or shutdown in progress (the session manager is saving)."""
        app = _window.QGuiApplication.instance()
        saving = getattr(app, "isSavingSession", None)
        return bool(saving and saving())

    def closeEvent(self, event):
        self.close_tv_mode()
        self.close_multiview()
        if self._closed:
            event.accept()
            return
        # Logout or shutdown must really close, not hide in the tray.
        if not self._quitting and not self._session_ending() and self.tray.hide_on_close():
            event.ignore()
            return
        self.save_progress()
        self.watch_tracker.finish()
        self._watch_timer.stop()
        self.tray.close()
        self._closed = True
        self.watch_panel.animation.stop()
        self.page_transition.finish()
        self.toast.dismiss(immediate=True)
        self.channel_list.set_loading(False)
        self.idle_inhibit.close()
        self.mpris.close()
        self.reminder_service.close()
        self.recording_service.close()
        self.refresh_scheduler.close()
        self.new_episodes.close()
        self.kids_limits.close()
        self.mini_player.close()
        self._source_edit_tokens.clear()
        self._source_health_tokens.clear()
        self.dismiss_resume()
        self.details.close()
        self.subtitles.dismiss()
        self.track_preferences.finish()
        self.fullscreen.close()
        self.transport.close()
        self.recovery.close()
        self._guide_timer.stop()
        self.logo_viewport.close()
        self.home_view.close_artwork()
        self.logos.close()
        self.posters.close()
        self.player.shutdown()
        self.store.close()
        event.accept()
