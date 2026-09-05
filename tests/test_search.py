import pytest
from PySide6.QtCore import Qt

from luna_iptv import library
from luna_iptv.library import ChannelFilter, ChannelModel
from luna_iptv.models import Channel


@pytest.fixture
def catalogue(qt_app):
    model = ChannelModel()
    model.reset(
        [
            Channel("home:a", "İkinci IŞIK yayını", "", group="Gündem"),
            Channel("home:b", "Café İstanbul", "", group="Belgesél"),
            Channel("away:c", "Sport", "", group="Gündem"),
            Channel("home:d", "Film", "", kind="movie"),
        ],
        {"home:b"},
    )
    proxy = ChannelFilter()
    proxy.setSourceModel(model)
    return model, proxy


def names(proxy):
    return [proxy.index(i, 0).data(Qt.DisplayRole) for i in range(proxy.rowCount())]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("ISIK", ["İkinci IŞIK yayını"]),
        ("ışık", ["İkinci IŞIK yayını"]),
        ("CAFE ISTANBUL", ["Café İstanbul"]),
        ("belgesel", ["Café İstanbul"]),
        ("guNDEM", ["İkinci IŞIK yayını", "Sport"]),
        ("", ["İkinci IŞIK yayını", "Café İstanbul", "Sport"]),
        ("nothing", []),
    ],
)
def test_name_group_and_turkish_search(catalogue, query, expected):
    _, proxy = catalogue
    proxy.query = query
    proxy.refresh()
    assert names(proxy) == expected


def test_replaced_catalogue_does_not_retain_stale_search_keys(catalogue):
    model, proxy = catalogue
    proxy.query = "isik"
    proxy.refresh()
    assert len(names(proxy)) == 1
    model.reset([Channel("home:a", "Yeni haber", "", group="Yeni")], set())
    proxy.refresh()
    assert names(proxy) == []
    proxy.query = "yeni"
    proxy.refresh()
    assert names(proxy) == ["Yeni haber"]


def test_search_composes_with_source_group_favorite_and_recent(catalogue):
    _, proxy = catalogue
    proxy.query = "gundem"
    proxy.group = "Gündem"
    proxy.source = "home"
    proxy.refresh()
    assert names(proxy) == ["İkinci IŞIK yayını"]
    proxy.section = "favorites"
    proxy.refresh()
    assert names(proxy) == []
    proxy.source = ""
    proxy.section = "recent"
    proxy.set_recent_ids(["away:c", "home:a"])
    proxy.refresh()
    assert names(proxy) == ["Sport", "İkinci IŞIK yayını"]


def test_each_keystroke_does_not_renormalize_entire_catalogue(qt_app, monkeypatch):
    model = ChannelModel()
    model.reset([Channel(str(i), f"İstanbul {i}", "") for i in range(1000)], set())
    proxy = ChannelFilter()
    proxy.setSourceModel(model)
    calls = []
    original = library.search_key

    def record(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(library, "search_key", record)
    for query in ("istan", "istanbul", "absent"):
        proxy.query = query
        proxy.refresh()
        proxy.rowCount()
    assert len(calls) <= 3
