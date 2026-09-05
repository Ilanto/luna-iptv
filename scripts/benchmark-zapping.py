"""Compare local switch-to-visible-frame latency using generated solid colours."""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

output = Path(sys.argv[1] if len(sys.argv) > 1 else "work/qa/zapping-after.json").resolve()
output.parent.mkdir(parents=True, exist_ok=True)
app = QApplication([])
with tempfile.TemporaryDirectory(prefix="luna-zapping-") as tmp:
    root = Path(tmp)
    for color in ("red", "blue"):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:size=640x360:rate=25",
                "-t",
                "20",
                "-c:v",
                "mpeg2video",
                str(root / f"{color}.mkv"),
            ],
            check=True,
        )
    store = Store(root / "data/library.sqlite3")
    source_id = store.save_source({"name": "Synthetic", "type": "m3u"})
    store.replace_channels(
        source_id, [Channel(color, color, str(root / f"{color}.mkv")) for color in ("red", "blue")]
    )
    w = MainWindow(store)
    errors, loaded, samples = [], [], []
    w.player.error.connect(errors.append)
    w.player.file_loaded.connect(lambda: loaded.append(time.perf_counter()))
    w.show()
    backend = w.player._mpv
    try:
        for i in range(24):
            channel = w.model.channels[i % 2]
            previous = len(loaded)
            start = time.perf_counter()
            w.play(channel)
            while time.perf_counter() - start < 10:
                app.processEvents()
                if len(loaded) > previous:
                    frame = w.video.grabFramebuffer()
                    pixel = frame.pixelColor(frame.width() // 2, frame.height() // 2)
                    matches = (
                        (pixel.red() > 180 and pixel.blue() < 70)
                        if i % 2 == 0
                        else (pixel.blue() > 180 and pixel.red() < 70)
                    )
                    if matches:
                        break
                time.sleep(0.005)
            else:
                raise AssertionError("No expected frame; " + str(errors))
            elapsed = (time.perf_counter() - start) * 1000
            assert w.player._mpv is backend and not errors
            if i >= 4:
                samples.append(round(elapsed, 2))
        result = {
            "platform": app.platformName(),
            "DISPLAY_present": bool(os.getenv("DISPLAY")),
            "fixture": "local 640x360 MPEG2 red/blue; 4 warmup + 20 switches",
            "measurement": "MainWindow.play to confirmed target framebuffer colour",
            "single_backend": True,
            "samples_ms": samples,
            "median_ms": round(statistics.median(samples), 2),
            "p95_ms": sorted(samples)[18],
            "max_ms": max(samples),
        }
        output.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        w.close()
        if w.player._termination:
            w.player._termination.join(timeout=20)
        app.processEvents()
