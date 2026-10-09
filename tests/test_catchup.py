"""Provider archive metadata, eligibility and guide actions with synthetic sources."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from test_guide_view import window as window

from luna_iptv.catchup import can_catchup
from luna_iptv.guide_view import ProgrammeCard
from luna_iptv.models import Channel, Programme
from luna_iptv.network import XtreamClient
from luna_iptv.storage import Store

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def channel():
    return Channel(
        "c",
        "Kanal",
        "https://example.test/live",
        provider_key="live:42",
        tv_archive=True,
        tv_archive_duration=3,
    )


def programme(start=NOW - timedelta(hours=2), end=NOW - timedelta(hours=1)):
    return Programme("epg", "Gece Ayı", start, end, "")


def test_url_escapes_path_segments_and_keeps_programme_time():
    client = XtreamClient("https://example.test/base", "sample/user", "sample pass")
    assert client.timeshift_url("42", NOW, 60) == (
        "https://example.test/base/timeshift/sample%2Fuser/sample%20pass/60/2026-10-09:12-00/42.ts"
    )
    with pytest.raises(ValueError):
        client.timeshift_url("42", NOW, 0)
    with pytest.raises(ValueError):
        client.timeshift_url("42", NOW.replace(tzinfo=None), 60)


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (NOW - timedelta(days=3), NOW - timedelta(days=3, hours=-1), True),
        (NOW - timedelta(days=3, seconds=1), NOW - timedelta(days=2), False),
        (NOW - timedelta(hours=1), NOW, True),
        (NOW - timedelta(minutes=5), NOW + timedelta(minutes=5), False),
        (NOW + timedelta(hours=1), NOW + timedelta(hours=2), False),
        (NOW, NOW, False),
    ],
)
def test_archive_window_limits(start, end, expected):
    assert can_catchup(channel(), programme(start, end), NOW) is expected


def test_nonarchive_or_nonlive_never_eligible():
    item = channel()
    item.tv_archive = False
    assert not can_catchup(item, programme(), NOW)
    item.tv_archive = True
    item.kind = "movie"
    assert not can_catchup(item, programme(), NOW)
    item.kind = "live"
    item.tv_archive_duration = 0
    assert not can_catchup(item, programme(), NOW)


def test_import_persists_archive_flags_and_handles_bad_provider_values(tmp_path, monkeypatch):
    client = XtreamClient("https://example.test", "synthetic", "synthetic")

    def api(action="", **kwargs):
        if not action:
            return {"user_info": {"auth": 1}}
        if action == "get_live_streams":
            return [
                {"stream_id": 42, "tv_archive": "1", "tv_archive_duration": "3"},
                {"stream_id": 43, "tv_archive": 0, "tv_archive_duration": "bad"},
            ]
        return []

    monkeypatch.setattr(client, "_api", api)
    playlist = client.catalog()
    assert playlist.channels[0].tv_archive
    assert playlist.channels[0].tv_archive_duration == 3
    assert not playlist.channels[1].tv_archive
    assert playlist.channels[1].tv_archive_duration == 0
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    store.save_source({"id": "s", "type": "xtream", "name": "Fake"})
    saved = store.replace_channels("s", playlist.channels)
    assert saved[0].tv_archive_duration == 3
    store.close()
    store = Store(path)
    assert store.channels()[0].tv_archive_duration == 3
    assert store.channels()[0].tv_archive
    store.close()


def test_legacy_database_migrates_archive_columns(tmp_path):
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    store.save_source({"id": "s", "type": "m3u", "name": "Fake"})
    store.replace_channels("s", [Channel("c", "Kanal", "file:///tmp/a.ts")])
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE channels DROP COLUMN tv_archive")
        db.execute("ALTER TABLE channels DROP COLUMN tv_archive_duration")
    store = Store(path)
    assert store.channels()[0].tv_archive is False
    assert store.channels()[0].tv_archive_duration == 0
    store.close()


def test_guide_offers_archive_only_for_eligible_past_and_record_for_future(qt_app):
    item = channel()
    card = ProgrammeCard(item, programme(), now=NOW)
    assert card.catchup_button is not None
    assert card.record_button is None
    chosen = []
    card.catchup.connect(lambda c, p, chosen=chosen: chosen.append((c, p)))
    card.catchup_button.click()
    assert chosen[0][0] is item
    for start, end in [
        (NOW, NOW + timedelta(hours=1)),
        (NOW + timedelta(hours=1), NOW + timedelta(hours=2)),
    ]:
        card = ProgrammeCard(item, programme(start, end), now=NOW)
        assert card.record_button is not None
        assert card.catchup_button is None
        chosen = []
        card.record.connect(lambda c, p, chosen=chosen: chosen.append((c, p)))
        card.record_button.click()
        assert chosen[0][0] is item
    item.tv_archive = False
    card = ProgrammeCard(item, programme(), now=NOW)
    assert card.catchup_button is None
    assert card.record_button is None
    card.close()


def test_window_catchup_uses_nonlive_title_and_original_parental_check(window, monkeypatch):
    item = window.store.channels()[0]
    item.tv_archive, item.tv_archive_duration, item.provider_key = True, 3, "live:42"
    now = datetime.now(timezone.utc)
    past = programme(now - timedelta(hours=2), now - timedelta(hours=1))
    monkeypatch.setattr(
        window,
        "source_for",
        lambda c: {
            "id": "home",
            "type": "xtream",
            "location": "https://example.test",
            "username": "synthetic",
            "password": "synthetic",
        },
    )
    attempts, played = [], []
    monkeypatch.setattr(window, "unlock_channel", lambda c: attempts.append(c.id) or False)
    monkeypatch.setattr(window, "request_play", lambda c, **kw: played.append(c))
    window.play_catchup(item, past)
    assert attempts == [item.id] and played == []
    monkeypatch.setattr(window, "unlock_channel", lambda c: True)
    window.play_catchup(item, past)
    assert played[0].kind == "movie"
    assert played[0].name == past.title
    assert played[0].parental_id == item.id
    assert "/timeshift/" in played[0].url


def test_guide_date_picker_reaches_provider_archive_days(qt_app):
    from PySide6.QtCore import QDate

    from luna_iptv.epg import GuideIndex
    from luna_iptv.guide_view import GuideView

    guide = GuideView()
    item = channel()
    guide.set_rows([(item, GuideIndex([programme()]))])
    assert not guide.archive_date.isHidden()
    assert guide.archive_date.minimumDate() == QDate.currentDate().addDays(-3)
    date = QDate.currentDate().addDays(-2)
    guide.archive_date.setDate(date)
    assert guide.day == date.toPython()
    guide.close()
