#!/usr/bin/env python3
"""Real native desktop workflow with isolated data and generated localhost media."""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.window import MainWindow


def main():
    out = Path("work/qa/app").resolve()
    out.mkdir(parents=True, exist_ok=True)
    media = out / "generated.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=960x540:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "20",
            "-c:v",
            "mpeg2video",
            "-c:a",
            "mp2",
            str(media),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-hls_time",
            "2",
            "-hls_list_size",
            "0",
            str(out / "stream.m3u8"),
        ],
        check=True,
    )

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    (out / "demo.m3u").write_text(
        '#EXTM3U x-tvg-url="guide.xml"\n#EXTINF:-1 tvg-id="luna.test" group-title="Yerel testler",Luna test yayını\nstream.m3u8\n#EXTINF:-1 group-title="Yerel testler" kind="movie",Görüntü ve ses testi\ngenerated.mkv\n#EXTINF:-1 tvg-id="luna.second" group-title="Yerel testler",İkinci test yayını\nstream.m3u8\n'
    )
    now = datetime.now(timezone.utc)

    def stamp(t):
        return t.strftime("%Y%m%d%H%M%S +0000")

    (out / "guide.xml").write_text(
        f'<tv><programme channel="luna.test" start="{stamp(now - timedelta(minutes=10))}" stop="{stamp(now + timedelta(minutes=20))}"><title>Yerel görüntü ve ses denemesi</title></programme><programme channel="luna.test" start="{stamp(now + timedelta(minutes=20))}" stop="{stamp(now + timedelta(minutes=50))}"><title>Sonraki program · test verisi</title></programme></tv>'
    )
    # Each run creates a fresh isolated library, never touches the user's app data.
    import tempfile

    data = Path(tempfile.mkdtemp(prefix="library-", dir=out))
    app = QApplication([])
    apply_theme(app)
    w = MainWindow(Store(data / "library.sqlite3"))
    w.show()
    props = {}
    errors = []
    w.player.property_changed.connect(lambda k, v: props.update({k: v}))
    w.player.error.connect(errors.append)

    def wait(predicate, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
            if predicate():
                return
        raise AssertionError("Timed out in UI smoke: " + w.message.text())

    results = {
        "platform": app.platformName(),
        "DISPLAY_present": bool(os.getenv("DISPLAY")),
        "checks": [],
    }
    try:
        wait(lambda: w.isVisible())
        w.grab().save(str(out / "luna-empty.png"))
        w.import_source(
            {
                "name": "Luna · Yerel deneme",
                "type": "m3u",
                "location": base + "/demo.m3u",
                "username": "",
                "password": "",
                "epg_url": "",
            }
        )
        wait(lambda: w.model.rowCount() == 3 and not w._busy)
        wait(lambda: bool(w._guide_data))
        assert w.proxy.rowCount() == 2
        results["checks"].append("async M3U + XMLTV import")
        w.search.setText("ikinci")
        assert w.proxy.rowCount() == 1
        w.search.clear()
        first = w.proxy.index(0, 0)
        QTest.mouseClick(
            w.channel_list.viewport(), Qt.LeftButton, pos=w.channel_list.visualRect(first).center()
        )
        wait(lambda: (props.get("time-pos") or 0) > 0.7 and not w._loading)
        assert "Yerel görüntü" in w.now_title.text()
        QTest.mouseClick(w.favorite_button, Qt.LeftButton)
        assert w.current.id in w.store.favorites()
        w.player.set_property("mute", True)
        wait(lambda: props.get("mute") is True)
        QTest.mouseClick(w.play_button, Qt.LeftButton)
        wait(lambda: props.get("pause") is True)
        image = w.video.grabFramebuffer()
        colors = {
            image.pixelColor(x, y).name()
            for x in range(20, image.width(), 50)
            for y in range(20, image.height(), 50)
        }
        assert len(colors) > 8
        w.grab().save(str(out / "luna-playing.png"))
        results["checks"].append("single-click native HLS rendering, EPG, favorite, pause, mute")
        w.toggle_fullscreen()
        wait(lambda: w.isFullScreen())
        assert not w.sidebar.isVisible()
        w.leave_fullscreen()
        wait(lambda: not w.isFullScreen())
        w.set_section("movie")
        movie = w.proxy.index(0, 0).data(Qt.UserRole)
        w.play(movie)
        wait(lambda: not w._loading and w._duration > 10)
        w.player.command(["seek", 8, "absolute+exact"])
        wait(lambda: abs(w._position - 8) < 0.8)
        w.player.set_property("pause", True)
        wait(lambda: props.get("pause") is True)
        movie_id = w.current.id
        w.stop_playback()
        wait(lambda: w._idle)
        w.save_progress()
        saved = w.store.progress(movie_id)
        assert saved[0] > 7 and saved[1] > 10, saved
        QTest.mouseClick(w.play_button, Qt.LeftButton)
        wait(lambda: not w._loading and not w._idle and w._position > 7)
        results["checks"].append("fullscreen and VOD stop/replay preserves resume")
        w.close()
        app.processEvents()
        reopened = Store(data / "library.sqlite3")
        assert movie_id in reopened.recent_ids()
        assert len(reopened.favorites()) == 1
        reopened.close()
        results["checks"].append("reopen persistence and orderly renderer shutdown")
        assert not errors, errors
        results["frame_colors"] = len(colors)
        results["success"] = True
    except Exception as exc:
        results["success"] = False
        results["error"] = str(exc)
        import traceback

        results["traceback"] = traceback.format_exc()
        if not w._closed:
            w.grab().save(str(out / "luna-failure.png"))
            w.close()
    finally:
        server.shutdown()
        server.server_close()
        app.processEvents()
    (out / "result.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if results["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
