"""Lazy list artwork: bounded HTTP, worker decoding/disk I/O, memory-only painting."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QObject,
    QPoint,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class _DiskCache:
    """Worker-only disk operations. Entries contain hashed identities, never source URLs."""

    def __init__(self, directory, quota, max_bytes, max_pixels, size, clock):
        self.directory, self.quota = directory, quota
        self.max_bytes, self.max_pixels, self.size = max_bytes, max_pixels, size
        self.clock = clock
        self.lock = threading.Lock()

    def _path(self, url):
        return self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".logo")

    def decode(self, raw):
        if not raw or len(raw) > self.max_bytes:
            return None
        buffer = QBuffer()
        buffer.setData(QByteArray(raw))
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        # Raster only: avoid SVG external resources and unbounded vector rendering.
        if bytes(reader.format()).lower() not in {b"png", b"jpeg", b"webp", b"gif", b"bmp", b"ico"}:
            return None
        dimensions = reader.size()
        if not dimensions.isValid() or dimensions.width() * dimensions.height() > self.max_pixels:
            return None
        reader.setScaledSize(dimensions.scaled(self.size, Qt.KeepAspectRatio))
        image = reader.read()
        if image.isNull() or image.width() * image.height() > self.max_pixels:
            return None
        return image.scaled(self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def read(self, url):
        with self.lock:
            path = self._path(url)
            try:
                with path.open("rb") as file:
                    raw = file.read(self.max_bytes + 1025)
                header, payload = raw.split(b"\n", 1)
                metadata = json.loads(header)
                expiry = float(metadata["expiry"])
                if expiry <= self.clock():
                    path.unlink(missing_ok=True)
                    return None
                image = self.decode(payload) if metadata["ok"] else None
                if metadata["ok"] and image is None:
                    path.unlink(missing_ok=True)
                    return None
                os.utime(path, None)
                return image, expiry
            except (OSError, ValueError, KeyError, TypeError):
                return None

    def local(self, url, success_ttl, negative_ttl):
        try:
            # Nonblocking open plus fstat closes the FIFO/device and stat/open race.
            fd = os.open(url, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as file:
                raw = file.read(self.max_bytes + 1) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
        except OSError:
            raw = b""
        return self.save(url, raw, success_ttl, negative_ttl)

    def save(self, url, raw, success_ttl, negative_ttl):
        image = self.decode(raw)
        expiry = self.clock() + (success_ttl if image is not None else negative_ttl)
        payload = QByteArray()
        if image is not None:
            buffer = QBuffer(payload)
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
        metadata = json.dumps({"expiry": expiry, "ok": image is not None}).encode()
        data = metadata + b"\n" + bytes(payload)
        with self.lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.directory.chmod(0o700)
                path = self._path(url)
                temporary = path.with_suffix(".tmp")
                if len(data) <= self.quota:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "wb") as file:
                        file.write(data)
                    temporary.chmod(0o600)
                    temporary.replace(path)
                files = sorted(self.directory.glob("*.logo"), key=lambda p: p.stat().st_mtime)
                total = sum(p.stat().st_size for p in files)
                for old in files:
                    if total <= self.quota:
                        break
                    total -= old.stat().st_size
                    old.unlink(missing_ok=True)
            except OSError:
                pass  # An unwritable cache never breaks the library.
        return image, expiry


class LogoCache(QObject):
    ready = Signal(str)

    def __init__(
        self,
        store_path,
        parent=None,
        *,
        clock=time.time,
        max_concurrent=4,
        timeout_ms=5000,
        max_bytes=2 * 1024 * 1024,
        max_pixels=4_000_000,
        memory_limit=256,
        disk_limit=64 * 1024 * 1024,
        success_ttl=7 * 86400,
        negative_ttl=15 * 60,
    ):
        super().__init__(parent)
        self.cache_dir = Path(store_path).parent / (Path(store_path).name + ".logos")
        self._clock = clock
        self._max_concurrent = max(1, min(4, max_concurrent))
        self._timeout_ms = max(1, min(5000, timeout_ms))
        self._max_bytes = min(2 * 1024 * 1024, max_bytes)
        self._memory_limit = max(1, min(256, memory_limit))
        self._success_ttl, self._negative_ttl = success_ttl, negative_ttl
        self._memory = OrderedDict()
        self._queue = OrderedDict()
        self._jobs = {}
        self._closed = False
        self._disk = _DiskCache(
            self.cache_dir,
            min(64 * 1024 * 1024, disk_limit),
            self._max_bytes,
            min(4_000_000, max_pixels),
            QSize(76, 76),
            clock,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrent, thread_name_prefix="logo"
        )
        # Worker futures never call QObjects, including after the window has been destroyed.
        executor = self._executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))
        self._network = QNetworkAccessManager(self)
        self._poll = QTimer(self)
        self._poll.setInterval(15)
        self._poll.timeout.connect(self._collect)

    def prepared_logo(self, url):
        """No I/O, decoding, request scheduling or catalogue traversal from paint."""
        entry = self._memory.get(url)
        if entry is None or entry[1] <= self._clock():
            return None
        self._memory.move_to_end(url)
        return entry[0]

    def _cached(self, url):
        entry = self._memory.get(url)
        return entry is not None and entry[1] > self._clock()

    def request_logo(self, url):
        if self._closed or not self._allowed(url) or self._cached(url) or url in self._jobs:
            return
        if len(self._queue) < 256:
            self._queue[url] = None
        self._start()

    def request_visible(self, urls):
        """Replace obsolete queued rows; running work remains bounded to four jobs."""
        self._queue.clear()
        for url in dict.fromkeys(urls):
            if len(self._queue) >= 256:
                break
            if self._allowed(url) and not self._cached(url) and url not in self._jobs:
                self._queue[url] = None
        self._start()

    @staticmethod
    def _allowed(url):
        try:
            parts = urlsplit(url)
            if not parts.scheme and isinstance(url, str) and url.startswith("/"):
                return not url.startswith("//")
            return bool(
                parts.scheme in {"http", "https"}
                and parts.hostname
                and not parts.username
                and not parts.password
            )
        except (ValueError, TypeError):
            return False

    def _start(self):
        if self._closed:
            return
        while self._queue and len(self._jobs) < self._max_concurrent:
            url, _ = self._queue.popitem(last=False)
            self._jobs[url] = {
                "future": self._executor.submit(self._disk.read, url),
                "stage": "disk",
                "redirects": 0,
                "reply": None,
                "timer": None,
            }
        if self._jobs:
            self._poll.start()

    def _collect(self):
        if self._closed:
            return
        for url, job in list(self._jobs.items()):
            future = job.get("future")
            if future is None or not future.done():
                continue
            job["future"] = None
            try:
                result = future.result()
            except Exception:
                result = None
            if job["stage"] == "disk" and result is None:
                if url.startswith("/"):
                    job["stage"] = "save"
                    job["future"] = self._executor.submit(
                        self._disk.local, url, self._success_ttl, self._negative_ttl
                    )
                    continue
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda u=url: self._timeout(u))
                job["timer"] = timer
                timer.start(self._timeout_ms)
                self._fetch(url, url)
            else:
                self._complete(url, result or (None, self._clock() + self._negative_ttl))
        if not self._jobs:
            self._poll.stop()

    def _fetch(self, url, target):
        job = self._jobs[url]
        request = QNetworkRequest(QUrl(target))
        request.setRawHeader(b"User-Agent", b"Luna-IPTV/0.1")
        request.setRawHeader(b"Accept-Encoding", b"identity")
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy
        )
        request.setAttribute(QNetworkRequest.CookieLoadControlAttribute, QNetworkRequest.Manual)
        request.setAttribute(QNetworkRequest.CookieSaveControlAttribute, QNetworkRequest.Manual)
        request.setAttribute(QNetworkRequest.AuthenticationReuseAttribute, QNetworkRequest.Manual)
        reply = self._network.get(request)
        reply.setReadBufferSize(self._max_bytes + 1)
        job.update(reply=reply, raw=bytearray(), failed=False, stage="network", target=target)
        reply.metaDataChanged.connect(lambda u=url: self._read(u))
        reply.readyRead.connect(lambda u=url: self._read(u))
        reply.finished.connect(lambda u=url: self._finished(u))

    def _read(self, url):
        if self._closed or url not in self._jobs:
            return
        job = self._jobs[url]
        reply = job["reply"]
        if reply is None or job["failed"]:
            return
        length = reply.header(QNetworkRequest.ContentLengthHeader)
        if length is not None and int(length) > self._max_bytes:
            job["failed"] = True
            reply.abort()
            return
        job["raw"].extend(bytes(reply.read(self._max_bytes + 1 - len(job["raw"]))))
        if len(job["raw"]) > self._max_bytes:
            job["failed"] = True
            reply.abort()

    def _timeout(self, url):
        job = self._jobs.get(url)
        if job and job["reply"] is not None:
            job["failed"] = True
            job["reply"].abort()

    def _finished(self, url):
        if self._closed or url not in self._jobs:
            return
        job = self._jobs[url]
        reply = job["reply"]
        self._read(url)
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        redirect = reply.attribute(QNetworkRequest.RedirectionTargetAttribute)
        ok = not job["failed"] and reply.error() == QNetworkReply.NoError
        job["reply"] = None
        reply.deleteLater()
        if ok and redirect is not None:
            target = QUrl(job["target"]).resolved(redirect).toString()
            if (
                self._allowed(target)
                and job["redirects"] < 5
                and not (
                    urlsplit(job["target"]).scheme == "https" and urlsplit(target).scheme == "http"
                )
            ):
                job["redirects"] += 1
                self._fetch(url, target)
                return
            ok = False
        job["timer"].stop()
        job["timer"].deleteLater()
        job["timer"] = None
        raw = bytes(job["raw"]) if ok and status == 200 else b""
        job["stage"] = "save"
        # Decode and persist on workers; only QPixmap preparation uses the GUI thread.
        job["future"] = self._executor.submit(
            self._disk.save, url, raw, self._success_ttl, self._negative_ttl
        )
        job.pop("raw", None)

    def _complete(self, url, result):
        image, expiry = result
        pixmap = QPixmap.fromImage(image) if image is not None else None
        self._memory[url] = pixmap, expiry
        self._memory.move_to_end(url)
        while len(self._memory) > self._memory_limit:
            self._memory.popitem(last=False)
        del self._jobs[url]
        self.ready.emit(url)
        self._start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._poll.stop()
        self._queue.clear()
        for job in self._jobs.values():
            if job["timer"] is not None:
                job["timer"].stop()
            if job["reply"] is not None:
                job["reply"].abort()
        self._jobs.clear()
        self._memory.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)


class LogoViewportController(QObject):
    """Coalesce viewport/model changes and fetch only the rows currently on screen."""

    def __init__(self, view, cache):
        super().__init__(view)
        self._view, self._cache = view, cache
        self._closed = False
        self._visible = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._schedule)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self._changed)
        view.horizontalScrollBar().valueChanged.connect(self._changed)
        model = view.model()
        for signal in (
            model.modelReset,
            model.layoutChanged,
            model.rowsInserted,
            model.rowsRemoved,
            model.dataChanged,
        ):
            signal.connect(self._changed)
        cache.ready.connect(self._ready)
        self._changed()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.Hide):
            self._changed()
        return False

    def _changed(self, *_):
        self._visible.clear()
        if not self._closed:
            self._timer.start()

    def _schedule(self):
        if self._closed:
            return
        view = self._view
        urls = []
        if view.isVisible():
            # Uniform list rows: indexAt gives the first row in O(1), then visit only
            # visible rectangles rather than traversing a potentially huge catalogue.
            index = view.indexAt(QPoint(4, 0))
            row = index.row() if index.isValid() else 0
            model = view.model()
            while row < model.rowCount():
                index = model.index(row, 0)
                rect = view.visualRect(index)
                if rect.top() >= view.viewport().height():
                    break
                if rect.bottom() >= 0:
                    channel = index.data(Qt.UserRole)
                    if channel and channel.logo:
                        urls.append(channel.logo)
                row += 1
        self._visible = set(urls)
        self._cache.request_visible(urls)

    def _ready(self, url):
        if not self._closed and url in self._visible and self._view.isVisible():
            self._view.viewport().update()

    def close(self):
        self._closed = True
        self._visible.clear()
        self._timer.stop()
