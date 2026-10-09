"""Subtitle service, cache and player integration without real network or windows."""

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from luna_iptv.models import Channel
from luna_iptv.online import OnlineError, request
from luna_iptv.subtitles import (
    SRT_LIMIT,
    OnlineErrorLogin,
    OpenSubtitlesClient,
    Subtitle,
    SubtitleCache,
    episode_query,
    file_hash,
)

SRT = b"1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n"
RESULT = Subtitle(123, "tr", "Release", 12)


def test_search_turkish_then_english_with_episode_numbers(tmp_path):
    calls = []
    channel = Channel(
        "ep",
        "Pilot",
        "https://provider.invalid/user/password/1.mkv",
        kind="movie",
        series_id="1",
        group="Sezon 2",
    )

    def fetch(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        calls.append((url, query, kwargs))
        language = query["languages"][0]
        return json.dumps(
            {
                "data": [
                    {
                        "attributes": {
                            "language": language,
                            "release": "Release",
                            "download_count": 12,
                            "files": [{"file_id": 123 if language == "tr" else 124}],
                        }
                    }
                ]
            }
        ).encode()

    client = OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch)
    results = client.search(channel, "A Series", {"episode": "3", "season": "2"})
    assert [s.language for s in results] == ["tr", "en"]
    assert results[0] == RESULT
    assert all(c[1]["season_number"] == ["2"] and c[1]["episode_number"] == ["3"] for c in calls)
    assert all(c[1]["query"] == ["A Series"] for c in calls)
    assert all("password" not in c[0] and "provider" not in c[0] for c in calls)
    assert all(c[2]["headers"] == {"Api-Key": "synthetic"} for c in calls)


def test_episode_query_parses_explicit_numbers_and_never_invents_them():
    ep = Channel("ep", "Show S02E03", "", kind="movie", series_id="1")
    query = episode_query(ep)
    assert query["season_number"] == 2 and query["episode_number"] == 3
    assert query["query"] == "Show"
    ep.name = "Pilot"
    query = episode_query(ep, "Show")
    assert "episode_number" not in query


def test_local_hash_and_search(tmp_path):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"\x01\x00\x00\x00\x00\x00\x00\x00" * 16384)
    assert file_hash(path.as_uri()) == f"{131072 + 16384:016x}"
    assert file_hash("https://example.invalid/movie.mkv") == ""
    assert file_hash(tmp_path.as_uri()) == ""
    small = tmp_path / "small.mkv"
    small.write_bytes(b"small")
    assert file_hash(str(small)) == ""
    queries = []

    def fetch(url, **_):
        queries.append(parse_qs(urlsplit(url).query))
        return b'{"data": []}'

    client = OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch)
    client.search(Channel("c", "Movie", path.as_uri(), kind="movie"))
    assert all(q["moviehash"] == [file_hash(str(path))] for q in queries)


def test_download_login_srt_cache_and_remember(tmp_path):
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/login"):
            return b'{"token": "synthetic-token", "base_url": "vip-api.opensubtitles.com"}'
        if url.endswith("/download"):
            return b'{"link": "https://dl.opensubtitles.com/test.srt"}'
        return SRT

    client = OpenSubtitlesClient("synthetic", tmp_path, "user", "password", fetcher=fetch)
    path = client.download(RESULT)
    assert path.read_bytes() == SRT
    assert calls[0][1]["data"] == {"username": "user", "password": "password"}
    assert calls[1][0].startswith("https://vip-api.opensubtitles.com/")
    assert calls[1][1]["data"] == {"file_id": 123, "sub_format": "srt"}
    assert calls[1][1]["headers"]["Authorization"] == "Bearer synthetic-token"
    assert calls[2][1] == {"max_bytes": SRT_LIMIT}
    client.cache.remember("channel", path)
    assert SubtitleCache(tmp_path).remembered("channel") == path
    assert client.download(RESULT) == path and len(calls) == 3
    assert all("synthetic-token" not in p.read_text() for p in path.parent.glob("*.json"))
    path.unlink()
    assert client.cache.remembered("channel") is None


@pytest.mark.parametrize("status", [406, 429])
def test_limit_errors(status, tmp_path):
    def fetch(*_, **__):
        raise OnlineError(status)

    with pytest.raises(OnlineError, match="Günlük indirme hakkı doldu"):
        OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch).download(RESULT)
    assert not list(tmp_path.iterdir())


def test_login_required_message(tmp_path):
    def fetch(*_, **__):
        raise OnlineError(401)

    with pytest.raises(OnlineErrorLogin, match="kullanıcı adı"):
        OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch).download(RESULT)


@pytest.mark.parametrize("raw", [b"x" * (SRT_LIMIT + 1), b"<html>Error</html>", b"-->\x00"])
def test_rejects_oversized_or_invalid_srt(tmp_path, raw):
    def fetch(url, **_):
        return (
            b'{"link": "https://dl.opensubtitles.com/test.srt"}'
            if url.endswith("/download")
            else raw
        )

    with pytest.raises(OnlineError):
        OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch).download(RESULT)
    assert not list(tmp_path.iterdir())


def test_no_key_no_network_or_hashing(tmp_path, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Network/hash used without key")

    monkeypatch.setattr("luna_iptv.subtitles.file_hash", forbidden)
    client = OpenSubtitlesClient("", tmp_path, fetcher=forbidden)
    assert client.search(Channel("c", "Film", "file:///fixture")) == []
    assert client.verify() is False
    with pytest.raises(OnlineError):
        client.download(RESULT)


def test_untrusted_download_host_never_receives_request(tmp_path):
    calls = []

    def fetch(url, **_):
        calls.append(url)
        return b'{"link": "https://evil.invalid/test.srt"}'

    with pytest.raises(OnlineError):
        OpenSubtitlesClient("synthetic", tmp_path, fetcher=fetch).download(RESULT)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 406, 429, 500])
def test_transport_preserves_only_status(monkeypatch, status):
    class Opener:
        def open(self, *_a, **_k):
            raise HTTPError("https://private.invalid?key=secret", status, "secret", {}, None)

    monkeypatch.setattr("luna_iptv.online.build_opener", lambda *_: Opener())
    with pytest.raises(OnlineError) as error:
        request("https://fixture.invalid", headers={"Api-Key": "synthetic"})
    assert error.value.status == status
    assert "secret" not in str(error.value) and "private.invalid" not in str(error.value)


def test_transport_caps_reads_and_uses_timeout(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, limit):
            assert limit == 101
            return b"x" * limit

    class Opener:
        def open(self, _req, timeout):
            assert timeout == 15
            return Response()

    monkeypatch.setattr("luna_iptv.online.build_opener", lambda *_: Opener())
    with pytest.raises(OnlineError):
        request("https://fixture.invalid", max_bytes=100)


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    from luna_iptv.storage import Store
    from luna_iptv.window import MainWindow

    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "s", "name": "Local", "type": "m3u", "location": "/fixture.m3u"})
    store.replace_channels(
        "s",
        [
            Channel("c", "Film", "file:///fixture", kind="movie"),
            Channel("d", "Other", "file:///other", kind="movie"),
        ],
    )
    value = MainWindow(store)
    value.jobs, value.commands = [], []
    monkeypatch.setattr(value.player, "load", lambda *_a, **_k: None)
    monkeypatch.setattr(value.player, "set_property", lambda *_: None)
    monkeypatch.setattr(value.player, "command", lambda args: value.commands.append(args))
    monkeypatch.setattr(
        value, "run_task", lambda fn, done, *a, **kw: value.jobs.append((fn, done, kw))
    )
    yield value
    value.close()


def test_menu_download_sub_add_and_remembered_replay(window, monkeypatch):
    channel = window.store.channels()[0]
    window.play(channel)
    window.loaded()
    assert "Altyazı bul…" not in [a.text() for a in window.build_track_menu().actions()]
    window.store.set_online_secret("opensubtitles_api_key", "synthetic")
    assert "Altyazı bul…" in [a.text() for a in window.build_track_menu().actions()]
    monkeypatch.setattr(OpenSubtitlesClient, "search", lambda *_: [RESULT])
    path = window.store.path.parent / "subtitles" / "123.srt"
    path.parent.mkdir()
    path.write_bytes(SRT)
    monkeypatch.setattr(OpenSubtitlesClient, "download", lambda *_: path)
    window.subtitles.open()
    dialog = window.subtitles.dialog
    work, done, _ = window.jobs.pop()
    done(work())
    assert dialog.results.count() == 1
    dialog.results.setCurrentRow(0)
    dialog.download_button.click()
    work, done, _ = window.jobs.pop()
    done(work())
    assert window.commands[-1] == ["sub-add", str(path), "select"]
    assert "sub" in window.track_preferences._manual_modes
    assert SubtitleCache(window.store.path.parent).remembered(channel.id) == path
    window.play(channel)
    window.loaded()
    work, done, _ = window.jobs.pop()
    done(work())
    assert window.commands.count(["sub-add", str(path), "select"]) == 2
    window.loaded()
    assert not window.jobs


def test_late_remembered_result_never_attaches_to_new_playback(window):
    window.store.set_online_secret("opensubtitles_api_key", "synthetic")
    first, second = window.store.channels()
    window.play(first)
    window.loaded()
    _, done, _ = window.jobs.pop()
    window.play(second)
    done(window.store.path.parent / "subtitles" / "123.srt")
    assert window.commands == []


def test_episode_numbers_survive_provider_disk_cache(tmp_path, monkeypatch):
    from luna_iptv.media_controller import source_fingerprint
    from luna_iptv.network import XtreamClient
    from luna_iptv.storage import Store

    store = Store(tmp_path / "library.sqlite3")
    source = {
        "id": "s",
        "name": "Fixture",
        "type": "xtream",
        "location": "https://fixture.invalid",
        "username": "synthetic",
        "password": "synthetic",
    }
    store.save_source(source)
    series = store.replace_channels("s", [Channel("s", "Show", "", kind="series", series_id="1")])[
        0
    ]
    client = XtreamClient(source["location"], "synthetic", "synthetic")
    monkeypatch.setattr(
        client,
        "_api",
        lambda *_, **__: {
            "info": {"name": "Show"},
            "episodes": {"2": [{"id": 9, "episode_num": 3, "title": "Pilot"}]},
        },
    )
    details = client.media_details(series)
    fingerprint = source_fingerprint(source)
    store.save_media_details(series.id, fingerprint, details, 1000)
    cached = store.media_details(series.id, fingerprint)[0]
    assert cached.episode_info["episode:1:9"]["episode"] == "3"
    assert cached.episode_info["episode:1:9"]["season"] == "2"
    store.close()


def test_limit_note_and_late_download_after_channel_change(window):
    window.store.set_online_secret("opensubtitles_api_key", "synthetic")
    first, second = window.store.channels()
    window.play(first)
    window.loaded()
    window.jobs.clear()
    window.subtitles.open()
    dialog = window.subtitles.dialog
    _, done, _ = window.jobs.pop()
    done([RESULT])
    dialog.results.setCurrentRow(0)
    dialog.download_button.click()
    _, _, options = window.jobs.pop()
    options["failure"]("Günlük indirme hakkı doldu")
    assert dialog.note.text() == "Günlük indirme hakkı doldu"
    assert dialog.download_button.isEnabled()
    dialog.download_button.click()
    _, done, _ = window.jobs.pop()
    window.play(second)
    done(window.store.path.parent / "subtitles" / "123.srt")
    assert window.commands == []


def test_manual_subtitle_wins_over_pending_restore(window):
    window.store.set_online_secret("opensubtitles_api_key", "synthetic")
    window.play(window.store.channels()[0])
    window.loaded()
    _, restored, _ = window.jobs.pop()
    window.subtitles.open()
    dialog = window.subtitles.dialog
    _, found, _ = window.jobs.pop()
    found([RESULT])
    dialog.results.setCurrentRow(0)
    dialog.download_button.click()
    restored(window.store.path.parent / "subtitles" / "999.srt")
    assert window.commands == []


def test_external_subtitle_survives_track_updates(window):
    window.store.set_setting("subtitle_language", "off")
    window.play(window.store.channels()[0])
    window.loaded()
    properties = []
    window.player.set_property = lambda *args: properties.append(args)
    window.track_preferences.external_subtitle()
    window.track_preferences.update_tracks(
        [{"id": 1, "type": "sub", "lang": "tr", "external": True, "selected": True}]
    )
    assert properties == []
