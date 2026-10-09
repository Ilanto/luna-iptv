"""Bounded live cache options and validated seekable windows."""

import math

TIMESHIFT_CHOICES = (("30 dk", 30), ("Kapalı", 0), ("15 dk", 15), ("60 dk", 60))


def cache_minutes(value):
    return value if type(value) is int and value in (0, 15, 30, 60) else 30


def live_cache_options(minutes):
    """Budget RAM at 4 Mbit/s, shared by forward and backward packets."""
    minutes = cache_minutes(minutes)
    if not minutes:
        return {}
    budget = minutes * 60 * 500_000
    return {
        "cache": "yes",
        "cache-secs": str(minutes * 60),
        "demuxer-max-bytes": str(budget),
        "demuxer-max-back-bytes": str(budget),
        "demuxer-seekable-cache": "yes",
        "cache-on-disk": "no",
    }


def cached_ranges(state):
    """Merge overlapping ranges without inventing data across gaps."""
    raw = state.get("seekable-ranges", []) if isinstance(state, dict) else []
    ranges = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            ranges.append((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)
