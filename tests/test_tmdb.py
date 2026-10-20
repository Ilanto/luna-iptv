"""TMDB requests use synthetic responses and never contact the service."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QDesktopServices, QPixmap

from luna_iptv.media_details import MediaDetails
from luna_iptv.media_dialog import MediaDetailDialog
from luna_iptv.models import Channel
from luna_iptv.online import OnlineError
from luna_iptv.tmdb import (
    ATTRIBUTION,
    TTL,
    TMDBClient,
    clean_title,
    merge,
    metadata,
    pick,
    trailer_url,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("TR: [DUAL] Dune (2021) 1080p", ("Dune", "2021")),
        ("[4K] Arrival [TR] (2016)", ("Arrival", "2016")),
        ("Film 2024 WEB-DL", ("Film", "2024")),
        ("Tron", ("Tron", "")),
    ],
)
def test_clean_titles(name, expected):
    assert clean_title(name) == expected


def test_match_prefers_exact_original_title_and_year():
    candidates = [
        {"id": 1, "title": "Dune", "release_date": "1984-01-01"},
        {"id": 2, "title": "Dune part 2", "release_date": "2021-01-01"},
        {"id": 3, "title": "Çöl Gezegeni", "original_title": "Dune", "release_date": "2021-01-01"},
    ]
    assert pick(candidates, "Dune", "2021")["id"] == 3
    assert pick([{"id": "../evil", "title": "Dune"}], "Dune") is None
    assert pick(candidates, "Unrelated") is None


@pytest.fixture
def payload():
    return {
        "id": 3,
        "overview": "",
        "release_date": "2021-10-01",
        "runtime": 155,
        "genres": [{"name": "Bilim kurgu"}],
        "vote_average": 8.2,
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "credits": {
            "cast": [{"name": f"Actor {n}"} for n in range(9)],
            "crew": [{"job": "Director", "name": "Director"}],
        },
        "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "abcdef12345"}]},
    }


def test_language_fallback_cache_ttl_offline_and_separate_languages(tmp_path, payload):
    calls, now = [], [1000]

    def fetch(url, **_):
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        calls.append((parts.path, params))
        if "search" in parts.path:
            result = {"results": [{"id": 3, "title": "Dune", "release_date": "2021"}]}
        else:
            result = payload | (
                {"overview": "English plot"} if params["language"] == ["en-US"] else {}
            )
        return json.dumps(result).encode()

    client = TMDBClient("synthetic-key", tmp_path, fetcher=fetch, clock=lambda: now[0])
    info = client.lookup("channel", "Dune (2021)")
    assert info["description"] == "English plot"
    assert info["cast"].endswith("Actor 5") and "Actor 6" not in info["cast"]
    assert info["duration"] == "155 dk" and info["director"] == "Director"
    assert calls[0][1]["year"] == ["2021"]
    assert len(calls) == 3
    assert client.lookup("channel", "Dune (2021)") == info
    assert len(calls) == 3
    assert all("synthetic-key" not in p.read_text() for p in (tmp_path / "tmdb").glob("*.json"))
    now[0] += TTL
    assert client.lookup("channel", "Dune (2021)") == info
    assert len(calls) == 6
    client.language = "en-US"
    client.lookup("channel", "Dune (2021)")
    assert len(calls) == 8
    now[0] += TTL + 1
    client.fetcher = lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline"))
    assert client.lookup("channel", "Dune (2021)")["description"] == "English plot"


def test_no_key_never_reads_network_or_cache(tmp_path):
    def forbidden(*_, **__):
        pytest.fail("No key must mean no request")

    client = TMDBClient("", tmp_path, fetcher=forbidden)
    assert client.lookup("a", "Dune") == {}
    assert client.verify() is False
    assert not list(tmp_path.iterdir())


def test_corrupt_cache_and_errors_are_safe(tmp_path):
    def fetch(*_, **__):
        raise ValueError("url?api_key=private")

    with pytest.raises(OnlineError) as error:
        TMDBClient("synthetic", tmp_path, fetcher=fetch).lookup("a", "Dune")
    assert "private" not in str(error.value)


def test_provider_merge_and_trailer_validation(payload):
    extra = metadata(payload)
    assert extra["tmdb_attribution"] == ATTRIBUTION
    assert extra["trailer"] == "https://www.youtube.com/watch?v=abcdef12345"
    assert trailer_url("evil&x=1") == ""
    provider = {
        "description": "Provider plot",
        "rating": "6",
        "poster": "provider.jpg",
        "genre": "",
    }
    combined = merge(provider, extra)
    assert combined["description"] == "Provider plot"
    assert combined["rating"] == "6" and "rating_source" not in combined
    assert combined["poster"] == "provider.jpg"
    assert combined["tmdb_poster"].endswith("/poster.jpg")
    assert combined["genre"] == "Bilim kurgu"
    assert provider["genre"] == ""
    assert merge({"rating": "9", "rating_source": "IMDb"}, extra)["rating_source"] == "IMDb"
    series = metadata({"created_by": [{"name": "Creator"}], "episode_run_time": [42]})
    assert series["director"] == "Creator" and series["duration"] == "42 dk"


class Posters(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.images = {}

    def request_logo(self, url):
        self.requests.append(url)

    def release(self, _url):
        pass

    def prepared_logo(self, url):
        return self.images.get(url)


def test_dialog_merge_attribution_trailer_and_backdrop(qt_app, payload, monkeypatch):
    posters = Posters()
    card = MediaDetailDialog(Channel("c", "Dune", "", kind="movie"), posters)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    provider = MediaDetails(info={"description": "Provider plot", "rating": "7"})
    card.set_details(provider)
    info = metadata(payload)
    pixmap = QPixmap(80, 40)
    pixmap.fill("red")
    posters.images[info["backdrop"]] = pixmap
    card.set_online_info(info)
    assert card.description_label.text() == "Provider plot"
    assert info["backdrop"] in posters.requests and card.hero._wash is not None
    assert card.tmdb_label.text() == "TMDB · " + ATTRIBUTION
    assert not card.trailer_button.isHidden()
    card.trailer_button.click()
    assert opened == [info["trailer"]]
    card.set_details(MediaDetails(info={"description": "New provider plot"}))
    assert card.description_label.text() == "New provider plot"
    assert card._details.info["cast"] == info["cast"]
    assert provider.info == {"description": "Provider plot", "rating": "7"}
    card.close()


def test_controller_m3u_enrichment_ignores_late_closed_card(qt_app, tmp_path, monkeypatch):
    from luna_iptv.storage import Store
    from luna_iptv.window import MainWindow

    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "s", "name": "Local", "type": "m3u", "location": "/fixture.m3u"})
    first, second = store.replace_channels(
        "s",
        [
            Channel("a", "First", "file:///first", kind="movie"),
            Channel("b", "Second", "file:///second", kind="movie"),
        ],
    )
    window = MainWindow(store)
    jobs = []
    monkeypatch.setattr(window, "run_task", lambda fn, done, *a, **kw: jobs.append((fn, done, kw)))
    window.details.open(first)
    assert jobs == []
    window.details.dismiss()
    store.set_online_secret("tmdb_api_key", "synthetic")
    window.details.open(first)
    window.details.open(second)
    jobs[0][1]({"description": "Wrong card", "tmdb_attribution": ATTRIBUTION})
    assert window.details.dialog.description_label.text() != "Wrong card"
    jobs[1][1]({"description": "Right card", "tmdb_attribution": ATTRIBUTION})
    assert window.details.dialog.description_label.text() == "Right card"
    window.close()
    jobs[1][1]({"description": "Too late", "tmdb_attribution": ATTRIBUTION})


def test_english_fallback_failure_preserves_other_metadata(tmp_path, payload):
    def fetch(url, **_):
        parts = urlsplit(url)
        if "search" in parts.path:
            return b'{"results": [{"id": 3, "title": "Dune"}]}'
        if parse_qs(parts.query)["language"] == ["en-US"]:
            raise OnlineError()
        return json.dumps(payload).encode()

    info = TMDBClient("synthetic", tmp_path, fetcher=fetch).lookup("c", "Dune")
    assert info["duration"] == "155 dk"
    assert "description" not in info
