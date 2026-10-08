"""Personal home rows, safe activation and updates without a desktop window."""

from datetime import datetime

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid

from luna_iptv.library import LOCKED_ROLE, PROGRESS_ROLE, ChannelDelegate
from luna_iptv.models import Channel
from luna_iptv.parental import hash_pin
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "home.sqlite3")
    store.save_source({"id": "demo", "name": "Demo", "type": "m3u"})
    store.replace_channels(
        "demo",
        [
            Channel("z", "Z Haber", "file:///z", group="Haber"),
            Channel("a", "A Haber", "file:///a", group="Haber"),
            Channel("b", "B Haber", "file:///b", group="Haber"),
            Channel("r", "Son Kanal", "file:///r"),
            Channel("m", "Ay Işığı", "file:///m", kind="movie", group="Sinema"),
            Channel("s", "Gece Yolculuğu", "", kind="series", series_id="7"),
            Channel("e", "İlk Bölüm", "file:///e", kind="movie", series_id="7"),
            Channel("end", "Biten Film", "file:///end", kind="movie"),
            Channel("new", "Yeni Film", "file:///new", kind="movie"),
        ],
    )
    value = MainWindow(store)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def seed(window):
    for key in ("z", "a", "b", "m", "s", "e"):
        window.store.set_favorite("demo:" + key, True)
    for key, position, duration in (
        ("m", 30, 100),
        ("z", 60, 0),
        ("r", 60, 0),
        ("e", 45, 100),
        ("end", 95, 100),
        ("new", 5, 100),
        ("s", 30, 100),
    ):
        window.store.save_progress("demo:" + key, position, duration)
    window.refresh_library()


def ids(window, key):
    model = window.home_view.rows[key].model
    return [model.index(row, 0).data(Qt.UserRole).id for row in range(model.rowCount())]


def channel(window, key):
    return next(c for c in window.model.channels if c.id == "demo:" + key)


def test_startup_home_and_browse_filters_survive_entering(window):
    assert next(iter(window.nav_buttons)) == "home"
    assert window.nav_buttons["home"].isChecked()
    assert window.library_pages.currentWidget() is window.home_view
    window.set_section("movie")
    window.search.setText("ay")
    before = (window.proxy.section, window.proxy.query, window.proxy.group)
    window.set_section("home")
    assert (window.proxy.section, window.proxy.query, window.proxy.group) == before
    window.set_section("guide")
    window.set_section("home")
    assert window.library_pages.currentWidget() is window.home_view


@pytest.mark.parametrize(
    ("hour", "greeting"),
    [
        (0, "İyi geceler"),
        (4, "İyi geceler"),
        (5, "Günaydın"),
        (11, "Günaydın"),
        (12, "İyi günler"),
        (17, "İyi günler"),
        (18, "İyi akşamlar"),
        (22, "İyi akşamlar"),
        (23, "İyi geceler"),
    ],
)
def test_greeting_uses_injected_local_clock(window, hour, greeting):
    window.home_view.clock = lambda: datetime(2026, 10, 8, hour)
    window.home_view.refresh()
    name = window.store.profile(window.store.profile_id)["name"]
    assert window.home_view.heading.text() == f"{greeting}, {name}"
    assert window.home_view.summary.text() == "4 canlı kanal · 3 film · 1 dizi"


def test_rows_have_the_requested_content_order_and_live_roles(window):
    seed(window)
    assert ids(window, "continue") == ["demo:e", "demo:m"]
    assert ids(window, "favorite_live") == ["demo:z", "demo:a", "demo:b"]
    assert ids(window, "recent_live") == ["demo:r"]
    assert ids(window, "favorite_vod") == ["demo:m", "demo:s"]
    for key, row in window.home_view.rows.items():
        assert not row.isHidden()
        assert isinstance(row.view.itemDelegate(), ChannelDelegate)
        assert row.view.itemDelegate().logos is window.logos
        assert row.view.itemDelegate().posters is window.posters
        assert row.view.poster_mode == (key in ("continue", "favorite_vod"))
        assert row.view.accessibleName() == f"{row.title}, {row.model.rowCount()} öğe"
    model = window.home_view.rows["continue"].model
    assert model.index(0, 0).data(PROGRESS_ROLE) == 0.45
    window.model.set_progress("demo:e", 60, 100)
    assert model.index(0, 0).data(PROGRESS_ROLE) == 0.6
    window.model.set_locked({"demo:e"})
    assert model.index(0, 0).data(LOCKED_ROLE)
    assert window.home_view.empty.isHidden()


def test_empty_rows_and_empty_actions(window, monkeypatch):
    home = window.home_view
    assert all(row.isHidden() for row in home.rows.values())
    assert not home.empty.isHidden() and not home.live_button.isHidden()
    home.live_button.click()
    assert window.library_pages.currentWidget() is window.browse
    window.store.replace_channels("demo", [])
    window.set_section("home")
    window.refresh_library()
    assert not home.empty.isHidden() and home.live_button.isHidden()
    added = []
    monkeypatch.setattr(window, "add_source", lambda: added.append(True))
    home.add_button.click()
    assert added == [True]


def test_kids_never_see_locked_rows_after_switch(window):
    seed(window)
    window.store.set_pin_hash(hash_pin("2468"))
    window.store.set_group_locked("demo", "Haber", True)
    window.store.set_group_locked("demo", "Sinema", True)
    window.apply_locks()
    assert "demo:z" in ids(window, "favorite_live")
    parent = window.store.profile_id
    kids = window.store.create_profile("Minik Ay", "#a9b8ff", kids=True)
    window.store.use_profile(kids)
    for key in ("z", "a", "m", "s"):
        window.store.set_favorite("demo:" + key, True)
        window.store.save_progress("demo:" + key, 30, 100)
    window.store.use_profile(parent)
    assert window.switch_profile(kids)
    assert window.proxy.hide_locked
    assert ids(window, "favorite_live") == []
    assert ids(window, "continue") == []
    assert ids(window, "favorite_vod") == ["demo:s"]
    assert all(not (set(ids(window, key)) & window.model.locked) for key in window.home_view.rows)
    assert window.home_view.heading.text().endswith(", Minik Ay")


def test_activation_uses_resume_or_pin_guarded_details(window, monkeypatch):
    seed(window)
    played, opened, unlocked = [], [], []
    monkeypatch.setattr(window, "request_play", lambda c: played.append(c.id))
    monkeypatch.setattr(window.details, "open", lambda c: opened.append(c.id))
    monkeypatch.setattr(window, "unlock_channel", lambda c: unlocked.append(c.id) or False)
    for key in ("continue", "favorite_live", "recent_live", "favorite_vod"):
        row = window.home_view.rows[key]
        row.view.clicked.emit(row.model.index(0, 0))
    assert played == ["demo:e", "demo:z", "demo:r"]
    assert unlocked == ["demo:m"] and opened == []
    monkeypatch.setattr(window, "unlock_channel", lambda c: True)
    row = window.home_view.rows["favorite_vod"]
    row.view.activated.emit(row.model.index(1, 0))
    assert opened == ["demo:s"]
    window.set_section("movie")
    window.activate_index(window.proxy.index(0, 0))
    assert len(opened) == 2


def test_favorite_toggle_profile_switch_and_hidden_refresh(window):
    window.toggle_channel_favorite(channel(window, "a"))
    assert ids(window, "favorite_live") == ["demo:a"]
    window.toggle_channel_favorite(channel(window, "a"))
    assert window.home_view.rows["favorite_live"].isHidden()
    window.set_section("live")
    window.toggle_channel_favorite(channel(window, "m"))
    window.set_section("home")
    assert ids(window, "favorite_vod") == ["demo:m"]
    other = window.store.create_profile("Başka Ay", "#a9b8ff")
    assert window.switch_profile(other)
    assert all(row.isHidden() for row in window.home_view.rows.values())
    assert window.home_view.heading.text().endswith(", Başka Ay")


def test_progress_stop_finish_and_clear_history_refresh(window):
    window.current = channel(window, "m")
    window._position, window._duration = 30, 100
    window.stop_playback()
    assert ids(window, "continue") == ["demo:m"]
    window._position = 100
    window._playback_active = True
    window._playback_token = 17
    window.playback_finished(17, "eof", "")
    assert ids(window, "continue") == []
    window._position = 40
    window.save_progress()
    assert ids(window, "continue") == ["demo:m"]
    window.clear_history(reset_progress=True)
    assert ids(window, "continue") == []


def test_context_menu_is_the_shared_channel_menu(window, monkeypatch):
    seed(window)
    row = window.home_view.rows["favorite_live"]
    row.view.doItemsLayout()
    seen = []
    original = window.build_channel_menu

    def menu_for(c):
        menu = original(c)
        seen.append((c.id, [a.text() for a in menu.actions()]))
        monkeypatch.setattr(menu, "exec", lambda point: None)
        return menu

    monkeypatch.setattr(window, "build_channel_menu", menu_for)
    point = row.view.visualRect(row.model.index(0, 0)).center()
    row.view.customContextMenuRequested.emit(point)
    assert seen[0][0] == "demo:z"
    assert "Favorilerden çıkar" in seen[0][1]


def test_horizontal_scroll_buttons_wheel_and_keyboard(window, qt_app):
    seed(window)
    window.resize(1400, 900)
    window.show()
    qt_app.processEvents()
    row = window.home_view.rows["favorite_live"]
    row.view.setFixedWidth(400)
    qt_app.processEvents()
    bar = row.view.horizontalScrollBar()
    assert bar.maximum() > 0
    row.next_button.click()
    assert bar.value() > 0
    row.previous_button.click()
    assert bar.value() == 0
    event = QWheelEvent(
        QPointF(20, 20),
        QPointF(20, 20),
        QPoint(),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    qt_app.sendEvent(row.view.viewport(), event)
    assert bar.value() > 0
    row.view.setFocus()
    row.view.setCurrentIndex(row.model.index(0, 0))
    QTest.keyClick(row.view, Qt.Key_Right)
    assert row.view.currentIndex().row() == 1
    QTest.keyClick(row.view, Qt.Key_Tab)
    assert window.home_view.rows["recent_live"].view.hasFocus()


def test_rows_are_capped_and_old_resumable_items_survive_many_recent_live(window, monkeypatch):
    movies = [Channel(f"movie{i}", f"Film {i}", "", kind="movie") for i in range(25)]
    live = [Channel(f"live{i}", f"Kanal {i:02}", "") for i in range(70)]
    window.model.reset(
        movies + live, {c.id for c in movies + live[:30]}, {c.id: (30, 100) for c in movies}
    )
    recent = [c.id for c in reversed(live + movies)]
    monkeypatch.setattr(window.store, "recent_ids", lambda limit: recent[:limit])
    monkeypatch.setattr(window.store, "progress", lambda *_: pytest.fail("Per-card store query"))
    window.home_view.refresh()
    assert ids(window, "continue") == [c.id for c in reversed(movies)][:20]
    assert ids(window, "favorite_live") == [c.id for c in reversed(live[:30])][:20]
    assert ids(window, "recent_live") == [c.id for c in reversed(live[30:])][:20]
    assert len(ids(window, "favorite_vod")) == 20


def test_artwork_only_requests_visible_unlocked_cards_across_strips(window, qt_app, monkeypatch):
    seed(window)
    for c in window.model.channels:
        c.logo = f"https://example.test/{c.id}.png"
    monkeypatch.setattr(window.logos, "_start", lambda: None)
    monkeypatch.setattr(window.posters, "_start", lambda: None)
    window.model.set_locked({"demo:z"})
    window.resize(1400, 900)
    window.show()
    qt_app.processEvents()
    for row in window.home_view.rows.values():
        row.artwork._schedule()
    queued = set(window.logos._queue) | set(window.posters._queue)
    assert channel(window, "e").logo in queued
    assert channel(window, "a").logo in queued
    assert channel(window, "z").logo not in queued
    assert channel(window, "s").logo not in queued  # row below the scroll area's viewport
    window.set_section("guide")
    for row in window.home_view.rows.values():
        row.artwork._schedule()
    assert not window.logos._queue and not window.posters._queue


def test_horizontal_artwork_does_not_fetch_the_whole_strip(window, qt_app, monkeypatch):
    channels = [
        Channel(str(i), f"Kanal {i}", "", logo=f"https://example.test/{i}.png") for i in range(20)
    ]
    window.model.reset(channels, {c.id for c in channels})
    window.home_view.refresh()
    monkeypatch.setattr(window.logos, "_start", lambda: None)
    monkeypatch.setattr(window.posters, "_start", lambda: None)
    window.show()
    qt_app.processEvents()
    row = window.home_view.rows["favorite_live"]
    row.artwork._schedule()
    assert 0 < len(window.logos._queue) < 6
    assert channels[-1].logo not in window.logos._queue
    row.view.scrollTo(row.model.index(19, 0))
    row.artwork._schedule()
    assert row.model.index(19, 0).data(Qt.UserRole).logo in window.logos._queue
    assert channels[0].logo not in window.logos._queue


def test_partial_first_card_keeps_its_artwork_request(window, qt_app, monkeypatch):
    channels = [
        Channel(str(i), f"Kanal {i:02}", "", logo=f"https://example.test/{i}.png")
        for i in range(20)
    ]
    window.model.reset(channels, {c.id for c in channels})
    window.home_view.refresh()
    monkeypatch.setattr(window.logos, "_start", lambda: None)
    monkeypatch.setattr(window.posters, "_start", lambda: None)
    window.show()
    qt_app.processEvents()
    row = window.home_view.rows["favorite_live"]
    row.view.horizontalScrollBar().setValue(row.view.gridSize().width() - 40)
    row.artwork._schedule()
    assert channels[0].logo in window.logos._queue


def test_startup_select_focuses_a_visible_home_card_and_enter_plays(window, qt_app, monkeypatch):
    seed(window)
    played = []
    monkeypatch.setattr(window, "request_play", lambda c: played.append(c.id))
    window.show()
    qt_app.processEvents()
    window.restore_last_channel()
    row = window.home_view.rows["recent_live"]
    assert row.view.hasFocus()
    assert row.view.currentIndex().data(Qt.UserRole).id == "demo:r"
    QTest.keyClick(row.view, Qt.Key_Return)
    assert played == ["demo:r"]


def test_guide_timer_repaints_visible_live_strips_only(window, qt_app, monkeypatch):
    seed(window)
    window.show()
    qt_app.processEvents()
    window._guide_index["demo"] = object()
    updated = []
    with monkeypatch.context() as patch:
        for key, row in window.home_view.rows.items():
            patch.setattr(row.view.viewport(), "update", lambda key=key: updated.append(key))
        window._guide_timer.timeout.emit()
        assert "favorite_live" in updated
        assert not {"continue", "favorite_vod"} & set(updated)
        window.set_section("movie")
        updated.clear()
        window._guide_timer.timeout.emit()
        assert not updated


def test_large_catalogue_refresh_uses_one_pass_and_bulk_history(window, monkeypatch):
    class Catalogue(list):
        visits = 0

        def __iter__(self):
            for item in super().__iter__():
                self.visits += 1
                yield item

    channels = Catalogue(Channel(str(i), f"Kanal {i:05}", "") for i in range(50_000))
    window.model.reset(channels, {str(i) for i in range(30)})
    calls = []

    def history(limit):
        calls.append(limit)
        return [str(i) for i in range(60)]

    monkeypatch.setattr(window.store, "recent_ids", history)
    monkeypatch.setattr(window.store, "progress", lambda *_: pytest.fail("Per-card query"))
    channels.visits = 0
    window.home_view.refresh()
    assert channels.visits == 50_000
    assert calls == [50_000]
    assert ids(window, "favorite_live") == [str(i) for i in range(20)]
    assert ids(window, "recent_live") == [str(i) for i in range(30, 50)]
    assert window.home_view.summary.text().startswith("50.000 canlı kanal")


def test_hero_features_what_to_continue_then_a_live_favourite(qt_app):
    from luna_iptv.home_hero import HomeHero, remaining_text
    from luna_iptv.models import Channel

    hero = HomeHero()
    film = Channel("h:f", "Dune", "file:///f", kind="movie", group="Bilim Kurgu")
    played = []
    hero.play.connect(played.append)
    hero.show_resume(film, 2400, 9000)
    assert not hero.isHidden() and hero.title.text() == "Dune"
    assert hero.left.text() == "1 sa 50 dk kaldı" and hero.play_button.text() == "Devam et"
    hero.play_button.click()
    assert played == [film]
    hero.show_live(Channel("h:l", "NTV", "file:///l"))
    assert hero.eyebrow.text() == "FAVORİN ŞU AN YAYINDA" and hero.details_button.isHidden()
    hero.clear()
    assert hero.isHidden()
    assert remaining_text(59) == "1 dk kaldı" and remaining_text(3600) == "1 sa kaldı"
