import json
import sqlite3
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest

from luna_iptv.media_details import MediaDetails, normalize_info
from luna_iptv.models import Channel
from luna_iptv.network import NetworkError, XtreamClient
from luna_iptv.storage import Store


@pytest.fixture
def provider(monkeypatch):
    responses = {}
    requests = []

    def fetch(url):
        query = parse_qs(urlsplit(url).query)
        requests.append(query)
        return json.dumps(responses[query["action"][0]]).encode()

    monkeypatch.setattr("luna_iptv.network.fetch", fetch)
    return (
        XtreamClient("https://provider.invalid", "synthetic-user", "synthetic-password"),
        responses,
        requests,
    )


def movie(**kwargs):
    return Channel(
        "movie", "A film", "https://provider.invalid/movie/u/p/99.mp4", kind="movie", **kwargs
    )


def series():
    return Channel("show", "A series", "", kind="series", series_id="42", provider_key="series:42")


def test_movie_merges_partial_info_and_movie_data_without_extra_request(provider):
    client, responses, requests = provider
    responses["get_vod_info"] = {
        "info": {
            "plot": "<p>A &amp; B</p>\nA story.",
            "releasedate": "2024-03-02",
            "genre": ["Drama", None, {"bad": "value"}, "Mystery"],
            "duration": "01:32:00",
            "cast": ["Actor One", "Actor Two"],
            "director": {"bad": "value"},
            "rating": "8.2",
            "imdb_id": "tt12345678",
            "movie_image": "/posters/film.jpg",
        },
        "movie_data": {"director": "A Director", "year": "1999", "rating_source": "imdb"},
    }
    details = client.media_details(movie(provider_key="movie:7%2F8"))
    assert details.info == {
        "description": "A & B A story.",
        "year": "2024",
        "genre": "Drama, Mystery",
        "duration": "01:32:00",
        "cast": "Actor One, Actor Two",
        "director": "A Director",
        "rating": "8.2",
        "imdb_id": "tt12345678",
        "poster": "https://provider.invalid/posters/film.jpg",
    }
    assert len(requests) == 1
    assert requests[0]["action"] == ["get_vod_info"]
    assert requests[0]["vod_id"] == ["7/8"]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"rating": "8", "imdb_id": "tt1234567"}, {"rating": "8", "imdb_id": "tt1234567"}),
        ({"rating": 8.5, "rating_source": "IMDb"}, {"rating": "8.5", "rating_source": "IMDb"}),
        ({"imdb_rating": "7.2/10", "rating": 9}, {"rating": "7.2", "rating_source": "IMDb"}),
        ({"rating": "6.1", "rating_source": "unknown"}, {"rating": "6.1"}),
        ({"rating": "6.1", "rating_source": "TMDB"}, {"rating": "6.1", "rating_source": "TMDb"}),
        ({"rating": True, "rating_source": "IMDb"}, {}),
        ({"rating": "NaN", "imdb_rating": 11}, {}),
    ],
)
def test_rating_provenance_is_explicit(record, expected):
    assert normalize_info(record) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("tt1234567", "tt1234567"),
        ("https://www.imdb.com/title/tt123456789/?ref_=test", "tt123456789"),
        ("tt123456", ""),
        (1234567, ""),
        ("tt1234567junk", ""),
        ("https://imdb.com.evil.invalid/title/tt1234567/", ""),
        ("https://[broken/title/tt1234567/", ""),
    ],
)
def test_only_valid_imdb_title_ids_are_exposed(value, expected):
    assert normalize_info({"imdb_id": value}).get("imdb_id", "") == expected


def test_malformed_fields_do_not_hide_usable_fallback_metadata(provider):
    client, responses, requests = provider
    responses["get_vod_info"] = {
        "info": {"plot": [], "year": {}, "genre": False, "cover": "file:///private.jpg"},
        "movie_data": {
            "plot": "Fallback story",
            "release_date": "2021-01-01",
            "duration_secs": 125,
        },
    }
    details = client.media_details(movie())
    assert details.info == {"description": "Fallback story", "year": "2021", "duration": "00:02:05"}
    assert requests[0]["vod_id"] == ["99"]
    responses["get_vod_info"] = {"info": [], "movie_data": None}
    assert client.media_details(movie()).info == {}


@pytest.mark.parametrize(
    ("record", "duration"),
    [
        ({"runtime": 95}, "95 dk"),
        ({"episode_run_time": "45.5"}, "45.5 dk"),
        ({"duration": "90 min"}, "90 dk"),
        ({"duration_secs": 3661}, "01:01:01"),
        ({"duration": "00:00:00", "runtime": 42}, "42 dk"),
        ({"duration": "unknown"}, ""),
        ({"duration": "01:99:02"}, ""),
        ({"duration": "00:00:00", "duration_secs": 0}, ""),
        ({"runtime": False}, ""),
        ({"runtime": -1}, ""),
    ],
)
def test_duration_has_units_or_clock_and_rejects_missing_values(record, duration):
    info = normalize_info(record)
    assert info.get("duration", "") == duration
    assert normalize_info(info) == info


def test_invalid_response_errors_never_include_connection_credentials(provider):
    client, responses, _ = provider
    responses["get_vod_info"] = ["https://provider.invalid/synthetic-password"]
    with pytest.raises(NetworkError) as error:
        client.media_details(movie())
    assert "synthetic-password" not in str(error.value)
    assert "provider.invalid" not in str(error.value)


def test_series_metadata_and_sorted_playable_episodes_share_one_response(provider):
    client, responses, requests = provider
    responses["get_series_info"] = {
        "info": {"plot": "Series story", "rating": "8"},
        "episodes": {
            "10": [{"id": 10, "episode_num": 1}],
            "2": [
                {"id": 22, "episode_num": 2, "title": "Second", "container_extension": "mkv"},
                {"id": 21, "episode_num": 1, "title": "First"},
                {"id": 21, "episode_num": 3, "title": "Duplicate"},
                None,
                {"id": {}},
                {"id": ""},
            ],
            "0": [{"id": 1, "episode_num": 0, "title": "Special"}],
            "bad": "not a season",
        },
    }
    details = client.media_details(series())
    assert details.info["description"] == "Series story"
    assert [episode.group for episode in details.episodes] == [
        "Sezon 0",
        "Sezon 2",
        "Sezon 2",
        "Sezon 10",
    ]
    assert [episode.provider_key for episode in details.episodes] == [
        "episode:42:1",
        "episode:42:21",
        "episode:42:22",
        "episode:42:10",
    ]
    assert details.episodes[2].url.endswith("/series/synthetic-user/synthetic-password/22.mkv")
    assert len(requests) == 1
    assert client.episodes("42") == details.episodes


@pytest.mark.parametrize(
    "episodes",
    [
        [[{"id": 4, "episode_num": "²"}]],
        [{"id": 4, "season": 0, "episode_num": "²"}],
    ],
)
def test_list_seasons_and_flat_episode_lists_remain_playable(provider, episodes):
    client, responses, _ = provider
    responses["get_series_info"] = {"episodes": episodes}
    parsed = client.media_details(series()).episodes
    assert [(item.group, item.provider_key) for item in parsed] == [("Sezon 0", "episode:42:4")]


@pytest.mark.parametrize("episodes", [None, "invalid", 4])
def test_malformed_series_does_not_replace_cached_episodes_with_empty_success(provider, episodes):
    client, responses, _ = provider
    responses["get_series_info"] = {"info": {"plot": "Still useful"}, "episodes": episodes}
    with pytest.raises(NetworkError):
        client.media_details(series())
    with pytest.raises(NetworkError):
        client.episodes("42")


def test_missing_episode_container_is_an_error_but_empty_container_is_valid(provider):
    client, responses, _ = provider
    responses["get_series_info"] = {"info": {"plot": "Series story"}}
    with pytest.raises(NetworkError):
        client.media_details(series())
    responses["get_series_info"]["episodes"] = []
    assert client.media_details(series()) == MediaDetails({"description": "Series story"})


def test_cached_episode_uses_selected_metadata_not_parent_rating_provenance(provider):
    client, responses, requests = provider
    responses["get_series_info"] = {
        "info": {"plot": "Parent story", "imdb_rating": "9", "genre": "Drama"},
        "episodes": {
            "1": [
                {"id": 7, "info": {"plot": "Other episode"}},
                {
                    "id": 8,
                    "info": {"plot": "Selected story", "rating": "7.5", "air_date": "2022-02-01"},
                },
            ]
        },
    }
    episode = Channel(
        "source:cached",
        "Episode",
        "https://provider.invalid/series/u/p/8.mp4",
        kind="movie",
        series_id="42",
    )
    details = client.media_details(episode)
    assert details.info == {
        "description": "Selected story",
        "rating": "7.5",
        "year": "2022",
        "genre": "Drama",
    }
    assert requests[0]["series_id"] == ["42"]
    assert len(requests) == 1


def test_cache_persists_and_isolated_by_source_and_connection(tmp_path):
    path = tmp_path / "library.sqlite"
    episode = Channel(
        "episode",
        "First",
        "https://provider.invalid/series/u/p/1.mp4",
        group="Sezon 1",
        kind="movie",
        series_id="42",
        provider_key="episode:42:1",
    )
    details = MediaDetails(
        {"description": "Saved story", "rating": "8", "imdb_id": "tt1234567"}, [episode]
    )
    with closing(Store(path)) as store:
        for source_id in ("one", "two"):
            store.save_source({"id": source_id, "type": "xtream"})
            store.replace_channels(source_id, [series()])
        store.save_media_details("one:show", "fingerprint-a", details, 1_700_000_000)
        store.save_media_details(
            "two:show",
            "fingerprint-a",
            MediaDetails({"description": "Other provider"}),
            1_700_000_001,
        )
    with closing(Store(path)) as store:
        assert store.media_details("one:show", "fingerprint-a") == (details, 1_700_000_000)
        assert store.media_details("one:show", "fingerprint-b") is None
        assert (
            store.media_details("two:show", "fingerprint-a")[0].info["description"]
            == "Other provider"
        )
        store.save_media_details(
            "one:show",
            "fingerprint-b",
            MediaDetails({"description": "New connection"}),
            1_700_000_002,
        )
        assert store.media_details("one:show", "fingerprint-a") is None
        assert (
            store.media_details("one:show", "fingerprint-b")[0].info["description"]
            == "New connection"
        )
        store.remove_source("one")
        store.save_source({"id": "one", "type": "xtream"})
        store.replace_channels("one", [series()])
        assert store.media_details("one:show", "fingerprint-b") is None
        store.replace_channels("two", [])
        store.replace_channels("two", [series()])
        assert store.media_details("two:show", "fingerprint-a") is None


@pytest.mark.parametrize(
    "corrupt",
    ["{", "[]", '{"info": [], "episodes": []}', '{"info": {}, "episodes": [{"id": "bad"}]}'],
)
def test_corrupt_cached_payload_is_a_cache_miss(tmp_path, corrupt):
    path = tmp_path / "library.sqlite"
    with closing(Store(path)) as store:
        store.save_source({"id": "source", "type": "xtream"})
        store.replace_channels("source", [series()])
        store.save_media_details("source:show", "fingerprint", MediaDetails(), 1_700_000_000)
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("UPDATE media_detail_cache SET data=?", (corrupt,))
        assert store.media_details("source:show", "fingerprint") is None
