#!/usr/bin/env python3
"""Exercise native rendering with generated local/HLS media; emits JSON evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication

from luna_iptv.player import Player, VideoWidget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="work/qa/player")
    parser.add_argument("--h264", action="store_true", help="Test H.264/AAC instead of MPEG-2/MP2")
    args = parser.parse_args()
    out = Path(args.output).resolve()
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
            "testsrc2=size=640x360:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "14",
            "-c:v",
            "libx264" if args.h264 else "mpeg2video",
            "-c:a",
            "aac" if args.h264 else "mp2",
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
            "1",
            "-hls_list_size",
            "0",
            str(out / "local.m3u8"),
        ],
        check=True,
    )
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append({"path": self.path, "header": self.headers.get("X-Luna-Probe")})
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(out)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    app = QApplication([])
    player = Player()
    widget = VideoWidget(player)
    widget.resize(960, 540)
    widget.setWindowTitle("Luna IPTV · Native Wayland render probe")
    props, errors, loaded = {}, [], []
    player.property_changed.connect(lambda k, v: props.update({k: v}))
    player.error.connect(errors.append)
    player.file_loaded.connect(lambda: loaded.append(True))
    widget.show()
    result = {
        "platform": app.platformName(),
        "DISPLAY_present": bool(os.getenv("DISPLAY")),
        "checks": {},
        "codec": "H.264/AAC" if args.h264 else "MPEG-2/MP2",
    }

    def wait(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(f"Timed out; errors={errors}; properties={props}")

    try:
        for name, url, headers in [
            ("local", str(media), {}),
            (
                "hls",
                f"http://127.0.0.1:{server.server_port}/local.m3u8",
                {"X-Luna-Probe": "one,two"},
            ),
        ]:
            before = len(loaded)
            player.load(url, headers)
            wait(lambda before=before: len(loaded) > before and (props.get("time-pos") or 0) > 0.5)
            player.set_property("mute", True)
            wait(lambda: props.get("mute") is True)
            player.pause_toggle()
            wait(lambda: props.get("pause") is True)
            player.command(["seek", 4, "absolute+exact"])
            wait(lambda: abs((props.get("time-pos") or 0) - 4) < 0.3)
            tracks = props.get("track-list") or []
            assert {"video", "audio"} <= {t["type"] for t in tracks}
            audio_id = next(t["id"] for t in tracks if t["type"] == "audio")
            player.set_property("aid", "no")
            wait(lambda: props.get("aid") is False or props.get("aid") == "no")
            player.set_property("aid", audio_id)
            wait(lambda audio_id=audio_id: props.get("aid") == audio_id)
            image = widget.grabFramebuffer()
            colors = {
                image.pixelColor(x, y).name()
                for x in range(50, image.width(), 70)
                for y in range(50, image.height(), 70)
            }
            assert len(colors) > 8
            image.save(str(out / f"{name}-native.png"))
            result["checks"][name] = {
                "position": props["time-pos"],
                "mute": props["mute"],
                "pause": props["pause"],
                "tracks": [t["type"] for t in tracks],
                "frame_colors": len(colors),
            }
            player.stop()
            wait(lambda: props.get("idle-active") is True)
        assert requests and all(r["header"] == "one,two" for r in requests)
        assert not errors, errors
        result["hls_requests"] = len(requests)
        result["hls_custom_headers"] = True
        result["success"] = True
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)
    finally:
        player.shutdown()
        widget.close()
        app.processEvents()
        server.shutdown()
        (out / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
