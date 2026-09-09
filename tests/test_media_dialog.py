"""Synthetic catalogue cards never begin playback without a user action."""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import QFormLayout, QLabel
from shiboken6 import isValid

from luna_iptv.logos import LogoCache
from luna_iptv.media_details import MediaDetails
from luna_iptv.media_dialog import MediaDetailDialog
from luna_iptv.models import Channel


class PosterCache(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.images = {}
        self.reads = []
        self.requests = []

    def request_logo(self, url):
        self.requests.append(url)

    def prepared_logo(self, url):
        self.reads.append(url)
        return self.images.get(url)


@pytest.fixture
def card_factory(qt_app):
    cards = []
    cache = PosterCache()

    def create(channel):
        card = MediaDetailDialog(channel, cache)
        cards.append(card)
        return card, cache

    yield create
    for card in cards:
        if isValid(card):
            card.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def movie(**kwargs):
    return Channel("home:film", "Film <b>title</b>", "file:///film.mkv", kind="movie", **kwargs)


def episode(number, season):
    return Channel(
        f"home:episode-{number}",
        f"Bölüm {number}",
        f"file:///episode-{number}.mkv",
        kind="movie",
        group=f"Sezon {season}",
        series_id="series-1",
        provider_key=f"xtream:episode:{number}",
    )


@pytest.mark.parametrize(
    "imdb_id", ["tt1234567/evil", "tt1234567\n", "tt123456", "tt１２３４５６７"]
)
def test_provider_text_is_literal_and_invalid_imdb_never_opens(card_factory, monkeypatch, imdb_id):
    card, _ = card_factory(movie())
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    payload = '<a href="https://untrusted.invalid/">Provider text</a>'
    card.set_details(
        MediaDetails(
            info={
                "description": payload,
                "cast": payload,
                "rating": "8.1",
                "imdb_id": imdb_id,
            }
        )
    )
    assert card.title_label.text() == movie().name
    assert card.description_label.text() == payload
    assert all(label.textFormat() == Qt.PlainText for label in card.findChildren(QLabel))
    cast = next(
        label for label in card.findChildren(QLabel) if label.accessibleName() == "Oyuncular"
    )
    assert cast.text() == payload
    assert card.imdb_button.isHidden()
    card.imdb_button.click()
    assert opened == []


def test_valid_imdb_link_does_not_attribute_unknown_rating(card_factory, monkeypatch):
    card, _ = card_factory(movie())
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    card.set_details(MediaDetails(info={"rating": "8.1", "imdb_id": "tt1234567"}))
    rating = next(label for label in card.findChildren(QLabel) if label.text() == "8.1")
    assert rating.accessibleName() == "Puan"
    assert not card.imdb_button.isHidden()
    card.imdb_button.click()
    assert opened == ["https://www.imdb.com/title/tt1234567/"]
    card.set_details(MediaDetails(info={"rating": "8.1", "rating_source": "IMDb"}))
    rating = next(label for label in card.findChildren(QLabel) if label.text() == "8.1")
    assert rating.accessibleName() == "IMDb puanı"
    assert card.imdb_button.isHidden()
    card.imdb_button.click()
    assert opened == ["https://www.imdb.com/title/tt1234567/"]


def test_series_waits_for_episodes_and_preserves_selected_identity(card_factory, qt_app):
    series = Channel("home:series", "Dizi", "", kind="series", series_id="series-1")
    card, _ = card_factory(series)
    played, favorites = [], []
    card.play_requested.connect(played.append)
    card.favorite_requested.connect(favorites.append)
    card.show()
    qt_app.processEvents()
    assert not card.isModal()
    assert not card.play_button.isEnabled()
    assert card.selected_channel() is None
    assert not card.favorite_button.isEnabled()
    assert card.favorite_channel() is None
    card.play_button.click()
    eps = [episode(10, 10), episode(3, 2), episode(2, 2), episode(0, 0)]
    card.set_details(MediaDetails(episodes=eps))
    assert [card.season_combo.itemText(i) for i in range(3)] == ["Sezon 0", "Sezon 2", "Sezon 10"]
    card.season_combo.setCurrentIndex(1)
    assert [card.episode_combo.itemData(i) for i in range(2)] == eps[1:3]
    card.episode_combo.setCurrentIndex(1)
    card.favorite_button.click()
    assert favorites == [eps[2]]
    assert played == []
    refreshed = episode(2, 2)
    refreshed.url = "file:///refreshed-episode.mkv"
    refreshed.id = "new-scope:episode-2"
    card.set_favorite(True)
    card.set_details(MediaDetails(episodes=[eps[0], refreshed, eps[1], eps[3]]))
    assert card.selected_channel() is refreshed
    assert card.title_label.text() == refreshed.name
    assert played == []
    card.play_button.click()
    assert played == [refreshed]
    assert card.isVisible()
    card.favorite_button.click()
    assert favorites == [eps[2], refreshed]
    card.set_details(MediaDetails())
    assert not card.play_button.isEnabled()
    assert card.selected_channel() is None
    assert not card.favorite_button.isEnabled()
    assert card.favorite_channel() is None


def test_cached_episode_actions_work_before_metadata_then_follow_selection(card_factory):
    original = episode(4, 2)
    card, _ = card_factory(original)
    favorites, played = [], []
    card.favorite_requested.connect(favorites.append)
    card.play_requested.connect(played.append)
    card.favorite_button.click()
    card.play_button.click()
    assert favorites == played == [original]
    favorites.clear()
    played.clear()
    other = episode(1, 0)
    card.set_details(MediaDetails(episodes=[other, original]))
    assert card.selected_channel() is original
    card.season_combo.setCurrentIndex(0)
    card.favorite_button.click()
    card.play_button.click()
    assert favorites == [other]
    assert played == [other]


def test_episode_selection_updates_visible_details_and_both_actions(card_factory, monkeypatch):
    original, other = episode(1, 1), episode(2, 1)
    parent = Channel("home:series", "Dizi", "", kind="series", series_id="series-1")
    card, cache = card_factory(original)
    card.set_series_channel(parent)
    opened, played, favorites, selections, series_favorites = [], [], [], [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    card.play_requested.connect(played.append)
    card.favorite_requested.connect(favorites.append)
    card.series_favorite_requested.connect(series_favorites.append)
    card.selection_changed.connect(
        lambda channel: selections.append(
            (channel, card.title_label.text(), card.description_label.text())
        )
    )
    first_art, second_art = "https://art.invalid/a.png", "https://art.invalid/b.png"
    for url, color in ((first_art, Qt.red), (second_art, Qt.blue)):
        image = QPixmap(100, 200)
        image.fill(color)
        cache.images[url] = image
    card.set_details(
        MediaDetails(
            info={
                "description": "Dizinin genel konusu",
                "rating": "9.2",
                "imdb_id": "tt9999999",
            },
            episodes=[original, other],
            series_title="Sağlayıcı dizi adı",
            episode_info={
                original.provider_key: {
                    "description": "İlk bölümün konusu",
                    "year": "2020",
                    "rating": "7.1",
                    "imdb_id": "tt1111111",
                    "poster": first_art,
                },
                other.provider_key: {
                    "description": "İkinci bölümün konusu",
                    "year": "2021",
                    "rating": "8.3",
                    "imdb_id": "tt2222222",
                    "poster": second_art,
                },
            },
        )
    )
    assert card.title_label.text() == original.name
    assert card.description_label.text() == "İlk bölümün konusu"
    card.set_favorite(True)
    card.episode_combo.setCurrentIndex(1)
    assert selections == [(other, other.name, "İkinci bölümün konusu")]
    assert card.title_label.text() == card.windowTitle() == other.name
    assert card.description_label.text() == "İkinci bölümün konusu"
    fields = {
        card.metadata.itemAt(row, QFormLayout.ItemRole.LabelRole)
        .widget()
        .text(): card.metadata.itemAt(row, QFormLayout.ItemRole.FieldRole).widget().text()
        for row in range(card.metadata.rowCount())
    }
    assert fields["Yıl"] == "2021"
    assert fields["Puan"] == "8.3"
    assert "IMDb puanı" not in fields
    assert card.poster_scope_label.text() == "Bölüm afişi"
    assert card.poster_label.pixmap().toImage().pixelColor(0, 0) == Qt.blue
    cache.ready.emit(first_art)
    assert card.poster_label.pixmap().toImage().pixelColor(0, 0) == Qt.blue
    card.imdb_button.click()
    assert opened == ["https://www.imdb.com/title/tt2222222/"]
    assert card.series_title_label.text() == "Sağlayıcı dizi adı"
    assert card.series_description_label.text() == "Dizinin genel konusu"
    assert not card.series_section.isHidden()
    assert card.scroll.widget().isAncestorOf(card.series_section)
    card.series_imdb_button.click()
    assert opened[-1] == "https://www.imdb.com/title/tt9999999/"
    assert card.favorite_channel() is card.selected_channel() is other
    assert played == favorites == []
    card.favorite_button.click()
    card.play_button.click()
    assert favorites == played == [other]
    card.series_favorite_button.click()
    assert series_favorites == [parent]


def test_missing_episode_metadata_never_inherits_episode_or_series_fields(
    card_factory, monkeypatch
):
    original, other = episode(1, 1), episode(2, 2)
    card, cache = card_factory(original)
    opened, selections = [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    card.selection_changed.connect(selections.append)
    series_art = "https://art.invalid/series.png"
    image = QPixmap(100, 200)
    image.fill(Qt.green)
    cache.images[series_art] = image
    card.set_details(
        MediaDetails(
            info={
                "description": "Dizi konusu",
                "year": "1999",
                "rating": "9.9",
                "poster": series_art,
                "imdb_id": "tt9999999",
            },
            episodes=[original, other],
            series_title="Dizi",
            episode_info={
                original.provider_key: {
                    "description": "A konusu",
                    "year": "2020",
                    "rating": "7.0",
                    "imdb_id": "tt1111111",
                    "poster": "https://art.invalid/a.png",
                }
            },
        )
    )
    card.season_combo.setCurrentIndex(1)
    assert selections == [other]
    assert card.title_label.text() == card.windowTitle() == other.name
    assert card.description_label.text() == "Açıklama bulunmuyor."
    assert all(
        card.metadata.itemAt(row, QFormLayout.ItemRole.FieldRole).widget().text() == "Belirtilmemiş"
        for row in range(card.metadata.rowCount())
    )
    assert card.imdb_button.isHidden()
    card.imdb_button.click()
    assert opened == []
    assert card.poster_scope_label.text() == "Dizi afişi (bölüm afişi bulunmuyor)"
    assert card.poster_label.pixmap().toImage().pixelColor(0, 0) == Qt.green
    assert card.series_description_label.text() == "Dizi konusu"
    assert card.favorite_channel() is card.selected_channel() is other
    card.set_details(MediaDetails())
    assert selections == [other, None]
    assert card.selected_channel() is card.favorite_channel() is None
    assert not card.play_button.isEnabled()
    assert not card.favorite_button.isEnabled()


def test_movie_refresh_retry_and_close_do_not_play(card_factory, qt_app):
    original = movie()
    card, cache = card_factory(original)
    played, retries = [], []
    card.play_requested.connect(played.append)
    card.retry_requested.connect(lambda: retries.append(True))
    card.show()
    card.set_details(MediaDetails())
    card.set_status("Bilgi alınamadı.", retry=True)
    card.retry_button.click()
    qt_app.processEvents()
    assert retries == [True]
    assert played == []
    assert cache.requests == []
    assert card.poster_label.pixmap().isNull()
    assert card.selected_channel() is original
    card.play_button.click()
    assert played == [original]
    assert card.isVisible()
    card.close_button.click()
    assert played == [original]


def test_stale_poster_completion_cannot_replace_current_poster_or_access_deleted_card(card_factory):
    old_url, new_url = "https://art.invalid/old.png", "https://art.invalid/new.png"
    card, cache = card_factory(movie(logo=old_url))
    card.set_details(MediaDetails(info={"poster": new_url}))
    old = QPixmap(100, 100)
    old.fill(Qt.red)
    cache.images[old_url] = old
    cache.ready.emit(old_url)
    assert card.poster_label.pixmap().isNull()
    current = QPixmap(100, 200)
    current.fill(Qt.blue)
    cache.images[new_url] = current
    cache.ready.emit(new_url)
    rendered = card.poster_label.pixmap()
    assert rendered.size() == QSize(180, 360)
    assert rendered.toImage().pixelColor(0, 0) == current.toImage().pixelColor(0, 0)
    card.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(card)
    reads_before = list(cache.reads)
    cache.ready.emit(new_url)
    assert cache.reads == reads_before


def test_poster_cache_does_not_reuse_list_thumbnail_dimensions(qt_app, tmp_path):
    source = tmp_path / "synthetic-poster.png"
    image = QImage(200, 300, QImage.Format_ARGB32)
    image.fill(Qt.blue)
    assert image.save(str(source))
    store_path = tmp_path / "library.sqlite3"
    logos = LogoCache(store_path)
    posters = LogoCache(store_path, size=QSize(240, 360), cache_suffix=".posters")
    try:
        for cache, expected_height in ((logos, 76), (posters, 360)):
            cache.request_logo(str(source))
            until = time.monotonic() + 3
            while cache.prepared_logo(str(source)) is None and time.monotonic() < until:
                qt_app.processEvents()
                time.sleep(0.005)
            rendered = cache.prepared_logo(str(source))
            assert rendered is not None
            assert rendered.height() == expected_height
    finally:
        logos.close()
        posters.close()
