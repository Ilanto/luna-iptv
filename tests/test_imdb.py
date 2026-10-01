"""IMDb lookups open a title page only for a certain match, else the search page."""

import json

from luna_iptv.imdb import clean_title, find, pick, search_url

DUNE = {"id": "tt15239678", "l": "Dune: Part Two", "qid": "movie", "y": 2024}


def test_clean_title_strips_provider_decorations():
    assert clean_title("TR: Kum: İkinci Bölüm (2024)") == ("Kum: İkinci Bölüm", "2024")
    assert clean_title("[4K] Dune (2021)", "2021-10-22") == ("Dune", "2021")
    assert clean_title("EN | Bosch: Legacy") == ("Bosch: Legacy", "")
    assert clean_title("1883") == ("1883", "")


def test_pick_needs_same_title_kind_and_year():
    payload = {
        "d": [DUNE, {"id": "tt31378509", "l": "Dune: Part Three", "qid": "movie", "y": 2026}]
    }
    assert pick(payload, "dune part two", "2024", "movie") == "tt15239678"
    assert pick(payload, "Dune: Part Two", "2019", "movie") is None
    assert pick(payload, "Dune: Part Two", "2024", "series") is None
    assert pick(payload, "Dune", "", "movie") is None


def test_pick_rejects_ambiguous_or_malformed_suggestions():
    twin = dict(DUNE, id="tt99999999")
    assert pick({"d": [DUNE, twin]}, "Dune: Part Two", "", "movie") is None
    assert pick({"d": [dict(DUNE, id="tt1/evil")]}, "Dune: Part Two", "2024", "movie") is None
    assert pick({"d": "nonsense"}, "Dune: Part Two", "2024", "movie") is None


def test_find_opens_the_page_or_falls_back_to_search():
    sent = []

    def fetcher(url, limit):
        sent.append(url)
        return json.dumps({"d": [DUNE]}).encode()

    assert (
        find("Dune: Part Two", "2024", "movie", fetcher) == "https://www.imdb.com/title/tt15239678/"
    )
    assert sent == ["https://v3.sg.media-imdb.com/suggestion/x/dune%20part%20two.json"]

    def broken(url, limit):
        raise OSError("offline")

    assert find("Kum", "2024", "movie", broken) == search_url("Kum", "2024", "movie")
    assert find("Kum", "", "series", lambda *a: b"<html>") == search_url("Kum", "", "series")
