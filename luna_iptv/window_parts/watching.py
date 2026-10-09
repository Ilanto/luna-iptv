from __future__ import annotations

from .. import window as _window
from ..i18n import _, _n


class WatchingMixin:
    """Manage banners, sleep, next episodes and channel numbers."""

    def banner_suppressed(self):
        """The mini player (unless it went fullscreen) has no room for the banner."""
        mini = getattr(self, "mini_player", None)
        return self.tv_mode is not None or bool(
            mini is not None and mini.active and not self.isFullScreen()
        )

    def show_channel_banner(self, seconds=4.5):
        """The TV-style banner: number, channel, programme now and next."""
        channel = self.current
        if self._closed or channel is None or self.video_stack.currentIndex() != 1:
            return
        if self.banner_suppressed():
            self.channel_banner.hide_banner()
            return
        programme = self.programme_now(channel)
        following = None
        if programme is not None:
            index = self._guide_index.get(channel.id.split(":", 1)[0])
            upcoming = index.upcoming(channel.tvg_id, 1) if index else []
            following = upcoming[0] if upcoming else None
        number = None
        if channel.kind == "live":
            ids = [c.id for c in self.numbered_channels()]
            number = ids.index(channel.id) + 1 if channel.id in ids else None
        self.channel_banner.set_content(channel, number, programme, following)
        self._banner_placer.place()
        self.channel_banner.show_for(seconds)

    # Watching comforts: next episode, sleep timer, channel numbers.

    def _notice(self, kind, text, primary=None, secondary=None):
        self._notice_kind = kind
        self.watch_notice.present(text, primary, secondary)

    def _clear_notice(self, kind=None):
        if kind is None or self._notice_kind == kind:
            self._notice_kind = None
            self.watch_notice.hide()

    def offer_next_episode(self):
        """After an episode ends: the next one starts by itself after a short countdown."""
        following = _window.next_episode(self.model.channels, self.current)
        if following is None:
            return False
        if _window.selected_setting(self.store, "autoplay_next", _window.AUTOPLAY_CHOICES) != "on":
            self._notice(
                "next",
                _("Sonraki bölüm: {name}").format(name=following.name),
                (_("Oynat"), lambda: self.play_next_episode(following)),
                (_("Kapat"), lambda: self._clear_notice("next")),
            )
            return True
        self.next_countdown.start(following)
        return True

    def _next_episode_tick(self, seconds):
        following = self.next_countdown.payload
        self._notice(
            "next",
            _n(
                "Sonraki bölüm {seconds} saniye içinde: {name}",
                "Sonraki bölüm {seconds} saniyeler içinde: {name}",
                seconds,
            ).format(seconds=seconds, name=following.name),
            (_("Şimdi oynat"), self.next_countdown.finish_now),
            (_("İptal"), self.cancel_next_episode),
        )

    def cancel_next_episode(self):
        self.next_countdown.cancel()
        self._clear_notice("next")

    def play_next_episode(self, channel):
        self._clear_notice("next")
        fresh = next((c for c in self.model.channels if c.id == channel.id), None)
        if fresh is not None:
            # The series was already open, so this episode needs no PIN again.
            self.request_play(fresh, approved=True)

    def sleep_menu(self):
        menu = self.build_sleep_menu()
        menu.exec(self.sleep_button.mapToGlobal(self.sleep_button.rect().topLeft()))
        menu.deleteLater()

    def build_sleep_menu(self):
        menu = _window.QMenu(self)
        menu.setTitle(_("Uyku zamanlayıcısı"))
        if self.sleep_timer.active:
            remaining = (
                _("Bitince duracak")
                if self.sleep_timer.mode == "media"
                else _("Kalan: {remaining}").format(remaining=self.sleep_timer.label())
            )
            menu.addAction(remaining).setEnabled(False)
            menu.addAction(_("15 dakika uzat"), lambda: self.sleep_timer.extend(15))
            menu.addAction(_("Kapat"), self.sleep_timer.cancel)
            menu.addSeparator()
        for minutes in _window.SLEEP_CHOICES:
            menu.addAction(
                _n("{minutes} dakika sonra", "{minutes} dakikalar sonra", minutes).format(
                    minutes=minutes
                ),
                lambda m=minutes: self.start_sleep(m),
            )
        if self.current is not None and self._playback_active:
            if self.current.kind == "live":
                programme = self.programme_now(self.current)
                if programme is not None:
                    end = programme.end.timestamp()
                    menu.addAction(_("Bu program bitince"), lambda: self.start_sleep_until(end))
            elif self._duration > 0:
                label = _("Bu bölüm bitince") if self.current.series_id else _("Bu film bitince")
                menu.addAction(label, self.start_sleep_at_media_end)
        return menu

    def start_sleep(self, minutes):
        self.sleep_timer.start(minutes)
        self.status(
            _n(
                "Uyku zamanlayıcısı: {minutes} dakika sonra oynatma duracak.",
                "Uyku zamanlayıcısı: {minutes} dakikalar sonra oynatma duracak.",
                minutes,
            ).format(minutes=minutes)
        )

    def start_sleep_at_media_end(self):
        self.sleep_timer.start_at_media_end()
        self.status(_("Uyku zamanlayıcısı: bitince oynatma duracak."))

    def start_sleep_until(self, moment):
        self.sleep_timer.start_until(moment)
        self.status(_("Uyku zamanlayıcısı: bitince oynatma duracak."))

    def refresh_sleep_button(self):
        active = self.sleep_timer.active
        self.sleep_label.setText(self.sleep_timer.label())
        self.sleep_label.setVisible(active)
        self.sleep_button.setToolTip(
            (
                _("Uyku zamanlayıcısı: bitince duracak")
                if self.sleep_timer.mode == "media"
                else _("Uyku zamanlayıcısı: {remaining} kaldı").format(
                    remaining=self.sleep_timer.label()
                )
            )
            if active
            else _("Uyku zamanlayıcısı")
        )
        if active and self.sleep_timer.mode != "media":
            self._sleep_label_timer.start()
        else:
            self._sleep_label_timer.stop()

    def sleep_expired(self):
        self.cancel_next_episode()
        if self._playback_active:
            self.stop_playback()
        self.leave_fullscreen()
        self.status(_("Uyku zamanlayıcısı: oynatma durduruldu. İyi geceler."))

    def numbered_channels(self):
        """Live channels in list order, of the chosen source; their 1-based place is the number."""
        source = self.proxy.source
        return [
            c
            for c in self.model.channels
            if c.kind == "live"
            and not self.proxy.category_hidden(c)
            and (not source or c.id.startswith(source + ":"))
            and not (self.proxy.hide_locked and c.id in self.model.locked)
        ]

    def _number_typing(self, digits):
        self._notice("number", _("Kanal {digits}_").format(digits=digits))

    def jump_to_number(self, number):
        channels = self.numbered_channels()
        if number > len(channels):
            self._notice(
                "number",
                _("{number} numaralı kanal yok (1–{count}).").format(
                    number=number, count=len(channels)
                ),
            )
            _window.QTimer.singleShot(2500, lambda: self._clear_notice("number"))
            return False
        channel = channels[number - 1]
        self._notice("number", f"{number} · {channel.name}")
        _window.QTimer.singleShot(2500, lambda: self._clear_notice("number"))
        self.request_play(channel)
        return True
