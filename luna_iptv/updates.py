"""A once-a-day look at the latest GitHub release; nothing about the person is sent."""

import json
import re
import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

RELEASES_API = "https://api.github.com/repos/Ilanto/luna-iptv/releases/latest"
RELEASES_PAGE = "https://github.com/Ilanto/luna-iptv/releases/latest"
CHECK_EVERY = 24 * 3600
UPDATE_CHOICES = (("Açık", "on"), ("Kapalı", "off"))


def parse_version(text):
    """'v0.19.0' → (0, 19, 0); anything else → None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def newer(remote, local=__version__):
    remote, local = parse_version(remote), parse_version(local)
    return remote is not None and local is not None and remote > local


class UpdateChecker(QObject):
    """Asks GitHub for the latest release at most once a day, when the setting allows it."""

    found = Signal(str, str)  # version, page

    def __init__(self, store, parent=None, *, fetch=None, clock=time.time):
        super().__init__(parent)
        self.store = store
        self.clock = clock
        self.fetch = fetch or self._fetch
        self.available = None  # (version, page) once found
        self._manager = None

    def enabled(self):
        return self.store.setting("update_check", "on") == "on"

    def check(self, *, force=False):
        if not force and not self.enabled():
            return False
        last = self.store.setting("update_checked_at", 0)
        if not force and isinstance(last, (int, float)) and self.clock() - last < CHECK_EVERY:
            return False
        self.fetch(self._answer)
        return True

    def _answer(self, payload):
        self.store.set_setting("update_checked_at", int(self.clock()))
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return
        tag = data.get("tag_name")
        page = data.get("html_url")
        if not isinstance(page, str) or not page.startswith("https://github.com/"):
            page = RELEASES_PAGE
        if newer(tag):
            version = ".".join(map(str, parse_version(tag)))
            self.available = (version, page)
            self.found.emit(version, page)

    def _fetch(self, callback):
        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(RELEASES_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"Luna-IPTV/{__version__}".encode())
        request.setTransferTimeout(15000)
        reply = self._manager.get(request)

        def finished():
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            payload = bytes(reply.readAll()).decode("utf-8", "replace") if ok else None
            reply.deleteLater()
            if ok:
                callback(payload)

        reply.finished.connect(finished)
