"""Cinematic details remain usable without a desktop or real poster requests."""

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QLabel
from shiboken6 import isValid

from luna_iptv import theme
from luna_iptv.logos import LogoCache
from luna_iptv.media_details import MediaDetails, normalize_info
from luna_iptv.media_dialog import EPISODE_ROW_HEIGHT, EpisodeDelegate, MediaDetailDialog
from luna_iptv.models import Channel

PLOT = (
    "Dünya ile bağlantısını kaybeden bir araştırma gemisi beklenmedik bir sinyal alır. "
    "Genç bir pilot sinyalin kaynağını ararken kendi geçmişiyle yüzleşir. "
) * 8
INFO = {
    "year": "2026",
    "duration": "128 dk",
    "rating": "8.4",
    "genre": "Bilim Kurgu, Dram, Macera",
    "description": PLOT,
    "director": "Deniz Aral",
    "cast": "Ece Yıldız, Kerem Aydın, Selin Aksoy, Arda Demir",
    "country": "Türkiye",
    "language": "Türkçe, İngilizce",
}


@pytest.fixture
def detail(qt_app, tmp_path):
    cache = LogoCache(tmp_path / "library.sqlite3", size=QSize(240, 360))
    cards = []

    def create(*, poster=False, series=False):
        url = "https://art.invalid/synthetic.png" if poster else ""
        if poster:
            image = QPixmap(240, 360)
            image.fill(QColor("#5577aa"))
            cache._memory[url] = image, float("inf")
        channel = Channel(
            "sample:series" if series else "sample:film",
            "Son Yörünge",
            "" if series else "file:///sample.mkv",
            kind="series" if series else "movie",
            logo=url,
            series_id="show" if series else "",
        )
        card = MediaDetailDialog(channel, cache)
        card.setStyleSheet(theme.STYLE)
        card.set_details(MediaDetails(info=dict(INFO)))
        card.show()
        cards.append(card)
        settle(qt_app)
        return card

    yield create
    for card in cards:
        if isValid(card):
            card.close()
    cache.close()


def settle(app):
    for _ in range(4):
        app.processEvents()


def test_hero_title_meta_chips_and_actions(detail, qt_app):
    card = detail(poster=True)
    assert card.title_label.text() == "Son Yörünge"
    assert card.title_label.font().pixelSize() == 34
    assert card.title_label.font().bold()
    assert card.kind_label.text() == "FİLM  ·  BİLİM KURGU"
    assert card.facts_label.text() == "2026   ·   128 dk   ·   ★ 8.4"
    assert [label.text() for label in card.genre_chips.findChildren(QLabel)] == [
        "Bilim Kurgu",
        "Dram",
        "Macera",
    ]
    assert not card.poster_label.pixmap().isNull()
    assert card.hero._wash is not None
    assert 240 <= card.hero.height() <= 350
    assert card.poster_label.width() * 3 == card.poster_label.height() * 2
    for button in (card.play_button, card.favorite_button, card.imdb_button):
        assert card.hero.isAncestorOf(button)
        assert button.visibleRegion().boundingRect().contains(button.rect())
    top = card.about.mapTo(card.scroll.widget(), QPoint()).y()
    assert 0 <= top - card.hero.geometry().bottom() <= 32
    card.set_details(MediaDetails(info={"genre": "Gizem / Dram; Gizem"}))
    settle(qt_app)
    assert [label.text() for label in card.genre_chips.findChildren(QLabel)] == ["Gizem", "Dram"]


def test_plot_expand_collapse_preserves_full_text(detail, qt_app):
    card = detail()
    label = card.description_label
    assert label.text() == PLOT
    assert label.height() <= label.fontMetrics().lineSpacing() * 4
    assert card.plot_toggle.isVisible()
    collapsed = label.height()
    card.plot_toggle.click()
    settle(qt_app)
    assert label.height() > collapsed
    assert label.text() == PLOT
    assert card.plot_toggle.text() == "Daha az"
    card.plot_toggle.click()
    settle(qt_app)
    assert label.height() == collapsed
    assert card.plot_toggle.text() == "Devamı"
    card.set_details(MediaDetails(info={"description": "Kısa konu."}))
    settle(qt_app)
    assert not card.plot_toggle.isVisible()
    assert label.text() == "Kısa konu."


@pytest.mark.parametrize(
    "saved, expected",
    [
        ((3120, 7680), "Devam et · 76 dk kaldı"),
        ((61, 100), "Devam et · 1 dk kaldı"),
        ((0, 100), "Oynat"),
        ((5, 100), "Oynat"),
        ((90, 100), "Oynat"),
        ((100, 100), "Oynat"),
        ((float("nan"), 100), "Oynat"),
        ((50, float("inf")), "Oynat"),
        (None, "Oynat"),
    ],
)
def test_resume_label_uses_saved_progress_without_playing(detail, saved, expected):
    card = detail()
    played = []
    card.play_requested.connect(played.append)
    card.set_progress_lookup(lambda cid: saved)
    assert card.play_button.text() == expected
    assert played == []
    card.play_button.click()
    assert played == [card.selected_channel()]
    card.set_progress_lookup(None)
    assert card.play_button.text() == "Oynat"


def test_no_poster_uses_title_coloured_gradient(detail, qt_app):
    card = detail()
    assert card.hero._wash is None
    assert card.hero.name == "Son Yörünge"
    assert card.poster_label.pixmap().isNull()
    first = card.hero.grab().toImage()
    left = first.pixelColor(5, 5)
    right = first.pixelColor(first.width() - 6, 5)
    assert left != right
    assert left.lightness() > right.lightness()
    card.hero.name = "Başka bir film"
    card.hero.update()
    settle(qt_app)
    assert card.hero.grab().toImage().pixelColor(5, 5) != left


def test_minimum_size_scrolls_and_restores_hero_actions(detail, qt_app):
    card = detail(poster=True)
    card.set_progress_lookup(lambda cid: (3120, 7680))
    card.set_favorite(True)
    card.resize(540, 320)
    settle(qt_app)
    assert card.size() == QSize(540, 320)
    assert card.scroll.verticalScrollBar().maximum() > 0
    assert card.scroll.widget().width() == card.scroll.viewport().width()
    assert card.scroll.horizontalScrollBar().maximum() == 0
    for control in (card.audio_combo, card.subtitle_combo):
        card.scroll.ensureWidgetVisible(control)
        settle(qt_app)
        assert control.visibleRegion().boundingRect().contains(control.rect())
    for button in (card.play_button, card.favorite_button, card.close_button):
        assert button.visibleRegion().boundingRect().contains(button.rect())
        assert card.rect().contains(button.mapTo(card, QPoint()) + button.rect().bottomRight())
    card.resize(980, 700)
    settle(qt_app)
    assert card.hero.isAncestorOf(card.play_button)
    assert card.hero.isAncestorOf(card.favorite_button)


def test_series_list_grows_and_auxiliary_sections_can_expand(detail, qt_app):
    card = detail(series=True)
    episodes = [
        Channel(
            f"sample:episode-{i}",
            f"Bölüm {i}",
            f"file:///episode-{i}.mkv",
            kind="movie",
            series_id="show",
            group="Sezon 1",
            provider_key=f"episode:{i}",
        )
        for i in range(1, 9)
    ]
    card.set_details(MediaDetails(episodes=episodes, info=dict(INFO), series_title="Son Yörünge"))
    card.preferences_toggle.setChecked(False)
    settle(qt_app)
    assert isinstance(card.episode_list.itemDelegate(), EpisodeDelegate)
    assert card.episode_list.height() >= 2 * EPISODE_ROW_HEIGHT
    before = card.episode_list.height()
    card.resize(980, 900)
    settle(qt_app)
    assert card.episode_list.height() > before
    card.series_info_toggle.click()
    card.preferences_toggle.click()
    settle(qt_app)
    assert card.series_description_label.isVisible()
    assert card.audio_combo.isVisible()
    card.episode_combo.setCurrentIndex(1)
    assert card.title_label.text() == "Bölüm 2"
    assert card.description_label.text() == "Açıklama bulunmuyor."


def test_country_and_language_survive_provider_normalization():
    assert normalize_info(
        {"countries": ["Türkiye", "Fransa"], "language": ["Türkçe", "Fransızca"]}
    ) == {
        "country": "Türkiye, Fransa",
        "language": "Türkçe, Fransızca",
    }
