"""Synthetic images and localhost only; logo work must never open stream URLs."""

import importlib
import json
import stat
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from luna_iptv.network import XtreamClient
from luna_iptv.playlist import parse_m3u


def spin(app, predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "asynchronous logo request did not complete"


def png(width=100, height=50):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.red)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


@pytest.fixture
def server():
    state = {"counts": Counter(), "headers": [], "routes": {}, "active": 0, "peak": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["counts"][self.path] += 1
            state["headers"].append(dict(self.headers))
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            try:
                code, headers, body, delay = state["routes"].get(self.path, (200, {}, png(), 0))
                time.sleep(delay)
                self.send_response(code)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state["active"] -= 1

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{http.server_port}"
    yield state
    http.shutdown()
    http.server_close()
    thread.join()


@pytest.fixture
def cache_factory(qt_app, tmp_path):
    caches = []

    def create(**kwargs):
        module = importlib.import_module("luna_iptv.logos")
        cache = module.LogoCache(tmp_path / "isolated-store.sqlite3", **kwargs)
        caches.append(cache)
        return cache

    yield create
    for cache in caches:
        cache.close()
    qt_app.processEvents()


def request(app, cache, url):
    seen = []
    cache.ready.connect(seen.append)
    cache.request_logo(url)
    spin(app, lambda: url in seen)
    cache.ready.disconnect(seen.append)
    return cache.prepared_logo(url)


def test_valid_logo_duplicate_memory_restart_and_private_disk(qt_app, cache_factory, server):
    cache = cache_factory()
    url = server["base"] + "/logo"
    seen = []
    cache.ready.connect(seen.append)
    cache.request_logo(url)
    cache.request_logo(url)
    spin(qt_app, lambda: seen)
    logo = cache.prepared_logo(url)
    assert (logo.width(), logo.height()) == (76, 38)
    cache.request_logo(url)
    qt_app.processEvents()
    assert server["counts"]["/logo"] == 1
    assert stat.S_IMODE(cache.cache_dir.stat().st_mode) == 0o700
    files = list(cache.cache_dir.glob("*.logo"))
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert url.encode() not in files[0].read_bytes()
    cache.close()
    restored = cache_factory()
    assert request(qt_app, restored, url) is not None
    assert server["counts"]["/logo"] == 1


@pytest.mark.parametrize("route", ["missing", "broken", "bytes", "pixels", "length"])
def test_bad_images_negative_cache(qt_app, cache_factory, server, route):
    server["routes"].update(
        {
            "/missing": (404, {}, b"no", 0),
            "/broken": (200, {}, b"not an image", 0),
            "/bytes": (200, {}, b"a" * 1025, 0),
            "/pixels": (200, {}, png(101, 101), 0),
            "/length": (200, {"Content-Length": "999999999"}, b"", 0),
        }
    )
    cache = cache_factory(max_bytes=1024, max_pixels=10000)
    url = server["base"] + "/" + route
    assert request(qt_app, cache, url) is None
    cache.request_logo(url)
    qt_app.processEvents()
    assert server["counts"]["/" + route] == 1
    cache.close()
    restored = cache_factory(max_bytes=1024, max_pixels=10000)
    assert request(qt_app, restored, url) is None
    assert server["counts"]["/" + route] == 1


def test_success_and_negative_expiry(qt_app, cache_factory, server):
    now = [1000.0]
    cache = cache_factory(clock=lambda: now[0], success_ttl=10, negative_ttl=2)
    server["routes"]["/bad"] = (404, {}, b"", 0)
    good, bad = server["base"] + "/good", server["base"] + "/bad"
    assert request(qt_app, cache, good) is not None
    assert request(qt_app, cache, bad) is None
    now[0] += 3
    assert request(qt_app, cache, bad) is None
    assert cache.prepared_logo(good) is not None
    now[0] += 8
    assert cache.prepared_logo(good) is None
    assert request(qt_app, cache, good) is not None
    assert server["counts"] == {"/good": 2, "/bad": 2}


def test_memory_and_disk_eviction(qt_app, cache_factory, server):
    cache = cache_factory(memory_limit=2, disk_limit=750)
    urls = [server["base"] + f"/{n}" for n in range(4)]
    for url in urls:
        assert request(qt_app, cache, url) is not None
    assert cache.prepared_logo(urls[0]) is None
    assert cache.prepared_logo(urls[-1]) is not None
    files = list(cache.cache_dir.glob("*.logo"))
    assert sum(path.stat().st_size for path in files) <= 750
    assert len(files) < 4


def test_abandoned_managed_temp_file_counts_toward_disk_quota(cache_factory, tmp_path):
    cache = cache_factory(disk_limit=1_000)
    cache.cache_dir.mkdir(parents=True)
    orphan = cache._disk._path("https://img.test/interrupted").with_suffix(".tmp")
    orphan.write_bytes(b"x" * 1_000)
    unmanaged = cache.cache_dir / "keep-this.tmp"
    unmanaged.write_bytes(b"inside cache but not managed")
    outside = tmp_path / "user-file.tmp"
    outside.write_bytes(b"outside cache")

    cache._disk.save("https://img.test/new", png(), 60, 60)

    managed = [
        path
        for path in cache.cache_dir.iterdir()
        if len(path.stem) == 64
        and set(path.stem) <= set("0123456789abcdef")
        and path.suffix in {".logo", ".tmp"}
    ]
    assert sum(path.stat().st_size for path in managed) <= 1_000
    assert unmanaged.read_bytes() == b"inside cache but not managed"
    assert outside.read_bytes() == b"outside cache"


def test_failed_partial_cache_write_removes_its_temp_file(cache_factory, tmp_path, monkeypatch):
    cache = cache_factory()
    url = "https://img.test/partial"
    temporary = cache._disk._path(url).with_suffix(".tmp")
    outside = tmp_path / "user-file.tmp"
    outside.write_bytes(b"untouched")
    real_fdopen = importlib.import_module("os").fdopen

    class PartialWrite:
        def __init__(self, descriptor):
            self.file = real_fdopen(descriptor, "wb")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.file.close()

        def write(self, data):
            self.file.write(data[:8])
            self.file.flush()
            raise OSError("simulated interrupted write")

    logos = importlib.import_module("luna_iptv.logos")
    monkeypatch.setattr(logos.os, "fdopen", lambda descriptor, _mode: PartialWrite(descriptor))

    cache._disk.save(url, png(), 60, 60)

    assert not temporary.exists()
    assert outside.read_bytes() == b"untouched"


def test_redirects_do_not_forward_auth_or_cookies(qt_app, cache_factory, server):
    server["routes"]["/redirect"] = (
        302,
        {"Location": "/final", "Set-Cookie": "secret=credential"},
        b"",
        0,
    )
    cache = cache_factory()
    assert request(qt_app, cache, server["base"] + "/redirect") is not None
    assert server["counts"] == {"/redirect": 1, "/final": 1}
    for headers in server["headers"]:
        assert "Authorization" not in headers
        assert "Cookie" not in headers
        assert "Referer" not in headers


@pytest.mark.parametrize("target", ["file:///etc/passwd", "ftp://127.0.0.1/logo", "/loop"])
def test_unsafe_or_looping_redirect_rejected(qt_app, cache_factory, server, target):
    server["routes"]["/loop"] = (302, {"Location": target}, b"", 0)
    assert request(qt_app, cache_factory(), server["base"] + "/loop") is None
    assert server["counts"]["/loop"] <= 6


def test_timeout_and_closed_cache_has_no_late_notification(qt_app, cache_factory, server):
    server["routes"]["/slow"] = (200, {}, png(), 0.4)
    cache = cache_factory(timeout_ms=70)
    started = time.monotonic()
    assert request(qt_app, cache, server["base"] + "/slow") is None
    assert time.monotonic() - started < 0.3
    seen = []
    cache.ready.connect(seen.append)
    cache.request_logo(server["base"] + "/later")
    cache.close()
    for _ in range(20):
        qt_app.processEvents()
        time.sleep(0.01)
    assert seen == []


def test_visible_priority_discards_obsolete_queue_and_bounds_parallelism(
    qt_app, cache_factory, server
):
    cache = cache_factory()
    for n in range(10):
        server["routes"][f"/{n}"] = (200, {}, png(), 0.08)
    urls = [server["base"] + f"/{n}" for n in range(10)]
    cache.request_visible(urls)
    spin(qt_app, lambda: sum(server["counts"].values()) == 4)
    cache.request_visible([server["base"] + "/new"])
    spin(qt_app, lambda: cache.prepared_logo(server["base"] + "/new") is not None)
    assert all(server["counts"][f"/{n}"] == 0 for n in range(4, 10))
    assert server["peak"] <= 4


def test_remote_m3u_logos_are_resolved_and_confined_to_http():
    playlist = parse_m3u(
        '#EXTM3U\n#EXTINF:-1 tvg-logo="../logo.png",Good\nhttps://test/stream\n'
        '#EXTINF:-1 tvg-logo="file:///tmp/private.png",Bad\nhttps://test/stream2\n',
        "https://test/lists/channels.m3u",
    )
    assert [channel.logo for channel in playlist.channels] == ["https://test/logo.png", ""]


def test_xtream_relative_logo_uses_server_base(server):
    server["routes"]["/player_api.php?username=u&password=p"] = (
        200,
        {},
        json.dumps({"user_info": {"auth": 1}}).encode(),
        0,
    )
    for mode in ("live", "vod", "series"):
        server["routes"][f"/player_api.php?username=u&password=p&action=get_{mode}_categories"] = (
            200,
            {},
            b"[]",
            0,
        )
    for action in ("get_live_streams", "get_vod_streams", "get_series"):
        rows = (
            [{"stream_id": 1, "stream_icon": "img/logo.png"}]
            if action == "get_live_streams"
            else []
        )
        server["routes"][f"/player_api.php?username=u&password=p&action={action}"] = (
            200,
            {},
            json.dumps(rows).encode(),
            0,
        )
    assert XtreamClient(server["base"], "u", "p").catalog().channels[0].logo == (
        server["base"] + "/img/logo.png"
    )


def test_viewport_only_fetches_visible_logos_and_tracks_filter_and_scroll(
    qt_app, cache_factory, server
):
    from PySide6.QtWidgets import QListView

    from luna_iptv.library import ChannelDelegate, ChannelFilter, ChannelModel
    from luna_iptv.models import Channel

    logos = importlib.import_module("luna_iptv.logos")
    cache = cache_factory()
    model = ChannelModel()
    model.reset(
        [
            Channel(
                str(n),
                f"Channel {n}",
                server["base"] + f"/STREAM-{n}",
                logo=server["base"] + f"/logo-{n}",
            )
            for n in range(100)
        ],
        set(),
    )
    proxy = ChannelFilter()
    proxy.setSourceModel(model)
    view = QListView()
    view.setModel(proxy)
    view.setUniformItemSizes(True)
    view.setItemDelegate(ChannelDelegate(view, logos=cache))
    view.resize(300, 210)
    controller = logos.LogoViewportController(view, cache)
    view.show()
    try:
        spin(qt_app, lambda: cache.prepared_logo(server["base"] + "/logo-0") is not None)
        assert len(server["counts"]) <= 4
        view.scrollTo(proxy.index(70, 0), QListView.PositionAtTop)
        spin(qt_app, lambda: cache.prepared_logo(server["base"] + "/logo-70") is not None)
        proxy.query = "Channel 99"
        proxy.refresh()
        spin(qt_app, lambda: cache.prepared_logo(server["base"] + "/logo-99") is not None)
        assert not any("STREAM" in path for path in server["counts"])
        controller.close()
        view.close()
    finally:
        controller.close()
        view.close()
        view.deleteLater()


def test_delegate_reads_prepared_pixmap_without_starting_io(qt_app):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QStyleOptionViewItem

    from luna_iptv.library import ChannelDelegate, ChannelModel
    from luna_iptv.models import Channel

    class Prepared:
        calls = []

        def prepared_logo(self, url):
            self.calls.append(url)
            return QPixmap.fromImage(QImage.fromData(png()))

    prepared = Prepared()
    model = ChannelModel()
    model.reset([Channel("id", "Logo", "http://test/stream", logo="http://test/icon")], set())
    delegate = ChannelDelegate(logos=prepared)
    canvas = QImage(300, 69, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 69)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()
    assert prepared.calls == ["http://test/icon"]
    assert canvas.pixelColor(30, 34) == Qt.red


def test_local_playlist_logo_is_relative_and_loaded_with_same_bounds(
    qt_app, cache_factory, tmp_path
):
    artwork = tmp_path / "icon.png"
    artwork.write_bytes(png())
    playlist = parse_m3u(
        '#EXTM3U\n#EXTINF:-1 tvg-logo="icon.png",Local\nvideo.ts\n',
        (tmp_path / "local.m3u").as_uri(),
    )
    assert playlist.channels[0].logo == str(artwork)
    assert request(qt_app, cache_factory(), playlist.channels[0].logo) is not None


def test_svg_and_malformed_disk_entry_are_not_decoded(qt_app, cache_factory, server):
    server["routes"]["/svg"] = (
        200,
        {},
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>',
        0,
    )
    cache = cache_factory()
    assert request(qt_app, cache, server["base"] + "/svg") is None
    url = server["base"] + "/good"
    assert request(qt_app, cache, url) is not None
    for entry in cache.cache_dir.glob("*.logo"):
        entry.write_bytes(b"corrupt-cache")
    cache.close()
    assert request(qt_app, cache_factory(), url) is not None
    assert server["counts"]["/good"] == 2


def test_object_deletion_during_network_request_emits_no_late_signal(qt_app, cache_factory, server):
    from PySide6.QtCore import QCoreApplication, QEvent

    server["routes"]["/delayed"] = (200, {}, png(), 0.2)
    cache = cache_factory()
    seen = []
    cache.ready.connect(seen.append)
    cache.request_logo(server["base"] + "/delayed")
    spin(qt_app, lambda: server["counts"]["/delayed"] == 1)
    cache.close()
    cache.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    for _ in range(30):
        qt_app.processEvents()
        time.sleep(0.01)
    assert seen == []


def test_nonregular_local_logo_is_rejected_without_blocking(qt_app, cache_factory, tmp_path):
    import os

    pipe = tmp_path / "not-an-image.png"
    os.mkfifo(pipe)
    # A writer avoids hanging a broken implementation while proving it accepted a FIFO.
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            try:
                fd = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                stop.wait(0.005)
                continue
            with os.fdopen(fd, "wb") as file:
                file.write(png())
            return

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        assert request(qt_app, cache_factory(), str(pipe)) is None
    finally:
        stop.set()
        thread.join(timeout=1)
