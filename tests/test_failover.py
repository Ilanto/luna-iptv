"""A live channel that will not open is tried in another source that has it."""

import pytest
from shiboken6 import isValid

from luna_iptv.library import channel_key
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_channel_key_ignores_quality_tags_and_spacing():
    assert channel_key("TRT 1 HD") == channel_key("TRT1 FHD") == channel_key("trt-1")
    assert channel_key("TRT 1") != channel_key("TRT 2")


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    for source, name in (("a", "Birinci"), ("b", "İkinci"), ("c", "Üçüncü")):
        store.save_source({"id": source, "type": "m3u", "name": name})
    store.replace_channels("a", [Channel("1", "TRT 1 HD", "file:///a1.ts")])
    store.replace_channels("b", [Channel("1", "TRT1 FHD", "file:///b1.ts")])
    store.replace_channels("c", [Channel("9", "Ulusal", "file:///c9.ts", tvg_id="trt1")])
    value = MainWindow(store)
    played = []
    monkeypatch.setattr(value.player, "load", lambda url, *a, **kw: played.append(url))
    value.played = played
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def channel(window, cid):
    return next(c for c in window.model.channels if c.id == cid)


def test_failed_live_channel_moves_to_another_source_once_each(window, qt_app):
    window.request_play(channel(window, "a:1"))
    window._playback_active = True
    assert window._fail_over()
    qt_app.processEvents()
    assert window.played[-1] == "file:///b1.ts"
    assert "İkinci" in window.message.text()
    window._playback_active = True
    assert not window._fail_over()  # a:1 and b:1 tried; no other copy by name
    window.request_play(channel(window, "a:1"))  # a person's own choice starts afresh
    assert window._failover_tried == set()


def test_same_guide_id_counts_as_the_same_channel(window):
    trt = Channel("x:1", "Başka ad", "file:///x.ts", tvg_id="trt1")
    assert window.alternative_channel(trt).id == "c:9"
    film = Channel("a:m", "TRT 1 HD", "file:///m.mkv", kind="movie")
    assert window.alternative_channel(film) is None
