"""Real frame regressions for Qt/libmpv state sharing; generated media only."""

import os
import shutil
import subprocess
import time

import pytest


@pytest.mark.parametrize("codec", ["h264", "hevc10"])
def test_subtitles_preserve_video_through_repaints_and_window_transitions(codec, tmp_path, qt_app):
    # Qt enables blending before paintGL. After libass changes its factors,
    # inheriting that state corrupts the next video frame on NVIDIA/NVDEC.
    # Check actual pixels, including after subtitles are disabled again.
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.skip("Native rendering requires a desktop display")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for generated media")
    encoder = "libx265" if codec == "hevc10" else "libx264"
    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
    ).stdout
    if encoder not in encoders:
        pytest.skip(f"{encoder} needed for generated media")

    from PySide6.QtWidgets import QWidget

    from luna_iptv.models import Channel
    from luna_iptv.storage import Store
    from luna_iptv.theme import apply_theme
    from luna_iptv.window import MainWindow

    media = tmp_path / "bars.mkv"
    options = ["-pix_fmt", "yuv420p"]
    if codec == "hevc10":
        options = [
            "-pix_fmt",
            "yuv420p10le",
            "-x265-params",
            "log-level=error:pools=2",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            "-colorspace",
            "bt2020nc",
        ]
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "smptebars=size=640x360:rate=24",
            "-t",
            "20",
            "-c:v",
            encoder,
            "-preset",
            "ultrafast",
            *options,
            str(media),
        ],
        check=True,
        capture_output=True,
    )
    subtitle = tmp_path / "local.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:20,000\nLuna subtitle regression\n")
    store = Store(tmp_path / "library.sqlite3")
    source_id = store.save_source({"name": "Generated", "type": "m3u"})
    store.replace_channels(source_id, [Channel("bars", "Colour bars", str(media))])
    apply_theme(qt_app)
    window = MainWindow(store)
    window.setWindowTitle("Luna · Yerel görüntü regresyon testi")
    cover = QWidget()
    cover.setWindowTitle("Luna · Geçici test penceresi")
    cover.resize(1400, 900)
    props, errors = {}, []
    window.player.property_changed.connect(lambda key, value: props.update({key: value}))
    window.player.error.connect(errors.append)
    backend = window.player._mpv
    window.show()

    def wait_for(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        assert predicate(), f"Timed out; player errors: {errors}"

    def pump(seconds=0.15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.005)

    def check_bars(stage):
        frame = window.video.grabFramebuffer()
        assert not frame.isNull(), stage
        # Sample within the image, excluding letter/pillarboxing and subtitles.
        width = min(frame.width(), frame.height() * 16 / 9)
        height = width * 9 / 16
        left, top = (frame.width() - width) / 2, (frame.height() - height) / 2
        samples = []
        for x in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85):
            column = [
                frame.pixelColor(int(left + width * x), int(top + height * y / 100)).getRgb()[:3]
                for y in range(20, 60)
            ]
            span = max(
                max(rgb[c] for rgb in column) - min(rgb[c] for rgb in column) for c in range(3)
            )
            assert span <= 8, f"{stage}: vertical colour bar is corrupted (span={span})"
            samples.append(column[0])
        assert len(set(samples)) >= 5, f"{stage}: video is blank"
        assert min(samples[0]) > 100, f"{stage}: grey bar is black or discoloured"
        assert window.player._mpv is backend
        assert not errors

    try:
        window.play(window.model.channels[0])
        wait_for(lambda: (props.get("time-pos") or 0) > 0.4)
        check_bars("before subtitles")
        window.player.command(["sub-add", str(subtitle), "select"])
        wait_for(lambda: any(t.get("type") == "sub" for t in props.get("track-list", [])))
        position = props.get("time-pos") or 0
        wait_for(lambda: (props.get("time-pos") or 0) > position + 0.4)
        check_bars("subtitles enabled")
        window.player.set_property("pause", True)
        wait_for(lambda: props.get("pause") is True)
        check_bars("paused repaint")

        cover.show()
        pump()
        cover.close()
        window.hide()
        pump()
        window.show()
        wait_for(lambda: window.windowHandle().isExposed())
        check_bars("window restored")
        window.toggle_info_panel()
        pump()
        check_bars("information panel open")
        window.toggle_info_panel()
        window.resize(1221, 791)
        pump()
        check_bars("video resized")

        window.player.set_property("sid", "no")
        wait_for(lambda: props.get("sid") is False or props.get("sid") == "no")
        check_bars("subtitles disabled")
        window.player.set_property("pause", False)
        wait_for(lambda: props.get("pause") is False)
        position = props.get("time-pos") or 0
        wait_for(lambda: (props.get("time-pos") or 0) > position + 0.2)
        check_bars("playback resumed")
    finally:
        cover.close()
        window.close()
        if window.player._termination:
            window.player._termination.join(timeout=20)
        qt_app.processEvents()
