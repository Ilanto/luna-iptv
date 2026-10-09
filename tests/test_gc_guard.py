"""Garbage is collected on the main thread, never in a worker."""

import gc

from luna_iptv.gc_guard import MainThreadCollector


def test_collector_disables_automatic_gc_and_collects_past_thresholds(qt_app, monkeypatch):
    was_enabled = gc.isenabled()
    collector = MainThreadCollector(interval_ms=50)
    try:
        assert not gc.isenabled() and collector.timer.isActive()
        monkeypatch.setattr(gc, "get_count", lambda: (10**6, 0, 0))
        collected = []
        monkeypatch.setattr(gc, "collect", lambda generation=2: collected.append(generation) or 0)
        assert collector.check() == 0 and collected == [0]
        monkeypatch.setattr(gc, "get_count", lambda: (0, 0, 10**6))
        assert collector.check() == 2
    finally:
        collector.timer.stop()
        if was_enabled:
            gc.enable()
