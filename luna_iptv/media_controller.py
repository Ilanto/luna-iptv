"""On-demand details, independent of the live-player and catalog task state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QSize, QUrl
from PySide6.QtGui import QDesktopServices
from shiboken6 import isValid

from . import imdb
from .logos import LogoCache
from .media_details import MediaDetails
from .media_dialog import MediaDetailDialog
from .network import XtreamClient

DETAIL_TTL = 86400
FAILURE_TTL = 60


def source_fingerprint(source):
    fields = ("id", "type", "location", "username", "password")
    raw = json.dumps([source.get(key, "") for key in fields], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class MediaDetailController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self.posters = None
        self._channel = None
        self._series_channel = None
        self._fingerprint = None
        self._active = None
        self._pending = None
        self._failures = OrderedDict()
        self._closed = False

    def dismiss(self):
        dialog, self.dialog = self.dialog, None
        self._pending = None
        if dialog is not None and isValid(dialog):
            dialog.reject()

    def open(self, channel):
        if self._closed:
            return
        if self.dialog is not None and self._channel.id == channel.id:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.window.dismiss_resume()
        self.dismiss()
        source = self.window.source_for(channel)
        if source is None:
            return
        self._channel = channel
        self._fingerprint = source_fingerprint(source)
        self._series_channel = (
            channel
            if channel.kind == "series"
            else next(
                (
                    item
                    for item in self.window.store.channels(source["id"])
                    if item.kind == "series" and item.series_id == channel.series_id
                ),
                None,
            )
            if channel.series_id
            else None
        )
        if self.posters is None:
            self.posters = getattr(self.window, "posters", None) or LogoCache(
                self.window.store.path, self, size=QSize(240, 360), cache_suffix=".posters"
            )
        dialog = MediaDetailDialog(channel, self.posters, self.window)
        self.dialog = dialog
        dialog.play_requested.connect(self._play)
        dialog.favorite_requested.connect(self._favorite)
        dialog.selection_changed.connect(self.refresh_favorite)
        dialog.series_favorite_requested.connect(self._favorite)
        dialog.set_series_channel(self._series_channel)
        dialog.set_playback_preferences(self.window.track_preferences.preview(source["id"]))
        dialog.retry_requested.connect(lambda: self._request(channel, source, force=True))
        dialog.imdb_lookup_requested.connect(self._find_imdb)

        def finished(_result):
            if self.dialog is dialog:
                self.dialog = None
                self._pending = None

        dialog.finished.connect(finished)
        self.refresh_favorite()
        dialog.show()
        if source["type"] != "xtream":
            dialog.set_status("Bu kaynak ayrıntılı içerik bilgisi sağlamıyor.")
            return
        cached = self.window.store.media_details(channel.id, self._fingerprint)
        if cached is not None:
            details, checked_at = cached
            self._display(details, source)
            if 0 <= time.time() - checked_at < DETAIL_TTL:
                dialog.set_status(
                    "Bu dizide henüz bölüm bulunmuyor."
                    if channel.kind == "series" and not details.episodes
                    else ""
                )
                return
        self._request(channel, source)

    def _valid(self, channel, fingerprint):
        if self._closed or self.window._closed:
            return False
        source = self.window.source_for(channel)
        return (
            source is not None
            and source_fingerprint(source) == fingerprint
            and any(c.id == channel.id for c in self.window.store.channels(source["id"]))
        )

    def _visible(self, channel, fingerprint):
        return (
            self.dialog is not None
            and isValid(self.dialog)
            and self._channel.id == channel.id
            and self._fingerprint == fingerprint
        )

    def _display(self, details, source):
        episodes = details.episodes
        if episodes:
            episodes = self.window.store.upsert_channels(source["id"], episodes)
            self.window.model.reset(self.window.store.channels(), self.window.store.favorites())
            self.window.filter_changed()
        self.dialog.set_details(
            MediaDetails(
                info=details.info,
                episodes=episodes,
                episode_info=details.episode_info,
                series_title=details.series_title
                or (self._series_channel.name if self._series_channel else ""),
            )
        )
        self.refresh_favorite()

    def _request(self, channel, source, *, force=False):
        fingerprint = source_fingerprint(source)
        key = channel.id, fingerprint
        if not self._valid(channel, fingerprint):
            self.invalidate()
            return
        if not force and time.monotonic() < self._failures.get(key, 0):
            if self._visible(channel, fingerprint):
                self.dialog.set_status("Ayrıntılar alınamadı. Yeniden deneyebilirsin.", retry=True)
            return
        if self._visible(channel, fingerprint):
            self.dialog.set_status("Ayrıntılar alınıyor…")
        if self._active is not None:
            self._pending = None if self._active == key else (channel, source)
            return
        self._active = key

        def finish():
            self._active = None
            pending, self._pending = self._pending, None
            if pending is not None and self.dialog is not None:
                self._request(*pending)

        def failed(_error):
            self._failures[key] = time.monotonic() + FAILURE_TTL
            self._failures.move_to_end(key)
            while len(self._failures) > 128:
                self._failures.popitem(last=False)
            if self._valid(channel, fingerprint) and self._visible(channel, fingerprint):
                self.dialog.set_status(
                    "Ayrıntılar alınamadı. Mevcut bilgiler korunuyor.", retry=True
                )
            finish()

        def loaded(details):
            try:
                if self._valid(channel, fingerprint):
                    self.window.store.save_media_details(
                        channel.id, fingerprint, details, int(time.time())
                    )
                    self._failures.pop(key, None)
                    if self._visible(channel, fingerprint):
                        self._display(details, source)
                        self.dialog.set_status(
                            "Bu dizide henüz bölüm bulunmuyor."
                            if channel.kind == "series" and not details.episodes
                            else ""
                        )
            except (sqlite3.Error, OSError):
                if self._visible(channel, fingerprint):
                    self.dialog.set_status("Ayrıntılar kaydedilemedi.", retry=True)
            finally:
                finish()

        self.window.run_task(
            lambda: XtreamClient(
                source["location"], source["username"], source["password"]
            ).media_details(channel),
            loaded,
            "",
            busy=False,
            failure=failed,
        )

    def _play(self, selected):
        if self.dialog is None or not self._valid(self._channel, self._fingerprint):
            self.invalidate()
            return
        fresh = next((c for c in self.window.store.channels() if c.id == selected.id), None)
        if fresh is not None:
            preferences = self.dialog.playback_preferences()
            self.dismiss()
            self.window.request_play(fresh, preferences=preferences)

    def _favorite(self, channel):
        if self.dialog is not None and self._valid(channel, self._fingerprint):
            self.window.toggle_channel_favorite(channel)

    def refresh_favorite(self, *_):
        if self.dialog is not None:
            favorites = self.window.store.favorites()
            target = self.dialog.favorite_channel()
            self.dialog.set_favorite(target is not None and target.id in favorites)
            self.dialog.set_series_favorite(
                self._series_channel is not None and self._series_channel.id in favorites
            )

    def invalidate(self):
        if self.dialog is not None and not self._valid(self._channel, self._fingerprint):
            self.dismiss()

    def _find_imdb(self, title, year, kind):
        self.window.run_task(
            lambda: imdb.find(title, year, kind),
            lambda url: QDesktopServices.openUrl(QUrl(url)),
            "IMDb'de aranıyor…",
            busy=False,
        )

    def close(self):
        self._closed = True
        self.dismiss()
        if self.posters is not None:
            self.posters.close()
