"""Episode browsing stays in sync without implicit playback."""

from dataclasses import replace

import pytest
import test_media_controller as controller_tests
import test_media_dialog as dialog_tests
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from luna_iptv import theme
from luna_iptv.media_details import MediaDetails
from luna_iptv.models import Channel

card_factory = dialog_tests.card_factory
window = controller_tests.window
episode = dialog_tests.episode


@pytest.fixture
def series_card(card_factory):
    card, _ = card_factory(Channel("series", "Dizi", "", kind="series", series_id="series-1"))
    return card


def test_tabs_and_list_mirror_both_combo_selections(series_card):
    card = series_card
    eps = [episode(10, 10), episode(3, 2), episode(2, 2), episode(0, 0)]
    card.set_details(MediaDetails(episodes=eps))
    assert card.season_combo.isHidden() and card.episode_combo.isHidden()
    assert [card.season_tabs.tabText(i) for i in range(3)] == ["Sezon 0", "Sezon 2", "Sezon 10"]
    card.season_tabs.setCurrentIndex(1)
    assert card.season_combo.currentIndex() == 1
    model = card.episode_list.model()
    assert [model.index(i, 0).data(Qt.UserRole) for i in range(2)] == eps[1:3]
    selections = []
    card.selection_changed.connect(selections.append)
    card.episode_list.setCurrentIndex(model.index(1, 0))
    assert card.episode_combo.currentIndex() == 1
    assert card.title_label.text() == eps[2].name
    assert selections == [eps[2]]
    card.episode_combo.setCurrentIndex(0)
    assert card.episode_list.currentIndex().row() == 0
    card.season_combo.setCurrentIndex(2)
    assert card.season_tabs.currentIndex() == 2
    assert card.episode_list.currentIndex().data(Qt.UserRole) == eps[0]


def test_refresh_preserves_identity_and_clears_empty_list(series_card):
    card = series_card
    first, second = episode(1, 1), episode(2, 1)
    card.set_details(MediaDetails(episodes=[first, second]))
    card.episode_combo.setCurrentIndex(1)
    refreshed = replace(second, id="refreshed", url="file:///refreshed.mkv")
    card.set_details(MediaDetails(episodes=[refreshed, first]))
    assert card.episode_list.currentIndex().data(Qt.UserRole) == refreshed
    assert card.selected_channel() == refreshed
    card.set_details(MediaDetails())
    assert card.episode_list.model().rowCount() == 0
    assert not card.episode_list.currentIndex().isValid()
    assert card.season_tabs.count() == 0
    assert not card.play_button.isEnabled()


def test_mouse_keyboard_and_double_click_play_contract(series_card, qt_app):
    card = series_card
    eps = [episode(i, 1) for i in range(1, 4)]
    card.set_details(MediaDetails(episodes=eps))
    played = []
    card.play_requested.connect(played.append)
    card.show()
    card.scroll.ensureWidgetVisible(card.episode_list)
    qt_app.processEvents()
    view = card.episode_list
    second = view.model().index(1, 0)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(second).center())
    assert card.selected_channel() == eps[1]
    QTest.keyClick(view, Qt.Key_Down)
    assert card.selected_channel() == eps[2]
    for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        QTest.keyClick(view, key)
    assert played == []
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(second).center())
    QTest.mouseDClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(second).center())
    assert played == [eps[1]]
    card.play_button.click()
    assert played == [eps[1], eps[1]]
    card.set_details(MediaDetails(episodes=[replace(eps[0], url="")]))
    first = view.model().index(0, 0)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(first).center())
    QTest.mouseDClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(first).center())
    assert played == [eps[1], eps[1]]


def render_row(card, row=0, state=QStyle.State_Enabled):
    view = card.episode_list
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 400, 64)
    option.font = view.font()
    option.state = state
    image = QImage(400, 64, QImage.Format_ARGB32)
    image.fill(QColor(theme.SURFACE))
    painter = QPainter(image)
    view.itemDelegate().paint(painter, option, view.model().index(row, 0))
    painter.end()
    return image


@pytest.mark.parametrize(
    "progress, partial, watched",
    [
        (None, False, False),
        ((5, 100), False, False),
        ((6, 100), True, False),
        ((50, 100), True, False),
        ((89, 100), True, False),
        ((90, 100), False, True),
        ((110, 100), False, True),
        ((20, 0), False, False),
    ],
)
def test_progress_boundaries_are_painted_and_accessible(series_card, progress, partial, watched):
    card = series_card
    ep = episode(1, 1)
    card.set_details(
        MediaDetails(episodes=[ep], episode_info={ep.provider_key: {"duration": "42 dk"}})
    )
    card.set_progress_lookup(lambda cid: progress if cid == ep.id else None)
    index = card.episode_list.model().index(0, 0)
    description = index.data(Qt.AccessibleTextRole)
    assert ep.name in description and "42 dk" in description
    assert ("İzlendi" in description) == watched
    image = render_row(card)
    assert (image.pixelColor(4, 62) == QColor(theme.GOLD)) == partial
    if partial:
        assert image.pixelColor(398, 62) != QColor(theme.GOLD)
    assert (
        sum(
            image.pixelColor(x, y) == QColor(theme.GOLD)
            for x in range(70, 390)
            for y in range(8, 55)
        )
        > 0
    ) == watched


def test_progress_and_duration_refresh_without_changing_selection(series_card):
    card = series_card
    eps = [episode(1, 1), episode(2, 1)]
    card.set_progress_lookup(lambda cid: (50, 100))
    card.set_details(MediaDetails(episodes=eps))
    card.episode_combo.setCurrentIndex(1)
    before = render_row(card, 1)
    card.set_progress_lookup(lambda cid: (100, 100))
    assert before != render_row(card, 1)
    assert card.selected_channel() == eps[1]
    card.set_details(
        MediaDetails(episodes=eps, episode_info={eps[1].provider_key: {"duration": "1 sa"}})
    )
    assert "1 sa" in card.episode_list.currentIndex().data(Qt.AccessibleTextRole)
    card.set_progress_lookup(None)
    assert "İzlendi" not in card.episode_list.currentIndex().data(Qt.AccessibleTextRole)


def test_selected_and_hover_rows_use_theme_colors(series_card):
    series_card.set_details(MediaDetails(episodes=[episode(1, 1)]))
    selected = render_row(series_card, state=QStyle.State_Selected | QStyle.State_MouseOver)
    assert selected.pixelColor(1, 30) == QColor(theme.ACCENT)
    assert selected.pixelColor(5, 30) == QColor(theme.ACCENT_TINT)
    hover = render_row(series_card, state=QStyle.State_MouseOver)
    assert hover.pixelColor(5, 30) == QColor(theme.RAISED)


def test_keyboard_focus_is_visible_on_selected_episode(series_card):
    series_card.set_details(MediaDetails(episodes=[episode(1, 1)]))
    selected = render_row(series_card, state=QStyle.State_Selected)
    focused = render_row(series_card, state=QStyle.State_Selected | QStyle.State_HasFocus)
    assert selected != focused
    assert focused.pixelColor(1, 30) == QColor(theme.ACCENT)


def test_keyboard_reaches_overflow_seasons_without_playing(series_card, qt_app):
    card = series_card
    eps = [episode(i, i) for i in range(1, 31)]
    card.set_details(MediaDetails(episodes=eps))
    played = []
    card.play_requested.connect(played.append)
    card.resize(540, 700)
    card.show()
    card.season_tabs.setFocus()
    qt_app.processEvents()
    for _ in range(29):
        QTest.keyClick(card.season_tabs, Qt.Key_Right)
    assert card.season_combo.currentText() == "Sezon 30"
    assert card.episode_list.currentIndex().data(Qt.UserRole) == eps[-1]
    rect = card.season_tabs.tabRect(29)
    assert rect.left() >= 0 and rect.right() < card.season_tabs.width()
    QTest.keyClick(card.season_tabs, Qt.Key_Return)
    assert played == []


def test_long_seasons_scroll_and_episode_rows_stay_bounded(series_card, qt_app):
    card = series_card
    eps = [episode(i, i) for i in range(30)] + [episode(i + 30, 0) for i in range(1000)]
    card.set_details(MediaDetails(episodes=eps))
    card.resize(540, 700)
    card.show()
    qt_app.processEvents()
    assert card.season_tabs.usesScrollButtons()
    assert card.season_tabs.width() < sum(card.season_tabs.tabRect(i).width() for i in range(30))
    view = card.episode_list
    assert view.uniformItemSizes()
    assert view.height() <= 5 * 64
    assert view.verticalScrollBar().maximum() > 0
    view.setCurrentIndex(view.model().index(1000, 0))
    assert card.selected_channel() == eps[-1]
    assert view.indexWidget(view.currentIndex()) is None


def test_controller_preserves_progress_when_loading_episode_details(window):
    parent = controller_tests.channel(window, "series")
    ep = replace(episode(1, 1), id="ep", series_id=parent.series_id)
    stored = window.store.upsert_channels("home", [ep])[0]
    window.model.progress = {stored.id: (50, 100)}
    window.details.open(parent)
    window.requests[0][1](MediaDetails(episodes=[ep]))
    card = window.details.dialog
    assert render_row(card).pixelColor(4, 62) == QColor(theme.GOLD)
    window.model.progress = {stored.id: (100, 100)}
    card.set_details(MediaDetails(episodes=[stored]))
    assert "İzlendi" in card.episode_list.currentIndex().data(Qt.AccessibleTextRole)


def test_films_keep_episode_controls_hidden(card_factory):
    card, _ = card_factory(Channel("film", "Film", "file:///film.mkv", kind="movie"))
    card.set_details(MediaDetails(info={"duration": "90 dk"}))
    assert card.selectors.isHidden()
    assert card.selected_channel().id == "film"


def test_long_titles_keep_full_tooltips_in_a_narrow_card(series_card, qt_app, tmp_path):
    card = series_card
    eps = [episode(i, 1) for i in range(1, 5)]
    eps[0].name = "Uzun bir gecenin ardından eve dönüş ve beklenmedik bir karşılaşma"
    eps[1].name = "Ay ışığında"
    eps[2].name = "Son durak"
    eps[3].name = "Yeni başlangıç"
    progress = {eps[0].id: (45, 100), eps[1].id: (100, 100)}
    card.set_progress_lookup(progress.get)
    card.set_details(
        MediaDetails(
            episodes=eps,
            episode_info={
                eps[0].provider_key: {"duration": "42 dk"},
                eps[1].provider_key: {"duration": "00:48:12"},
            },
        )
    )
    card.setStyleSheet(theme.STYLE)
    card.resize(540, 700)
    card.show()
    card.scroll.ensureWidgetVisible(card.selectors)
    qt_app.processEvents()
    view = card.episode_list
    first = view.model().index(0, 0)
    assert eps[0].name in first.data(Qt.ToolTipRole)
    assert view.visualRect(first).height() == 64
    assert view.horizontalScrollBar().maximum() == 0
    assert card.selectors.width() <= card.scroll.viewport().width()
    assert card.selectors.grab().save(str(tmp_path / "episode-list.png"))
