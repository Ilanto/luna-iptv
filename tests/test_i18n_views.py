"""Populated translated views with inert media, no display, playback or network."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget
from shiboken6 import isValid

from luna_iptv.channel_banner import ChannelBanner
from luna_iptv.chips import ChipBar, ChoicePopup
from luna_iptv.epg import GuideIndex
from luna_iptv.guide_view import GuideView, ProgrammeCard
from luna_iptv.home_hero import remaining_text
from luna_iptv.i18n import get_language, set_language
from luna_iptv.library import KIND_LABELS, ChannelModel
from luna_iptv.models import Channel, Programme
from luna_iptv.statistics import StatisticsDialog, duration_text
from luna_iptv.tv_browser import TABS, TvBrowser
from luna_iptv.tv_mode import TvModeWindow


@pytest.fixture(autouse=True)
def english():
    previous = get_language()
    set_language("en")
    yield
    set_language(previous)


def test_lazy_labels_and_accessibility_preserve_provider_text(qt_app):
    # These constants were imported before the fixture selected English.
    assert KIND_LABELS["series"] == "Dizi"
    assert TABS[-1] == "TV modundan çık"
    host = QWidget()
    banner = ChannelBanner(host)
    channel = Channel("provider:1", "Şimdi", "", kind="series", group="Dizi")
    banner.set_content(channel)
    assert banner.title.text() == "Dizi"  # Provider data is never translated.
    assert banner.time.text() == "Series"
    model = ChannelModel()
    model.reset([channel], set())
    model.set_locked({channel.id})
    model.set_unseen_series({channel.id})
    assert model.index(0).data(Qt.AccessibleTextRole) == "Şimdi, Dizi, locked, new episode"
    assert model.index(0).data(Qt.UserRole) is channel
    assert channel.group == "Dizi"
    names = []
    browser = SimpleNamespace(
        animation=SimpleNamespace(stop=lambda: None),
        tv=SimpleNamespace(
            row=-1, tab=5, focused_channel=lambda: None, setAccessibleName=names.append
        ),
        update=lambda: None,
    )
    TvBrowser.focus_changed(browser)
    assert names == ["Exit TV mode"]
    host.close()


def test_category_counts_leave_provider_labels_and_keys_intact(qt_app):
    host = QWidget()
    bar = ChipBar(show_counts=True, parent=host)
    provider = "Dizi, Özel"
    bar.set_items([(provider, provider, 1234)])
    assert bar.all_label == "All"
    assert bar._items == [(provider, provider, 1234)]
    assert f"{provider}  1,234" in [button.text() for button in bar.buttons().values()]
    popup = ChoicePopup([(provider, provider, 1234)], provider, parent=host)
    assert popup.list.item(0).text() == f"{provider}   1,234"
    assert popup.list.item(0).data(Qt.UserRole) == provider
    set_language("tr")
    assert bar._label(provider, 1234) == f"{provider}  1.234"
    host.close()


def test_populated_guide_english_counts_dates_and_programme_data(qt_app):
    view = GuideView()
    today = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    channels = [Channel(str(i), f"Kanal {i}", "", tvg_id=str(i)) for i in range(2)]
    programmes = [
        Programme(str(i), f"Özel yayın {i}", today, today + timedelta(hours=1), "Açıklama")
        for i in range(2)
    ]
    index = GuideIndex(programmes)
    try:
        view.set_rows([(channel, index) for channel in channels])
        assert view.count_label.text() == "2 channels"
        view.search.setText("Özel")
        assert view.count_label.text() == "2 programmes"
        view.search.setText("Özel yayın 0")
        assert view.count_label.text() == "1 programme"
        view.search.clear()
        view.set_rows([(channels[0], index)])
        assert view.count_label.text() == "1 channel"
        assert view.archive_date.displayFormat() == "dd/MM/yyyy"
        assert view.day_buttons[today.date()].text() == "Today"
        assert view.day_buttons[today.date() + timedelta(days=1)].text() == "Tomorrow"
        assert view.grid.rows[0][0].name == "Kanal 0"
        start = datetime(2026, 10, 9, 12).astimezone()
        programme = Programme("0", "Özel yayın", start, start + timedelta(hours=1), "Açıklama")
        card = ProgrammeCard(channels[0], programme, now=start)
        try:
            texts = [label.text() for label in card.findChildren(QLabel)]
            assert "Friday 09 October · 12:00 – 13:00  ·  60 min" in texts
            assert "Özel yayın" in texts and "Açıklama" in texts
            assert card.watch_button.text() == "Watch channel"
        finally:
            card.close()
        set_language("tr")
        view.set_rows([(channel, index) for channel in channels])
        assert view.count_label.text() == "2 kanal"
    finally:
        view.close()


def test_populated_home_english_and_data_stability(qt_app, tmp_path, monkeypatch):
    import luna_iptv.layout as layout_module
    import luna_iptv.window as window_module
    from luna_iptv.storage import Store
    from tests.test_mini_player import InertPlayer, InertVideo

    monkeypatch.setattr(window_module, "Player", InertPlayer)
    monkeypatch.setattr(layout_module, "VideoWidget", InertVideo)
    store = Store(tmp_path / "home.sqlite3")
    store.update_profile(store.profile_id, name="Çağrı", color="#E8B04B")
    store.save_source({"id": "provider", "name": "Dizi", "type": "m3u"})
    store.replace_channels(
        "provider",
        [
            Channel("live", "Canlı", "", group="Dizi"),
            Channel("film", "Şimdi", "", kind="movie", group="Dizi"),
            Channel("film2", "Sonra", "", kind="movie", group="Dizi"),
            Channel("series", "Dizi", "", kind="series"),
        ],
    )
    store.set_favorite("provider:live", True)
    store.save_progress("provider:film", 1200, 4800)
    before = [(c.id, c.name, c.group) for c in store.channels()]
    window = window_module.MainWindow(store)
    try:
        home = window.home_view
        home.clock = lambda: datetime(2026, 10, 9, 9)
        home.refresh()
        assert home.heading.text() == "Good morning, Çağrı"
        assert home.summary.text() == "1 live channel · 2 films · 1 series"
        assert home.rows["continue"].title == "Continue watching"
        assert home.rows["continue"].view.accessibleName() == "Continue watching, 1 item"
        assert home.hero.title.text() == "Şimdi"
        assert home.hero.meta.text() == "Dizi · Film / video"
        assert home.hero.left.text() == "1 hr left"
        assert [(c.id, c.name, c.group) for c in store.channels()] == before
        assert store.sources()[0]["name"] == "Dizi"
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_populated_statistics_english_hours_and_user_names(qt_app):
    store = SimpleNamespace(
        profile_id=1,
        profile=lambda pid: {"name": "Çağrı"},
        watch_statistics=lambda: {
            "week_seconds": 5400,
            "days": [(f"2026-10-{i:02d}", 3600 if i == 9 else 0) for i in range(3, 10)],
            "top": [("Dizi", 5400)],
            "live_seconds": 3600,
            "vod_seconds": 1800,
        },
    )
    dialog = StatisticsDialog(store)
    try:
        assert dialog.windowTitle() == "Statistics"
        assert dialog.total_label.text() == "You watched 1.5 hr this week"
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Çağrı · Statistics" in texts
        assert "1. Dizi  ·  1 hr 30 min" in texts
        assert "Live: 1 hr  ·  Films / series: 30 min" in texts
        assert dialog.chart.accessibleName() == "Watch time for the last 7 days"
        assert remaining_text(3660) == "1 hr 1 min left"
        assert duration_text(3660) == "1 hr 1 min"
        set_language("tr")
        assert remaining_text(3660) == "1 sa 1 dk kaldı"
        assert duration_text(3660) == "1 sa 1 dk"
    finally:
        dialog.close()


def test_tv_fallback_group_does_not_merge_provider_category():
    channels = [
        Channel("1", "Uncategorised", "", kind="movie", group=""),
        Channel("2", "Provider category", "", kind="movie", group="Other"),
        Channel("3", "Provider Turkish category", "", kind="movie", group="Diğer"),
    ]
    view = SimpleNamespace(
        _closed=False,
        focused_channel=lambda: None,
        host=SimpleNamespace(
            model=SimpleNamespace(channels=channels),
            kids_profile=lambda: False,
            proxy=SimpleNamespace(source="", category_hidden=lambda c: False),
        ),
        series=None,
        tab=1,
        row=-1,
        focus_changed=lambda: None,
    )
    TvModeWindow.refresh_rows(view)
    assert [(title, [c.id for c in items]) for title, items in view.rows] == [
        ("Other", ["1"]),
        ("Other", ["2"]),
        ("Diğer", ["3"]),
    ]
    assert [channel.group for channel in channels] == ["", "Other", "Diğer"]
