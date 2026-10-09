"""Xtream archive eligibility without inspecting account credentials."""

from datetime import datetime, timedelta, timezone


def can_catchup(channel, programme, now=None):
    now = now or datetime.now(timezone.utc)
    return bool(
        channel.kind == "live"
        and channel.tv_archive
        and channel.tv_archive_duration > 0
        and channel.provider_key.startswith("live:")
        and programme.start.utcoffset() is not None
        and programme.end.utcoffset() is not None
        and now - timedelta(days=channel.tv_archive_duration) <= programme.start
        and programme.start < programme.end <= now
    )


def archive_days(value):
    try:
        return max(0, min(365, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0
