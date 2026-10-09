"""Serial, daily checks of every profile's favourite Xtream series."""

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from . import reminders
from .media_controller import source_fingerprint
from .network import XtreamClient
from .settings import AUTOPLAY_CHOICES, selected_setting


class NewEpisodeService(QObject):
    changed = Signal()

    def __init__(
        self,
        store,
        run_task,
        play,
        switch_profile,
        toast,
        parent=None,
        *,
        client_factory=None,
        sender=None,
        clock=datetime.now,
    ):
        super().__init__(parent)
        self.store, self.run_task = store, run_task
        self.play, self.switch_profile, self.toast = play, switch_profile, toast
        self.clock = clock
        self.client_factory = client_factory or (
            lambda source: XtreamClient(source["location"], source["username"], source["password"])
        )
        self.sender = sender if sender is not None else reminders.DesktopNotifications(self)
        self.sender.action_invoked.connect(self._watch)
        self.sender.notification_closed.connect(self._dismiss)
        self._notifications = {}
        self._queue = []
        self._active = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._next)

    def enabled(self):
        return selected_setting(self.store, "new_episode_notifications", AUTOPLAY_CHOICES) == "on"

    def refresh(self):
        if self._closed or not self.enabled():
            return
        queued = set(self._queue)
        for _, series_id, _ in self.store.favorite_series():
            if series_id not in queued:
                self._queue.append(series_id)
                queued.add(series_id)
        if not self._active and not self._timer.isActive():
            self._next()

    def _next(self):
        if self._closed or not self.enabled():
            self._queue.clear()
            return
        today = self.clock().date().isoformat()
        favorites = {cid: sid for _, cid, sid in self.store.favorite_series()}
        channels = {c.id: c for c in self.store.channels()}
        sources = {s["id"]: s for s in self.store.sources()}
        while self._queue:
            series_id = self._queue.pop(0)
            if series_id not in favorites or self.store.episode_check_day(series_id) == today:
                continue
            series = channels.get(series_id)
            source = sources.get(favorites[series_id])
            if series is not None and source is not None:
                break
        else:
            return
        fingerprint = source_fingerprint(source)
        # Count attempts too, so a failing server is not retried every refresh.
        self.store.mark_episode_check(series.id, today)
        self._active = True

        def failed(_error):
            self._active = False
            self._queue.clear()
            self._timer.stop()

        def loaded(details):
            if self._closed:
                return
            try:
                current = next((s for s in self.store.sources() if s["id"] == source["id"]), None)
                owners = [pid for pid, cid, _ in self.store.favorite_series() if cid == series.id]
                if (
                    not self.enabled()
                    or not owners
                    or current is None
                    or source_fingerprint(current) != fingerprint
                ):
                    failed(None)
                    return
                existing = self.store.channels(source["id"])
                known_ids = {c.id for c in existing if c.series_id == series.series_id}
                known_keys = {
                    c.provider_key or self.store._legacy_provider_key(c)
                    for c in existing
                    if c.kind != "series" and c.series_id == series.series_id
                } - {""}
                episodes = self.store.upsert_channels(source["id"], details.episodes)
                new = [
                    c
                    for c in episodes
                    if c.id not in known_ids
                    and (c.provider_key or self.store._legacy_provider_key(c)) not in known_keys
                ]
                self.store.save_media_details(
                    series.id, fingerprint, details, int(self.clock().timestamp())
                )
                if new:
                    self.store.mark_new_episodes(series.id, new, owners)
                    body = f"{series.name}: {len(new)} yeni bölüm"
                    for profile_id in owners:
                        self.sender.send(
                            "Yeni bölümler",
                            body,
                            lambda nid, pid=profile_id: self._sent(nid, pid, series.id, new[0].id),
                        )
                    self.toast(body)
                    self.changed.emit()
            except Exception:
                failed(None)
                return
            self._active = False
            self._timer.start()

        try:
            self.run_task(
                lambda: self.client_factory(source).media_details(series),
                loaded,
                "",
                busy=False,
                failure=failed,
            )
        except Exception:
            failed(None)

    def _sent(self, notification_id, profile_id, series_id, episode_id):
        if not self._closed and notification_id is not None:
            self._notifications[notification_id] = (profile_id, series_id, episode_id)

    def _watch(self, notification_id, action):
        if self._closed or action != "watch" or not self.enabled():
            return
        item = self._notifications.pop(notification_id, None)
        if item is None:
            return
        profile_id, series_id, episode_id = item
        if not any(
            pid == profile_id and cid == series_id for pid, cid, _ in self.store.favorite_series()
        ):
            return
        if profile_id != self.store.profile_id and not self.switch_profile(profile_id):
            return
        if not self._closed and self.store.profile_id == profile_id:
            episode = next((c for c in self.store.channels() if c.id == episode_id), None)
            if episode is not None:
                self.play(episode)

    def _dismiss(self, notification_id):
        self._notifications.pop(notification_id, None)

    def close(self):
        self._closed = True
        self._timer.stop()
        self._queue.clear()
        self._notifications.clear()
        self.sender.close()
