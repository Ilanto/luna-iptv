import pytest
from PySide6.QtWidgets import QDialog
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels(
        "home",
        [
            Channel("film", "Film <b>title</b>", "file:///film.mkv", kind="movie"),
            Channel("live", "Live", "file:///live.ts"),
        ],
    )
    store.save_progress("home:film", 42, 100)
    value = MainWindow(store)
    value.loads = []
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: value.loads.append((a, kw)))
    monkeypatch.setattr(value.player, "set_property", lambda *a: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


@pytest.mark.parametrize("choice,start", [("resume_button", 42), ("restart_button", 0)])
def test_library_selection_waits_for_resume_choice(window, choice, start):
    window.set_section("movie")
    window.activate_index(window.proxy.index(0, 0))
    assert not window.loads
    dialog = window._resume_dialog
    assert isinstance(dialog, QDialog)
    getattr(dialog, choice).click()
    assert window.loads[0][1]["start"] == start
    assert window.current.id == "home:film"
    window.loaded()
    assert window.store.progress("home:film")[0] == start


def test_cancel_and_removed_source_do_not_change_playback(window):
    film = window.store.channels("home")[0]
    window.request_play(film)
    window._resume_dialog.reject()
    assert not window.loads and window.current is None
    window.request_play(film)
    window.store.remove_source("home")
    window.refresh_library()
    window._resume_dialog.resume_button.click()
    assert not window.loads


def test_new_selection_invalidates_previous_resume_dialog(window):
    film = next(c for c in window.store.channels() if c.kind == "movie")
    live = next(c for c in window.store.channels() if c.kind == "live")
    window.request_play(film)
    old = window._resume_dialog
    window.request_play(live)
    old.resume_button.click()
    assert len(window.loads) == 1 and window.current.id == live.id
    assert window.loads[0][1]["start"] == 0


@pytest.mark.parametrize("reset", [False, True])
def test_clear_active_history_stays_cleared_until_new_user_play(window, reset):
    film = next(c for c in window.store.channels() if c.kind == "movie")
    window.play(film)
    window.loaded()
    window._position, window._duration = 50, 100
    window.clear_history("home", reset_progress=reset)
    window.player_property("time-pos", 55)
    window.loaded()
    assert window.store.recent_ids() == []
    assert window.store.progress(film.id)[0] == (0 if reset else 55)
    window.play(film, recovering=True)
    window.loaded()
    assert window.store.recent_ids() == []
    window.play(film, start_override=0)
    window.loaded()
    assert window.store.recent_ids() == [film.id]


def test_history_confirmation_cancel_and_default_preserves_resume(window):
    window.set_section("recent")
    assert not window.history_clear_button.isHidden()
    window.confirm_clear_history()
    dialog = window._history_dialog
    assert not dialog.reset_positions.isChecked()
    dialog.reject()
    assert window.store.recent_ids() == ["home:film"]
    window.confirm_clear_history()
    window._history_dialog.clear_button.click()
    assert window.store.recent_ids() == []
    assert window.store.progress("home:film") == (42, 100)
    assert window.proxy.rowCount() == 0


def test_track_menu_selection_is_remembered_and_old_menu_cannot_change_new_file(window):
    film = next(c for c in window.store.channels() if c.kind == "movie")
    window.play(film)
    window.loaded()
    window.player_property("track-list", [{"type": "audio", "id": 2, "lang": "tur"}])
    menu = window.build_track_menu()
    menu.actions()[0].menu().actions()[1].trigger()
    assert window.store.playback_preferences("home")["audio"]["lang"] == "tr"
    window.play(film, start_override=0)
    menu.actions()[0].menu().actions()[0].trigger()
    assert window.store.playback_preferences("home")["audio"]["mode"] == "track"
