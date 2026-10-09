from __future__ import annotations

from .. import window as _window


class PlaybackMixin:
    """Control playback, transport and recovery."""

    def can_zap(self):
        return (
            self.current is not None and self.current.kind == "live" and self.proxy.rowCount() > 1
        )

    def zap(self, step):
        """Play the next (1) or previous (-1) live channel in the order the grid shows.

        Without a live channel playing, Page Up / Page Down keep scrolling the grid.
        """
        if not self.can_zap():
            bar = self.channel_list.verticalScrollBar()
            action = _window.QAbstractSlider.SliderAction
            bar.triggerAction(action.SliderPageStepAdd if step > 0 else action.SliderPageStepSub)
            return False
        row = self._proxy_row(self.current.id)
        if row is None:
            self.status("Bu kanal şu anki listede yok; önce listeden bir kanal seç.")
            return False
        rows = self.proxy.rowCount()
        for _ in range(rows - 1):
            row = (row + step) % rows
            channel = self.proxy.index(row, 0).data(_window.Qt.UserRole)
            if channel.kind == "live" and channel.id not in self.model.locked:
                index = self.proxy.index(row, 0)
                self.channel_list.setCurrentIndex(index)
                self.channel_list.scrollTo(index)
                self.request_play(channel)
                return True
        return False

    def restore_last_channel(self):
        """Select or play the last live channel according to the startup preference."""
        if self._closed or self.current is not None:
            return
        action = _window.selected_setting(self.store, "startup_action", _window.STARTUP_CHOICES)
        if action == "none":
            return
        live = {
            c.id for c in self.model.channels if c.kind == "live" and c.id not in self.model.locked
        }
        last = next((cid for cid in self.store.recent_ids(10_000) if cid in live), None)
        row = self._proxy_row(last) if last else None
        if row is None:
            return
        index = self.proxy.index(row, 0)
        self.channel_list.setCurrentIndex(index)
        self.channel_list.scrollTo(index, _window.QAbstractItemView.ScrollHint.PositionAtCenter)
        if self.library_pages.currentWidget() is self.home_view:
            self.home_view.select_channel(last)
        else:
            self.channel_list.setFocus()
        if action == "play":
            self.request_play(index.data(_window.Qt.UserRole))
            return
        self.welcome_subtitle.setText(
            f"Son izlediğin: {index.data(_window.Qt.UserRole).name}\nOynatmak için Enter'a bas."
        )

    def resume_position(self, channel):
        position, duration = self.store.progress(channel.id)
        return position if channel.kind != "live" and _window.resumable(position, duration) else 0

    def dismiss_resume(self):
        dialog, self._resume_dialog = self._resume_dialog, None
        if dialog is not None and _window.isValid(dialog):
            dialog.reject()

    def watch_new_episode(self, episode):
        """A desktop action must also respect the parent series' content lock."""
        source_id = episode.id.split(":", 1)[0]
        series = next(
            (
                c
                for c in self.store.channels(source_id)
                if c.kind == "series" and c.series_id == episode.series_id
            ),
            None,
        )
        if series is not None and self.unlock_channel(series):
            self.request_play(episode)

    def request_play(self, channel, *, preferences=None, approved=False, failover=False):
        """Play a channel; locked ones ask for the PIN every time (approved: already asked)."""
        tv_mode, profile_id = self.tv_mode, self.store.profile_id
        if not self.kids_limits.check():
            return
        if not self.unlock_channel(channel, approved=approved):
            return
        if tv_mode is not None and (
            self._closed or self.tv_mode is not tv_mode or self.store.profile_id != profile_id
        ):
            return
        if not failover:
            self._failover_tried = set()  # a person's own choice starts a new chain
        self.cancel_next_episode()
        self.dismiss_resume()
        preferences = (
            _window.normalize_preferences(preferences) if preferences is not None else None
        )
        position = self.resume_position(channel)
        if self.tv_mode is not None:
            self.play(channel, start_override=position, preferences=preferences)
            return
        if not position:
            self.play(channel, start_override=0, preferences=preferences)
            return
        dialog = _window.ResumeDialog(channel.name, _window.clock_text(position), self)
        self._resume_dialog = dialog

        def finished(result):
            if self._closed or self._resume_dialog is not dialog:
                return
            self._resume_dialog = None
            if result != _window.QDialog.Accepted:
                return
            fresh = next((c for c in self.model.channels if c.id == channel.id), None)
            if fresh is not None:
                self.play(
                    fresh,
                    start_override=position if dialog.choice == "resume" else 0,
                    preferences=preferences,
                )

        dialog.finished.connect(finished)
        dialog.open()

    def restart_current(self):
        if self.current and self._current_persistent and self.current.kind != "live":
            self.play(self.current, start_override=0)

    def _sync_playback_state(self):
        """Tell the desktop what is playing: the idle inhibit and media controls."""
        self.idle_inhibit.set_active(self._playback_active and not self._playback_paused)
        self._sync_mpris()
        self._wake_refresh()
        self.watch_tracker.set_running(
            self._playback_active
            and not self._playback_paused
            and not self._loading
            and not self._idle
            and not self._buffering
        )
        self.tray.refresh()

    def _sync_mpris(self):
        channel = self.current if self._playback_active else None
        if channel is None:
            self.mpris.update(status="Stopped")
            self.mpris.set_position(0)
            return
        programme = self.programme_now(channel)
        source = self.source_for(channel)
        artist = channel.name if programme else (source["name"] if source else "")
        live = channel.kind == "live"
        self.mpris.update(
            status="Paused" if self._playback_paused else "Playing",
            title=programme.title if programme else channel.name,
            artist=artist,
            art_url=channel.logo if channel.logo.startswith(("http://", "https://")) else "",
            length_us=None if live or self._duration <= 0 else round(self._duration * 1e6),
            can_seek=self._seekable,
            can_go_next=self.can_zap(),
            can_go_previous=self.can_zap(),
            track_id=_window.track_path(channel.id),
        )

    def play(self, channel, *, start_override=None, recovering=False, preferences=None):
        if not self.kids_limits.check():
            return
        self.subtitles.dismiss()
        if not channel.url:
            self.status("Bu bölüm yeniden alınmalı. Diziyi açıp bölüm listesini yenile.")
            return
        if (
            self.current
            and self.current.id == channel.id
            and self._loading
            and start_override is None
            and not recovering
        ):
            return
        if recovering and (
            self.current is None or self.current.id != channel.id or not self._current_persistent
        ):
            self.recovery.cancel()
            return
        self.dismiss_resume()
        self.save_progress()
        self.watch_tracker.begin(None if channel.parental_id else channel.id)
        self._buffering = False
        if not recovering:
            self.recovery.begin(channel.id, live=channel.kind == "live")
            self._record_recent = not bool(channel.parental_id)
            self._record_progress = not bool(channel.parental_id)
        start = self.resume_position(channel) if start_override is None else start_override
        persist_preferences = preferences is not None
        if preferences is None and self.current is not None and self.current.id == channel.id:
            preferences = self.track_preferences.current_choices()
        preserve_comfort = self.current is not None and self.current.id == channel.id
        self.current = channel
        self.watch_panel.sync()
        self._current_persistent = True
        self._position = float(start)
        # Retain known duration until mpv publishes metadata; an early close
        # after file-loaded must not erase a valid saved resume position.
        known_duration = self.store.progress(channel.id)[1]
        self._duration = (
            known_duration
            if channel.kind != "live"
            and _window.math.isfinite(known_duration)
            and known_duration > 0
            else 0.0
        )
        self._seekable = False
        self._last_saved = 0.0
        self._loading = True
        self._tracks = []
        source = self.source_for(channel)
        self.comfort_preferences.begin(source["id"] if source else None, preserve=preserve_comfort)
        track_options = self.track_preferences.begin(
            source["id"] if source else None,
            preferences=preferences,
            persist=persist_preferences,
        )
        self._language_notice_timer.stop()
        self.language_notice.hide()
        live_cache = (
            _window.cache_minutes(self.store.setting("timeshift_minutes", 30))
            if channel.kind == "live"
            else 0
        )
        self.transport.prepare(live=channel.kind == "live", backbuffer=bool(live_cache))
        self.media_info.begin_load()
        self.refresh_media_info()
        self.info_button.setEnabled(True)
        self.video_title.setText(channel.name)
        self.video_badge.setText(
            "CANLI YAYIN" if channel.kind == "live" else channel.group.upper() or "FİLM / VİDEO"
        )
        self.favorite_button.setEnabled(not bool(channel.parental_id))
        self.favorite_button.setText("★" if channel.id in self.store.favorites() else "☆")
        self.video_stack.setCurrentIndex(1)
        self.seek.setEnabled(False)
        self.time_label.setText("Bağlanıyor…")
        self.update_guide()
        self._playback_token = self.player.reserve_load()
        self._untracked_playback_token = None
        self._playback_active = self._playback_token is not None
        self._playback_paused = False
        self._sync_playback_state()
        self.recovery.watch(self._playback_token)
        self.status(
            "Yayın açılıyor…",
            lambda: self.play(self.current) if self.current and self._current_persistent else None,
        )
        self.refresh_recovery()
        if self._playback_token is not None:
            self.player.load(
                channel.url,
                channel.headers,
                start=start,
                track_options=track_options,
                live_cache_minutes=live_cache,
            )
            self.playback_started.emit(channel)
        source = self.source_for(channel)
        if source and source.get("epg_url") and source["id"] not in self._guide_data:
            self.load_guide(source)

    def _legacy_loaded(self):
        if not self._playback_active or self._untracked_playback_token != self._playback_token:
            return
        self.recovery.loaded(self._playback_token)
        self._mark_loaded()

    def playback_loaded(self, token):
        if token != self._playback_token or not self._playback_active:
            return
        self.recovery.loaded(token)
        self._mark_loaded()

    def loaded(self):
        """Update the loaded UI; native callbacks validate their token first."""
        self._mark_loaded()

    def _mark_loaded(self):
        if self._closed:
            return
        self.show_channel_banner()
        self._idle = False
        self._loading = False
        self.transport.loaded()
        self.track_preferences.loaded()
        self.subtitles.loaded()
        self._show_track_notice()
        self.media_info.mark_loaded()
        self.refresh_media_info()
        self.info_button.setEnabled(self.current is not None)
        # mpv only reports pause changes; a new file starting unpaused sends none.
        self.play_button.setText("▶" if self._playback_paused else "Ⅱ")
        self.status(self.recovery.message or "Yayın oynatılıyor.")
        self.comfort_preferences.loaded(volume=self.volume.value())
        self._sync_playback_state()
        self.save_progress()

    def playback_error(self, message):
        if self._closed:
            return
        self.status(message)

    def playback_tracking_lost(self, token):
        if self._closed or token != self._playback_token:
            return
        self._untracked_playback_token = token
        self.recovery.suppress_retries(token)

    def playback_property(self, token, name, value):
        if token != self._playback_token:
            return
        if name == "time-pos":
            self.recovery.progress(token, value)
        elif name == "pause":
            self._playback_paused = bool(value)
            self.recovery.paused(token, bool(value))
            self._sync_playback_state()
        elif name == "paused-for-cache":
            self._buffering = bool(value)
            self.watch_tracker.set_running(
                self._playback_active
                and not self._playback_paused
                and not self._loading
                and not self._idle
                and not self._buffering
            )
            self.recovery.buffering(token, bool(value))
        elif name == "track-list" and self._playback_active:
            self._update_tracks(value)

    def _update_tracks(self, value):
        self._tracks = value if isinstance(value, list) else []
        self.track_preferences.update_tracks(self._tracks)
        if not self._loading:
            self._show_track_notice()

    def _show_track_notice(self):
        if not self._closed and (notice := self.track_preferences.take_notice()):
            self.language_notice.setText(notice)
            self.language_notice.show()
            self._language_notice_timer.start()

    def playback_finished(self, token, reason, message):
        if token != self._playback_token or not self._playback_active:
            return
        recovery_handled = self.recovery.failure(token, reason)
        if self.current and self.current.kind != "live" and not self._loading:
            self.save_progress()
        self._finish_playback()
        if reason == "eof" and not self.sleep_timer.media_ended():
            self.offer_next_episode()
        if recovery_handled and self.recovery.state == "failed":
            self.status(
                self.recovery.message,
                lambda: (
                    self.play(self.current) if self.current and self._current_persistent else None
                ),
            )
        elif recovery_handled and self.recovery.message:
            self.status(self.recovery.message)
        elif message:
            retry = (
                (
                    lambda: (
                        self.play(self.current)
                        if self.current and self._current_persistent
                        else None
                    )
                )
                if reason == "error"
                and self.current
                and self._current_persistent
                and self.current.kind != "live"
                else None
            )
            self.status(message, retry)

    def _finish_playback(self, *, end_session=True):
        self.watch_tracker.set_running(False)
        if end_session:
            self.watch_tracker.finish()
            self._playback_active = False
            self._playback_paused = False
            self._sync_playback_state()
            self.track_preferences.finish()
        self._idle = True
        self._loading = False
        self.transport.finished()
        self.play_button.setText("▶")
        self.media_info.reset()
        self.refresh_media_info()
        self.fullscreen.set_info_visible(False)
        self.info_button.setEnabled(False)
        self.seek.setEnabled(False)

    def ended(self):
        if self._closed:
            return
        if self._playback_active and self._untracked_playback_token == self._playback_token:
            if self.current and self.current.kind != "live" and not self._loading:
                self.save_progress()
            self.recovery.failure(self._playback_token, "unknown")
            self._finish_playback()

    def player_property(self, name, value):
        if self._closed:
            return
        self.transport.observe(name, value)
        if (
            name == "paused-for-cache"
            and self._playback_active
            and self._untracked_playback_token == self._playback_token
        ):
            self._buffering = bool(value)
            self._sync_playback_state()
        if self.media_info.update(name, value):
            self.refresh_media_info()
        if name == "idle-active" and value and not self._loading:
            self.fullscreen.set_info_visible(False)
            self.info_button.setEnabled(False)
        if name == "time-pos" and value is not None:
            self._position = float(value)
            self.mpris.set_position(round(self._position * 1e6))
            if not self.seek.isSliderDown() and self._duration > 0:
                self.seek.setValue(round(1000 * self._position / self._duration))
            self.time_label.setText(
                _window.clock_text(self._position)
                + (
                    f" / {_window.clock_text(self._duration)}"
                    if self._duration > 0
                    else "  ·  CANLI"
                )
            )
            if abs(self._position - self._last_saved) >= 5:
                self.save_progress()
                self._last_saved = self._position
        elif name == "duration" and value is not None and float(value) > 0:
            self._duration = float(value)
            self.seek.setEnabled(self._seekable and self._duration > 0)
            self._sync_mpris()
        elif name == "seekable":
            self._seekable = bool(value)
            self.seek.setEnabled(self._seekable and self._duration > 0)
            self._sync_mpris()
        elif name == "seeking" and not value and self._playback_active:
            # A seek has landed: media controls learn the new position.
            self.mpris.seeked(round(self._position * 1e6))
        elif name == "pause":
            self.play_button.setText("▶" if value else "Ⅱ")
            if self._playback_active and self._untracked_playback_token == self._playback_token:
                # Players without playlist entry ids report pause only here.
                self._playback_paused = bool(value)
                self._sync_playback_state()
        elif name == "mute":
            self.mute_button.setText("Sessiz" if value else "Ses")
        elif name == "volume" and value is not None:
            self.volume.blockSignals(True)
            self.volume.setMaximum(max(100, self.comfort_preferences.values["boost"], round(value)))
            self.volume.setValue(round(value))
            self.volume.blockSignals(False)
        elif name == "track-list":
            if self._untracked_playback_token == self._playback_token:
                self._update_tracks(value)
        elif name == "idle-active":
            self._idle = bool(value)
            self._sync_playback_state()
        elif name == "paused-for-cache" and value:
            self.status("Yayın arabelleğe alınıyor…")
        elif (
            name == "paused-for-cache"
            and value is False
            and self.current
            and not self._loading
            and not self._idle
        ):
            self.status("Yayın oynatılıyor.")

        self.refresh_live_transport()

    def save_progress(self):
        if not self._closed:
            self.watch_tracker.flush()
        if self.current and self._current_persistent and not self._closed and self._record_progress:
            self.store.save_progress(
                self.current.id, self._position, self._duration, mark_recent=self._record_recent
            )
            self.model.set_progress(self.current.id, self._position, self._duration)
            self.refresh_home()

    def seek_to_slider(self):
        if window := self.transport.live_window:
            start, end = window
            self.transport.seek_absolute(start + self.seek.value() * (end - start) / 1000)
            return
        if self._duration > 0 and self._seekable:
            self.transport.cancel(restore_pause=True)
            self.player.command(["seek", self.seek.value() * self._duration / 1000, "absolute"])

    def toggle_play(self):
        if self.current and not self._current_persistent:
            self.status("Bu yayın artık kaynakta bulunmuyor. Listeden başka bir yayın seç.")
            return
        if self.current and self._idle:
            self.play(self.current)
        elif self.current and self.transport.rate:
            self.transport.normal_play()
        elif self.current:
            self.player.pause_toggle()

    def refresh_transport(self):
        if self._closed:
            return
        for widget in (self.seek_back_button, self.seek_forward_button):
            widget.setEnabled(self.transport.can_seek)
        for widget in (self.rewind_button, self.forward_button):
            widget.setEnabled(self.transport.can_scan)
        self.rewind_button.setChecked(self.transport.rate < 0)
        self.forward_button.setChecked(self.transport.rate > 0)
        self.rate_button.setText(self.transport.label)
        self.rate_button.setEnabled(bool(self.transport.rate))
        if self.transport.rate:
            self.play_button.setText("▶")
        self.refresh_live_transport()

    def refresh_live_transport(self):
        window = self.transport.live_window
        for button, direction in (
            (self.seek_back_button, "geri"),
            (self.seek_forward_button, "ileri"),
        ):
            button.caption = "10" if window else "5"
            button.setToolTip(f"{button.caption} saniye {direction}")
            button.setAccessibleName(button.toolTip())
            button.update()
        self.live_edge_button.setVisible(self.transport.behind_live)
        if not window:
            self.seek.setToolTip("")
            if self.current and self.current.kind == "live" and self.transport.timeshift_enabled:
                changed = self._seekable
                self._seekable = False
                self.seek.setEnabled(False)
                if changed:
                    self._sync_mpris()
            return
        start, end = window
        changed = self._seekable != self.transport.can_seek
        self._seekable = self.transport.can_seek
        if changed:
            self._sync_mpris()
        self.seek.setEnabled(self.transport.can_seek)
        if not self.seek.isSliderDown():
            self.seek.setValue(round(1000 * (self._position - start) / (end - start)))
        self.time_label.setText(f"−{_window.clock_text(end - self._position)} / CANLI")
        self.seek.setToolTip(f"Önbellek: {_window.clock_text(end - start)} · CANLI")

    def _reload_live_cache(self):
        if self.current and self.current.kind == "live" and self._playback_active:
            self.play(self.current, start_override=0)

    def close_current(self):
        """Stop and forget the current channel, back to the welcome screen."""
        self.channel_banner.hide_banner()
        self.stop_playback()
        self.current = None
        self.tray.refresh()
        self.watch_panel.sync()
        self._current_persistent = False
        self.favorite_button.setEnabled(False)
        self.video_stack.setCurrentIndex(0)
        self.video_title.setText("İyi bir yayına yer aç.")

    def stop_playback(self):
        self.subtitles.dismiss()
        self.cancel_next_episode()  # Stop, profile switches and closing end the countdown too
        self.dismiss_resume()
        self.save_progress()
        self.watch_tracker.finish()
        self.recovery.cancel()
        self._untracked_playback_token = None
        self._playback_active = False
        self._playback_paused = False
        self._sync_playback_state()
        self.track_preferences.finish()
        self._language_notice_timer.stop()
        self.language_notice.hide()
        self.transport.finished()
        self._idle = True
        self._loading = False
        self.media_info.reset()
        self.refresh_media_info()
        self.fullscreen.set_info_visible(False)
        self.info_button.setEnabled(False)
        self.player.stop()
        self.status("Yayın durduruldu.")
        self.seek.setEnabled(False)

    def cancel_recovery(self):
        self.stop_playback()

    def _retry_live(self, channel_id):
        if (
            self._closed
            or self.current is None
            or self.current.id != channel_id
            or not self._current_persistent
        ):
            self.recovery.cancel()
            return
        self.play(self.current, recovering=True)

    def alternative_channel(self, channel, tried=()):
        """The same live channel in another source: same guide id, else the same plain name."""
        if channel is None or channel.kind != "live":
            return None
        source = channel.id.split(":", 1)[0]
        name = _window.channel_key(channel.name)
        for candidate in self.model.channels:
            if (
                candidate.kind != "live"
                or candidate.id in tried
                or candidate.id.split(":", 1)[0] == source
                or candidate.id in self.model.locked
                or self.proxy.category_hidden(candidate)
            ):
                continue
            same_guide = channel.tvg_id and candidate.tvg_id == channel.tvg_id
            if same_guide or (name and _window.channel_key(candidate.name) == name):
                return candidate
        return None

    def _fail_over(self):
        """A live channel that will not open is tried once in each other source that has it."""
        failed = self.current
        tried = self._failover_tried | {failed.id}
        alternative = self.alternative_channel(failed, tried)
        if alternative is None:
            return False
        source = self.source_for(alternative)
        self._failover_tried = tried
        note = f"{failed.name} açılmadı; {source['name'] if source else 'diğer kaynak'} üzerinden açılıyor."

        def switch():
            self.request_play(alternative, failover=True)
            self.status(note)  # after play's own "bağlanılıyor" line, so it is the one seen

        _window.QTimer.singleShot(0, switch)
        return True

    def refresh_recovery(self):
        if self._closed:
            return
        terminal = self.recovery.state in {"failed", "untracked-failed"}
        if (
            terminal
            and self.current is not None
            and self.current.kind == "live"
            and self._playback_active
            and self._fail_over()
        ):
            self._finish_playback()
            return
        live_wait = (
            self.recovery.state == "waiting"
            and self.current is not None
            and self.current.kind == "live"
        )
        terminal_cleanup = terminal and (self._playback_active or self._loading or not self._idle)
        if terminal_cleanup or (live_wait and (self._loading or not self._idle)):
            self._finish_playback(end_session=terminal)
            if terminal:
                self.player.stop()
        self.recovery_cancel_button.setVisible(self.recovery.can_cancel)
        self.mini_cancel_button.setVisible(self.recovery.can_cancel)
        self._sync_message_bar()
        if self.recovery.message:
            retry = (
                (
                    lambda: (
                        self.play(self.current)
                        if self.current and self._current_persistent
                        else None
                    )
                )
                if self.recovery.state in {"failed", "untracked-failed"}
                else None
            )
            self.status(self.recovery.message, retry)

    def toggle_info_panel(self):
        self.fullscreen.toggle_info()

    def refresh_media_info(self):
        fields = {
            self.info_dimensions: self.media_info.dimensions,
            self.info_quality: self.media_info.quality,
            self.info_video_codec: self.media_info.video_codec,
            self.info_audio_codec: self.media_info.audio_codec,
            self.info_audio_layout: self.media_info.audio_layout,
            self.info_fps: self.media_info.fps,
            self.info_bitrate: self.media_info.bitrate,
            self.info_dynamic_range: self.media_info.dynamic_range,
        }
        for label, value in fields.items():
            if label.text() != value:
                label.setText(value)
        for label, kind in ((self.info_video_codec, "video"), (self.info_audio_codec, "audio")):
            description = self.media_info.codec_description(kind)
            label.setToolTip(description)
            label.setAccessibleDescription(description)
        buffer_text = self.media_info.buffer_text
        self.buffer_label.setText(buffer_text)
        self.buffer_label.setVisible(bool(buffer_text))

    def track_menu(self):
        menu = self.build_track_menu()
        menu.exec(self.cursor().pos())
        menu.deleteLater()

    def build_track_menu(self):
        menu = _window.QMenu(self)
        generation = self.track_preferences.generation

        def guarded(callback):
            if not self._closed and self.track_preferences.generation == generation:
                callback()

        for mode, title in [("audio", "Ses parçaları"), ("sub", "Altyazılar")]:
            sub = menu.addMenu(title)
            sub.setEnabled(self.current is not None and not self._idle)
            tracks = [t for t in self._tracks if isinstance(t, dict) and t.get("type") == mode]
            off = sub.addAction("Kapalı")
            off.setCheckable(True)
            off.setChecked(not any(t.get("selected") for t in tracks))
            off.triggered.connect(
                lambda checked=False, m=mode: self.track_preferences.select(
                    m, None, generation=generation
                )
            )
            for track in tracks:
                label = track.get("title") or track.get("lang") or f"Parça {track.get('id', '')}"
                action = sub.addAction(str(label).replace("&", "&&"))
                action.setCheckable(True)
                action.setChecked(bool(track.get("selected")))
                action.triggered.connect(
                    lambda checked=False, m=mode, t=track: self.track_preferences.select(
                        m, t, generation=generation
                    )
                )
        if self.subtitles.available():
            find_subtitle = menu.addAction("Altyazı bul…")
            find_subtitle.triggered.connect(lambda: guarded(self.subtitles.open))
        menu.addSeparator()
        self.comfort_preferences.add_menu(
            menu, guarded, self.status, enabled=self.current is not None and not self._idle
        )
        menu.addSeparator()
        remember = menu.addAction("Bu kaynak için tercihleri hatırla")
        remember.setCheckable(True)
        remember.setChecked(self.track_preferences.remember)
        remember.setEnabled(self.track_preferences.source_id is not None)
        remember.triggered.connect(
            lambda checked: guarded(lambda: self.track_preferences.set_remember(checked))
        )
        reset = menu.addAction("Ses ve altyazı tercihlerini sıfırla")
        reset.setEnabled(self.track_preferences.source_id is not None)
        reset.triggered.connect(lambda: guarded(self.track_preferences.reset))
        menu.addSeparator()
        record = menu.addAction("Kaydet", self.record_current)
        record.setEnabled(
            bool(self.current and self.current.kind == "live" and self._playback_active)
        )
        menu.addAction("Kayıtlar…", self.open_recordings)
        restart = menu.addAction("Baştan başlat")
        restart.setEnabled(
            self.current is not None and self._current_persistent and self.current.kind != "live"
        )
        restart.triggered.connect(lambda: guarded(self.restart_current))
        return menu
