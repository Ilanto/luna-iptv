from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from luna_iptv.epg import now_next, parse_xmltv
from luna_iptv.models import Channel, Programme
from luna_iptv.playlist import parse_m3u
from luna_iptv.storage import Store


def test_m3u_parses_quoted_commas_attributes_headers_and_bom() -> None:
    playlist = parse_m3u(
        '\ufeff#EXTM3U url-tvg="https://guide.test/a.xml,https://guide.test/b.xml"\n'
        '#EXTINF:-1 tvg-id="news.1" tvg-logo="https://img.test/a.png" '
        'group-title="News, World",News, International\n'
        "#EXTVLCOPT:http-user-agent=Luna Test\n"
        "#EXTVLCOPT:http-referrer=https://ref.test/\n"
        "https://media.test/live.m3u8|Authorization=Bearer%20token&X-Test=yes\n"
    )

    assert playlist.epg_urls == ["https://guide.test/a.xml", "https://guide.test/b.xml"]
    assert playlist.warnings == []
    assert playlist.channels == [
        Channel(
            id=playlist.channels[0].id,
            name="News, International",
            url="https://media.test/live.m3u8",
            group="News, World",
            tvg_id="news.1",
            logo="https://img.test/a.png",
            headers={
                "User-Agent": "Luna Test",
                "Referer": "https://ref.test/",
                "Authorization": "Bearer token",
                "X-Test": "yes",
            },
        )
    ]


def test_m3u_ids_are_stable_and_duplicate_entries_get_distinct_ids() -> None:
    text = '#EXTM3U\n#EXTINF:-1 tvg-id="same",One\nhttps://x.test/1\n'
    first = parse_m3u(text).channels[0].id
    assert parse_m3u(text).channels[0].id == first

    duplicate = parse_m3u(text + '#EXTINF:-1 tvg-id="same",One\nhttps://x.test/1\n')
    assert len({channel.id for channel in duplicate.channels}) == 2


def test_m3u_resolves_relative_network_urls_and_rejects_unsafe_streams() -> None:
    playlist = parse_m3u(
        "#EXTM3U\n"
        "#EXTINF:-1,Relative\nstreams/live.ts\n"
        "#EXTINF:-1,Local file\nfile:///etc/passwd\n"
        "#EXTINF:-1,Javascript\njavascript:alert(1)\n",
        "https://provider.test/lists/main.m3u",
    )

    assert [channel.url for channel in playlist.channels] == [
        "https://provider.test/lists/streams/live.ts"
    ]
    assert len(playlist.warnings) == 2
    assert all(
        "unsupported" in warning.lower() or "local" in warning.lower()
        for warning in playlist.warnings
    )


def test_m3u_resolves_local_relative_paths_and_accepts_supported_schemes(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.m3u"
    playlist = parse_m3u(
        "#EXTM3U\n"
        "#EXTINF:-1,Clip\nmedia/clip.ts\n"
        "#EXTINF:-1,UDP\nudp://@239.0.0.1:1234\n"
        "#EXTINF:-1,RTSP\nrtsp://camera.test/live\n",
        str(playlist_file),
    )

    assert playlist.channels[0].url == str(tmp_path / "media" / "clip.ts")
    assert [channel.url for channel in playlist.channels[1:]] == [
        "udp://@239.0.0.1:1234",
        "rtsp://camera.test/live",
    ]


def test_hls_manifest_is_a_single_stream_instead_of_an_empty_playlist() -> None:
    playlist = parse_m3u(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10,\nsegment-1.ts\n#EXT-X-ENDLIST\n",
        "https://media.test/channel/index.m3u8",
    )

    assert len(playlist.channels) == 1
    assert playlist.channels[0].url == "https://media.test/channel/index.m3u8"
    assert playlist.channels[0].name == "index"


def test_malformed_m3u_is_skipped_with_actionable_warning() -> None:
    playlist = parse_m3u("#EXTM3U\n#EXTINF:-1,Missing URL\n# comment\n")
    assert playlist.channels == []
    assert playlist.warnings and "Missing URL" in playlist.warnings[0]


def test_remote_m3u_resolves_safe_epg_and_rejects_local_epg_reference() -> None:
    playlist = parse_m3u(
        '#EXTM3U url-tvg="guide.xml,file:///etc/passwd,javascript:alert(1)"\n'
        "#EXTINF:-1,Channel\nstream.ts\n",
        "https://provider.test/nested/list.m3u",
    )

    assert playlist.epg_urls == ["https://provider.test/nested/guide.xml"]
    assert len(playlist.warnings) == 2


@pytest.mark.parametrize(
    "document",
    [
        "<!doctype html><html><body>provider error</body></html>",
        '{"error":"authentication failed","url":"https://media.test/fake.ts"}',
    ],
)
def test_non_m3u_error_documents_do_not_become_channels(document: str) -> None:
    playlist = parse_m3u(document, "https://provider.test/list.m3u")
    assert playlist.channels == []
    assert playlist.warnings


def test_m3u_infers_vod_kind_and_normalizes_unknown_kind() -> None:
    playlist = parse_m3u(
        "#EXTM3U\n"
        "#EXTINF:-1,Movie\nhttps://media.test/movie.mkv\n"
        '#EXTINF:-1 kind="unknown",Visible fallback\nhttps://media.test/live.ts\n'
    )
    assert [channel.kind for channel in playlist.channels] == ["movie", "live"]


def test_xmltv_parses_offsets_fractional_seconds_and_unicode() -> None:
    programmes = parse_xmltv(
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<tv><programme channel="ch1" start="20260905123045.5 +0300" '
        b'stop="20260905140000 +0300"><title>G\xc3\xbcncel</title>'
        b"<desc>Haberler</desc></programme></tv>"
    )

    assert programmes == [
        Programme(
            channel_id="ch1",
            title="Güncel",
            start=datetime(2026, 9, 5, 9, 30, 45, 500000, tzinfo=timezone.utc),
            end=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
            description="Haberler",
        )
    ]


def test_xmltv_allows_external_doctype_without_resolving_it() -> None:
    programmes = parse_xmltv(
        b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "https://invalid.test/xmltv.dtd">'
        b'<tv><programme channel="x" start="20260905120000" stop="20260905130000">'
        b"<title>UTC programme</title></programme></tv>"
    )
    assert programmes[0].start == datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "document",
    [
        b'<!DOCTYPE tv [<!ENTITY x "expanded">]><tv><programme channel="x" '
        b'start="20260905120000" stop="20260905130000"><title>&x;</title></programme></tv>',
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE tv [<!ENTITY x "expanded">]>'
        "<tv></tv>".encode("utf-16"),
    ],
)
def test_xmltv_rejects_internal_entities_including_utf16(document: bytes) -> None:
    with pytest.raises(ValueError, match="DTD|entity"):
        parse_xmltv(document)


def test_xmltv_rejects_malformed_dates_and_non_tv_roots() -> None:
    with pytest.raises(ValueError, match="Invalid XMLTV timestamp"):
        parse_xmltv(
            b'<tv><programme channel="x" start="20261305120000" stop="20260905130000"/></tv>'
        )
    with pytest.raises(ValueError, match="root"):
        parse_xmltv(b"<html></html>")


def test_now_next_uses_aware_time_and_channel() -> None:
    noon = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    current = Programme(
        "a", "Current", noon - timedelta(minutes=5), noon + timedelta(minutes=5), ""
    )
    following = Programme(
        "a", "Next", noon + timedelta(minutes=5), noon + timedelta(minutes=35), ""
    )
    other = Programme("b", "Other", noon - timedelta(minutes=5), noon + timedelta(minutes=5), "")

    assert now_next([following, other, current], "a", noon) == (current, following)
    with pytest.raises(ValueError, match="timezone"):
        now_next([current], "a", noon.replace(tzinfo=None))


def _source(name: str = "Primary", source_id: str | None = None) -> dict[str, str]:
    source = {
        "name": name,
        "type": "m3u",
        "location": "https://provider.test/list.m3u",
        "username": "",
        "password": "",
        "epg_url": "https://provider.test/guide.xml",
    }
    if source_id is not None:
        source["id"] = source_id
    return source


def test_store_persists_sources_channels_favorites_and_progress(tmp_path: Path) -> None:
    database = tmp_path / "private" / "library.db"
    store = Store(database)
    source_id = store.save_source(_source())
    store.replace_channels(source_id, [Channel("base", "One", "https://x.test/1")])
    stored = store.channels(source_id)[0]
    store.set_favorite(stored.id, True)
    store.save_progress(stored.id, 12.5, 100.0)
    store.close()

    reopened = Store(database)
    assert reopened.sources() == [_source(source_id=source_id)]
    assert reopened.channels(source_id) == [Channel(f"{source_id}:base", "One", "https://x.test/1")]
    assert reopened.favorites() == {f"{source_id}:base"}
    assert reopened.progress(f"{source_id}:base") == (12.5, 100.0)
    reopened.close()

    assert database.parent.stat().st_mode & 0o777 == 0o700
    assert database.stat().st_mode & 0o777 == 0o600


def test_store_replace_is_atomic_and_preserves_related_state(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.db")
    source_id = store.save_source(_source())
    store.replace_channels(
        source_id,
        [
            Channel("one", "Old name", "https://x.test/old"),
            Channel("gone", "Gone", "https://x.test/gone"),
        ],
    )
    one_id = f"{source_id}:one"
    gone_id = f"{source_id}:gone"
    store.set_favorite(one_id, True)
    store.set_favorite(gone_id, True)
    store.save_progress(one_id, 4, 10)

    store.replace_channels(source_id, [Channel("one", "New name", "https://x.test/new")])

    assert store.channels(source_id) == [Channel(one_id, "New name", "https://x.test/new")]
    assert store.favorites() == {one_id}
    assert store.progress(one_id) == (4.0, 10.0)
    assert store.progress(gone_id) == (0.0, 0.0)
    store.close()


def test_store_keeps_identical_parser_ids_distinct_between_sources(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.db")
    one = store.save_source(_source("One"))
    two = store.save_source(_source("Two"))
    channel = Channel("same", "Same", "https://x.test/live")
    store.replace_channels(one, [channel])
    store.replace_channels(two, [channel])

    assert {item.id for item in store.channels()} == {f"{one}:same", f"{two}:same"}
    store.close()


def test_store_upsert_adds_episodes_without_replacing_or_double_prefixing(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.db")
    source_id = store.save_source(_source())
    existing_id = f"{source_id}:series"
    store.replace_channels(
        source_id, [Channel("series", "Series", "", kind="series", series_id="42")]
    )
    store.set_favorite(existing_id, True)

    store.upsert_channels(
        source_id,
        [
            Channel(
                f"{source_id}:episode-1",
                "Episode 1",
                "https://x.test/e1",
                kind="movie",
                series_id="42",
            )
        ],
    )

    assert [channel.id for channel in store.channels(source_id)] == [
        existing_id,
        f"{source_id}:episode-1",
    ]
    assert store.favorites() == {existing_id}
    store.close()


def test_store_recent_ids_follow_last_progress_save_and_limit(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.db")
    source_id = store.save_source(_source())
    store.replace_channels(
        source_id,
        [
            Channel("one", "One", "https://x.test/1"),
            Channel("two", "Two", "https://x.test/2"),
            Channel("three", "Three", "https://x.test/3"),
        ],
    )
    one, two, three = (f"{source_id}:{suffix}" for suffix in ("one", "two", "three"))
    store.save_progress(one, 10, 100)
    store.save_progress(two, 100, 100)
    store.save_progress(three, -1, 100)
    store.save_progress(one, 20, 100)

    assert store.recent_ids() == [one, two]
    assert store.recent_ids(limit=1) == [one]
    assert store.recent_ids(limit=0) == []
    store.close()


def test_store_remove_source_cleans_only_its_state(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.db")
    one = store.save_source(_source("One"))
    two = store.save_source(_source("Two"))
    store.replace_channels(one, [Channel("a", "A", "https://x.test/a")])
    store.replace_channels(two, [Channel("b", "B", "https://x.test/b")])
    store.set_favorite(f"{one}:a", True)
    store.set_favorite(f"{two}:b", True)

    store.remove_source(one)

    assert [source["id"] for source in store.sources()] == [two]
    assert [channel.id for channel in store.channels()] == [f"{two}:b"]
    assert store.favorites() == {f"{two}:b"}
    store.close()


def test_store_reports_corrupt_database_with_path(tmp_path: Path) -> None:
    database = tmp_path / "broken.db"
    database.write_bytes(b"not sqlite")
    with pytest.raises(RuntimeError, match=r"broken\.db.*corrupt|corrupt.*broken\.db"):
        Store(database)
