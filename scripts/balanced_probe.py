#!/usr/bin/env python3
"""Exercise the balanced slice with a real window, local artwork/account API and generated video."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.window import MainWindow


def main():
    out = Path("work/qa/balanced").resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "20",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(out / "test.mkv"),
        ],
        check=True,
    )
    requests = Counter()
    now = int(time.time())

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            requests[path] += 1
            if path == "/player_api.php":
                payload = json.dumps(
                    {
                        "user_info": {
                            "auth": 1,
                            "status": "Active",
                            "created_at": now - 60 * 86400,
                            "exp_date": now + 90 * 86400,
                            "active_cons": "1",
                            "max_connections": "2",
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    app = QApplication([])
    apply_theme(app)
    for name, color in [("rose", "#e889a8"), ("cream", "#f8e7ec")]:
        logo = QImage(64, 40, QImage.Format_ARGB32)
        logo.fill(QColor(color))
        logo.save(str(out / f"{name}.png"))
    result = {"platform": app.platformName(), "DISPLAY_present": bool(os.getenv("DISPLAY"))}
    with tempfile.TemporaryDirectory(prefix="luna-balanced-") as directory:
        store = Store(Path(directory) / "library.sqlite3")
        source = {
            "id": "local",
            "name": "Evdeki yayınlar · test",
            "type": "xtream",
            "location": base,
            "username": "fixture",
            "password": "fixture",
        }
        store.save_source(source)
        store.replace_channels(
            "local",
            [
                Channel(
                    "one",
                    "Luna · Görüntü ve ses",
                    base + "/test.mkv",
                    group="Yerel testler",
                    logo=base + "/rose.png",
                ),
                Channel(
                    "two",
                    "İkinci test yayını",
                    base + "/test.mkv",
                    group="Yerel testler",
                    logo=base + "/cream.png",
                ),
                Channel(
                    "broken",
                    "Logosu olmayan yayın",
                    base + "/test.mkv",
                    group="Yerel testler",
                    logo=base + "/missing.png",
                ),
            ],
        )
        w = MainWindow(store)
        errors = []
        w.player.error.connect(errors.append)
        w.show()

        def wait(predicate, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                time.sleep(0.01)
            raise AssertionError("Balanced probe timeout: " + w.message.text())

        try:
            wait(lambda: w.logos.prepared_logo(base + "/rose.png") is not None)
            wait(lambda: w.logos.prepared_logo(base + "/cream.png") is not None)
            wait(lambda: requests["/missing.png"] == 1)
            assert requests["/test.mkv"] == 0
            assert w.logos.prepared_logo(base + "/missing.png") is None
            index = w.proxy.index(0, 0)
            QTest.mouseClick(
                w.channel_list.viewport(),
                Qt.LeftButton,
                pos=w.channel_list.visualRect(index).center(),
            )
            wait(lambda: not w._loading and w._position > 0.5)
            wait(lambda: w.info_dimensions.text() == "1280 × 720")
            assert w.info_button.isEnabled()
            width_before = w.width()
            w.info_button.click()
            wait(lambda: w.info_panel.isVisible())
            QTest.qWait(100)
            assert w.width() == width_before
            assert "H.264" in w.info_video_codec.text()
            assert "AAC" in w.info_audio_codec.text()
            assert w.info_fps.text() == "25 FPS"
            frame = w.video.grabFramebuffer()
            colors = {
                frame.pixelColor(x, y).name()
                for x in range(20, frame.width(), 60)
                for y in range(20, frame.height(), 60)
            }
            assert len(colors) > 8
            w.grab().save(str(out / "luna-balanced.png"))
            result["media"] = {
                "dimensions": w.info_dimensions.text(),
                "quality": w.info_quality.text(),
                "video_codec": w.info_video_codec.text(),
                "audio_codec": w.info_audio_codec.text(),
                "fps": w.info_fps.text(),
                "frame_colors": len(colors),
                "width_preserved": True,
            }
            before_backend = w.player._mpv
            w.source_combo.setCurrentIndex(w.source_combo.findData("local"))
            dialog = w.open_account(source)
            wait(lambda: not dialog.is_refreshing)
            assert dialog.status_value.text() == "Aktif"
            assert "1 / 2" in dialog.connections_value.text()
            assert "90 gün" in dialog.remaining_value.text()
            assert requests["/player_api.php"] == 1
            assert w.player._mpv is before_backend
            dialog.grab().save(str(out / "luna-account.png"))
            dialog.close()
            w.player_property("paused-for-cache", True)
            w.player_property("cache-buffering-state", 37)
            assert w.buffer_label.text() == "Arabellek · %37"
            QTest.qWait(100)
            assert w.width() == width_before, "Buffer status changed window width"
            w.grab().save(str(out / "luna-buffer-simulated.png"))
            w.player_property("paused-for-cache", False)
            assert w.buffer_label.isHidden()
            result["buffer"] = "UI state simulation at 37%; no provider stall simulated"
            result["logos"] = "visible logos loaded; missing fallback; no media opened by artwork"
            result["account"] = (
                "one local profile request; cached snapshot; 90 days; 1/2 connections"
            )
            result["request_counts"] = dict(requests)
            assert not errors, errors
            result["success"] = True
        finally:
            w.close()
            if w.player._termination:
                w.player._termination.join(timeout=20)
            app.processEvents()
            server.shutdown()
            server.server_close()
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
