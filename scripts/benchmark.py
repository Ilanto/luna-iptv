"""Time the paths that grow with the catalogue, on a synthetic library (offscreen).

    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark.py [channels]

Prints seconds per step and exits non-zero when a step exceeds its budget, so a slow
change shows up before a person with a big provider list notices it.
"""

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from luna_iptv.epg import GuideIndex  # noqa: E402
from luna_iptv.models import Channel, Programme  # noqa: E402
from luna_iptv.storage import Store  # noqa: E402

# Generous budgets for a modest laptop; the point is catching order-of-magnitude slips.
BUDGETS = {
    "store_replace": 12.0,
    "store_reload": 4.0,
    "window_open": 8.0,
    "refresh_library": 6.0,
    "section_switch": 1.5,
    "search": 1.5,
    "search_clear": 1.5,
    "home_refresh": 1.0,
    "guide_open": 2.0,
}


def catalogue(count):
    groups = [f"Grup {n}" for n in range(400)]
    live = count * 6 // 10
    films = count * 3 // 10
    items = [
        Channel(f"l{n}", f"Kanal {n}", f"file:///l{n}.ts", group=groups[n % 400], tvg_id=f"t{n}")
        for n in range(live)
    ]
    items += [
        Channel(f"m{n}", f"Film {n}", f"file:///m{n}.mkv", kind="movie", group=groups[n % 97])
        for n in range(films)
    ]
    items += [
        Channel(f"s{n}", f"Dizi {n}", "", kind="series", group=groups[n % 53], series_id=str(n))
        for n in range(count - live - films)
    ]
    return items


def guide(channels, hours=24):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    programmes = []
    for channel in channels[:2000]:
        start = now - timedelta(hours=2)
        while start < now + timedelta(hours=hours):
            programmes.append(
                Programme(channel.tvg_id, "Program", start, start + timedelta(minutes=60), "")
            )
            start += timedelta(minutes=60)
    return GuideIndex(programmes)


def timed(results, name, action):
    started = time.perf_counter()
    value = action()
    results[name] = round(time.perf_counter() - started, 3)
    return value


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    app = QApplication.instance() or QApplication([])
    import luna_iptv.reminders as reminders

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from conftest import RecordingNotifications  # no desktop notifications while timing

    reminders.DesktopNotifications = RecordingNotifications
    from luna_iptv.window import MainWindow

    results = {}
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "library.sqlite3")
        store.save_source({"id": "big", "type": "m3u", "name": "Büyük liste"})
        channels = catalogue(count)
        timed(results, "store_replace", lambda: store.replace_channels("big", channels))
        timed(results, "store_reload", store.channels)
        window = timed(results, "window_open", lambda: MainWindow(store))
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        timed(results, "refresh_library", window.refresh_library)
        timed(results, "section_switch", lambda: window.set_section("live"))
        timed(results, "search", lambda: window.search.setText("kanal 4242"))
        timed(results, "search_clear", window.search.clear)
        if hasattr(window, "home_view"):
            timed(results, "home_refresh", window.home_view.refresh)
        window._guide_index["big"] = guide(window.model.channels)
        timed(results, "guide_open", lambda: window.set_section("guide"))
        app.processEvents()
        window.close()
        app.processEvents()
        store.close()
    slow = {name: value for name, value in results.items() if value > BUDGETS.get(name, 1e9)}
    print(json.dumps({"channels": count, "seconds": results, "over_budget": slow}, indent=2))
    return 1 if slow else 0


if __name__ == "__main__":
    raise SystemExit(main())
