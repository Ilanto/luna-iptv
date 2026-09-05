from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid

from luna_iptv.media_info import MediaInfo
from luna_iptv.models import Channel
from luna_iptv.player import Player
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.mark.parametrize(
    ("width", "height", "interlaced", "expected"),
    [
        (720, 576, False, "SD · 576p"),
        (1280, 720, False, "HD · 720p"),
        (1920, 1080, True, "Full HD · 1080i"),
        (2560, 1440, False, "QHD · 1440p"),
        (3840, 2160, False, "4K · 2160p"),
        (1080, 1920, False, "Full HD · 1080p"),
        (1920, 1080, None, "Full HD"),
        (1600, 900, False, "HD"),
    ],
)
def test_source_dimensions_use_only_justified_scan_labels(
    width: int, height: int, interlaced: bool | None, expected: str
) -> None:
    info = MediaInfo()
    info.update("video-params", {"w": 640, "h": 360})
    info.update("video-dec-params", {"w": width, "h": height})
    if interlaced is not None:
        info.update("video-frame-info/interlaced", interlaced)

    assert info.dimensions == f"{width} × {height}"
    assert info.quality == expected


def test_dimensions_fall_back_when_decoder_metadata_is_partial() -> None:
    info = MediaInfo()
    info.update("video-params", {"w": 1280, "h": 720})
    info.update("video-dec-params", {"gamma": "pq"})

    assert info.dimensions == "1280 × 720"


def test_selected_tracks_fps_audio_layout_and_absent_bitrate() -> None:
    info = MediaInfo()
    info.update(
        "track-list",
        [
            {"id": 1, "type": "video", "selected": False, "codec": "hevc"},
            {
                "id": 2,
                "type": "video",
                "selected": True,
                "codec": "h264",
                "codec-desc": "H.264 / AVC",
            },
            {"id": 3, "type": "audio", "selected": True, "codec": "aac"},
        ],
    )
    info.update("audio-params", {"channels": "5.1", "hr-channels": "5.1"})
    info.update("container-fps", 25.0)

    assert info.video_codec == "H.264 / AVC"
    assert info.audio_codec == "AAC"
    assert info.audio_layout == "5.1"
    assert info.fps == "25 FPS"
    assert info.bitrate == "Bilgi yok"

    info.update("video-bitrate", 5_250_000)
    info.update("audio-bitrate", 0)
    assert info.bitrate == "Video 5,25 Mb/sn"


@pytest.mark.parametrize(
    ("params", "track", "expected"),
    [
        ({"gamma": "pq"}, {}, "HDR (PQ)"),
        ({"gamma": "hlg"}, {}, "HDR (HLG)"),
        ({"gamma": "bt.1886"}, {}, "SDR"),
        ({}, {}, "Bilgi yok"),
        ({"gamma": "pq"}, {"dolby-vision-profile": 8}, "Dolby Vision"),
    ],
)
def test_dynamic_range_describes_source_metadata_only(params, track, expected) -> None:
    info = MediaInfo()
    info.update("video-dec-params", params)
    if track:
        info.update("track-list", [{"type": "video", "selected": True, **track}])
    assert info.dynamic_range == expected


def test_reset_clears_every_stream_specific_value() -> None:
    info = MediaInfo()
    info.update("video-dec-params", {"w": 1920, "h": 1080, "gamma": "pq"})
    info.update("container-fps", 50)
    info.update("video-bitrate", 8_000_000)
    info.update("track-list", [{"type": "video", "selected": True, "codec": "h264"}])

    info.reset()

    assert info.dimensions == "Bilgi yok"
    assert info.quality == "Bilgi yok"
    assert info.video_codec == "Bilgi yok"
    assert info.fps == "Bilgi yok"
    assert info.bitrate == "Bilgi yok"
    assert info.dynamic_range == "Bilgi yok"


def test_buffer_indicator_handles_percent_and_playback_transitions() -> None:
    info = MediaInfo()
    assert info.buffer_text == ""

    info.begin_load()
    assert info.buffer_text == "Bağlanıyor…"
    info.update("paused-for-cache", True)
    assert info.buffer_text == "Arabellek…"

    for value, expected in [(0, "Arabellek · %0"), (43.6, "Arabellek · %44"), (100, "Arabellek · %100")]:
        info.update("cache-buffering-state", value)
        assert info.buffer_text == expected

    info.update("paused-for-cache", False)
    info.update("pause", True)
    assert info.buffer_text == "Bağlanıyor…"
    info.mark_loaded()
    assert info.buffer_text == ""
    info.update("idle-active", True)
    assert info.buffer_text == ""


def test_unsupported_optional_mpv_properties_do_not_disable_player(monkeypatch) -> None:
    observed: list[str] = []

    class Backend:
        def observe_property(self, name, _handler):
            observed.append(name)
            if name == "video-dec-params":
                raise AttributeError("property unavailable")

        def event_callback(self, _name):
            return lambda callback: callback

        def terminate(self):
            pass

    backend = Backend()
    binding = SimpleNamespace(MPV=lambda **_kwargs: backend)
    monkeypatch.setitem(sys.modules, "mpv", binding)

    player = Player()
    try:
        assert player._mpv is backend
        assert "video-dec-params" in observed
        assert "audio-params" in observed
    finally:
        player.shutdown()
        if player._termination is not None:
            player._termination.join(timeout=2)


def test_info_panel_buffer_and_fullscreen_controls_reset_between_streams(
    qt_app, tmp_path, monkeypatch
) -> None:
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    loads = []
    monkeypatch.setattr(window.player, "load", lambda *args, **kwargs: loads.append((args, kwargs)))
    monkeypatch.setattr(window.player, "stop", lambda: None)
    monkeypatch.setattr(window, "showFullScreen", lambda: None)
    monkeypatch.setattr(window, "showNormal", lambda: None)
    source_id = window.store.save_source(
        {"name": "Test", "type": "m3u", "location": "https://example.test/list"}
    )
    window.store.replace_channels(
        source_id,
        [
            Channel("one", "Birinci", "https://example.test/one"),
            Channel("two", "İkinci", "https://example.test/two"),
        ],
    )
    first, second = window.store.channels(source_id)

    try:
        assert window.info_panel.isHidden()
        assert window.info_button.accessibleName() == "Yayın bilgisini göster / gizle"
        assert window.buffer_label.accessibleName() == "Arabellek durumu"
        assert window.info_dimensions.textFormat() == Qt.PlainText

        window.play(first)
        window.info_button.click()
        window.player_property("video-dec-params", {"w": 1920, "h": 1080})
        window.player_property("video-frame-info/interlaced", False)
        window.player_property("paused-for-cache", True)
        window.player_property("cache-buffering-state", 42)
        assert window.info_dimensions.text() == "1920 × 1080"
        assert window.info_quality.text() == "Full HD · 1080p"
        assert window.buffer_label.text() == "Arabellek · %42"
        assert not window.buffer_label.isHidden()

        window.toggle_fullscreen()
        assert window._fullscreen is True
        assert not window.controls.isHidden()
        window.info_button.click()
        assert window.info_panel.isHidden()
        window.info_button.click()
        assert not window.info_panel.isHidden()

        window.play(second)
        assert len(loads) == 2
        assert window.info_dimensions.text() == "Bilgi yok"
        assert window.buffer_label.text() == "Bağlanıyor…"
        assert "%42" not in window.buffer_label.text()

        window.stop_playback()
        assert window.info_dimensions.text() == "Bilgi yok"
        assert window.buffer_label.isHidden()
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()
