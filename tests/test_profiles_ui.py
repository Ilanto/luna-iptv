"""Profiles and the parental PIN as people meet them: prompts, locks, switching."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from shiboken6 import isValid

from luna_iptv import parental_ui
from luna_iptv.library import LOCKED_ROLE
from luna_iptv.models import Channel
from luna_iptv.parental import hash_pin
from luna_iptv.parental_ui import ATTEMPTS, NewPinDialog, ParentalDialog, PinDialog
from luna_iptv.profiles_ui import ProfileEditor, ProfilePicker, ProfilesDialog
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

PIN = "2468"


@pytest.fixture(autouse=True)
def fresh_attempts():
    parental_ui.reset_failures()
    yield
    parental_ui.reset_failures()


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "home", "type": "m3u", "name": "Home"})
    value.replace_channels(
        "home",
        [
            Channel("news", "Haber", "file:///news.ts", group="Haber"),
            Channel("kids", "Çizgi", "file:///kids.ts", group="Çocuk"),
            Channel("late", "Gece", "file:///late.ts", group="XXX Adult"),
            Channel("film", "Film", "file:///film.mkv", kind="movie", group="XXX Adult"),
        ],
    )
    yield value
    value.close()


@pytest.fixture
def pin_answers(monkeypatch):
    """Answers the PIN prompt from a queue and records why it was asked."""
    answers, asked = [], []

    def ask(store, reason, parent=None):
        if not store.pin_hash():
            return True
        asked.append(reason)
        return answers.pop(0) if answers else False

    monkeypatch.setattr("luna_iptv.window.ask_pin", ask)
    return answers, asked


@pytest.fixture
def window(qt_app, store, monkeypatch, pin_answers):
    value = MainWindow(store)
    played = []
    monkeypatch.setattr(value.player, "load", lambda url, *a, **kw: played.append(url))
    value.played = played
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def lock_adult(store):
    store.set_pin_hash(hash_pin(PIN))
    assert store.lock_adult_groups() == 1


def visible_names(window):
    return [
        window.proxy.index(row, 0).data(Qt.UserRole).name for row in range(window.proxy.rowCount())
    ]


def channel(window, name):
    return next(c for c in window.model.channels if c.name == name)


def test_pin_dialog_accepts_the_pin_and_locks_out_after_repeated_mistakes(qt_app, store):
    store.set_pin_hash(hash_pin(PIN))
    now = [100.0]
    dialog = PinDialog(store, "Neden", clock=lambda: now[0])
    dialog.field.setText("0000")
    assert not dialog.try_pin()
    assert "4 deneme" in dialog.error.text()
    for _ in range(ATTEMPTS - 1):
        dialog.field.setText("0000")
        dialog.try_pin()
    assert not dialog.field.isEnabled() and "30 saniye" in dialog.error.text()
    again = PinDialog(store, "Neden", clock=lambda: now[0])  # reopening does not reset
    assert not again.open_button.isEnabled()
    now[0] += 31
    again._refresh_lockout()
    again.field.setText(PIN)
    assert again.try_pin() and again.result() == QDialog.Accepted


def test_ask_pin_is_free_without_a_pin(qt_app, store):
    assert parental_ui.ask_pin(store, "Neden")


def test_new_pin_needs_digits_twice(qt_app):
    dialog = NewPinDialog()
    dialog.first.setText("12")
    dialog.second.setText("12")
    assert not dialog.save() and "4 ile 8" in dialog.error.text()
    dialog.first.setText("1234")
    dialog.second.setText("1243")
    assert not dialog.save() and "aynı değil" in dialog.error.text()
    dialog.second.setText("1234")
    assert dialog.save() and dialog.pin == "1234"


def test_locked_category_asks_every_time_and_hides_artwork(window, store, pin_answers):
    answers, asked = pin_answers
    lock_adult(store)
    window.refresh_library()
    late = channel(window, "Gece")
    row = window.model.channels.index(late)
    assert window.model.index(row).data(LOCKED_ROLE)
    assert window.channel_list.itemDelegate().artwork(late, True) is None
    window.request_play(late)
    assert window.played == [] and len(asked) == 1
    answers.extend([True, True])
    window.request_play(late)
    window.request_play(late)
    assert window.played == ["file:///late.ts"] * 2 and len(asked) == 3
    window.request_play(channel(window, "Haber"))  # unlocked: no prompt
    assert len(asked) == 3


def test_kids_profile_never_sees_locked_content(window, store, pin_answers):
    _, asked = pin_answers
    lock_adult(store)
    kid = store.create_profile("Ece", "#4FC3A1", kids=True)
    assert window.switch_profile(kid)  # entering a kids profile is free
    window.set_section("live")
    assert visible_names(window) == ["Haber", "Çizgi"]
    window.search.setText("gece")
    assert visible_names(window) == []
    window.search.clear()
    window.request_play(channel(window, "Gece"))
    assert window.played == [] and asked == []
    assert "kapalı" in window.message.text()


def test_leaving_a_kids_profile_or_entering_a_protected_one_asks(window, store, pin_answers):
    answers, asked = pin_answers
    store.set_pin_hash(hash_pin(PIN))
    me = store.profile_id
    kid = store.create_profile("Ece", "#4FC3A1", kids=True)
    guarded = store.create_profile("Ebeveyn", "#7C8CF8", protected=True)
    window.switch_profile(kid)
    assert not window.switch_profile(me) and store.profile_id == kid
    answers.append(True)
    assert window.switch_profile(me) and store.profile_id == me
    assert not window.switch_profile(guarded) and store.profile_id == me
    assert len(asked) == 2


def test_profiles_keep_their_own_favorites(window, store):
    news = channel(window, "Haber")
    window.toggle_channel_favorite(news)
    other = store.create_profile("Misafir", "#F07A8C")
    window.switch_profile(other)
    assert window.model.favorites == set()
    assert window.profile_button.profile["name"] == "Misafir"
    window.switch_profile(1)
    assert window.model.favorites == {news.id}


def test_settings_sources_and_backups_are_behind_the_pin(window, store, pin_answers, monkeypatch):
    _, asked = pin_answers
    store.set_pin_hash(hash_pin(PIN))
    opened = []
    monkeypatch.setattr("luna_iptv.window.SettingsDialog", lambda *a: opened.append(a))
    window.open_settings()
    window.add_source()
    window.remove_source(store.sources()[0])
    window.restore_backup()
    window.open_profiles()
    window.open_parental()
    assert opened == [] and len(asked) == 6
    assert len(store.sources()) == 1


def test_single_channel_lock_from_the_card_menu(window, store, pin_answers):
    answers, asked = pin_answers
    store.set_pin_hash(hash_pin(PIN))
    news = channel(window, "Haber")
    labels = [a.text() for a in window.build_channel_menu(news).actions()]
    assert "Kilitle" in labels
    window.set_channel_locked(news, True)
    assert news.id in window.model.locked and asked == []
    labels = [a.text() for a in window.build_channel_menu(news).actions()]
    assert "Kilidi kaldır…" in labels
    window.set_channel_locked(news, False)  # refused
    assert news.id in window.model.locked
    answers.append(True)
    window.set_channel_locked(news, False)
    assert news.id not in window.model.locked


def test_zapping_skips_locked_channels(window, store, pin_answers):
    _, asked = pin_answers
    lock_adult(store)
    window.refresh_library()
    window.set_section("live")
    window.request_play(channel(window, "Çizgi"))
    window.zap(1)
    assert window.played[-1] == "file:///news.ts" and asked == []


def test_parental_dialog_sets_a_pin_and_locks_adult_categories(qt_app, store, monkeypatch):
    def choose(dialog):
        dialog.pin = PIN
        return QDialog.Accepted

    monkeypatch.setattr(NewPinDialog, "exec", choose)
    dialog = ParentalDialog(store)
    changes = []
    dialog.changed.connect(lambda: changes.append(True))
    assert dialog.setup_button.isVisibleTo(dialog) and not dialog.tree.isVisibleTo(dialog)
    assert dialog.set_up()
    assert store.locked_groups() == {("home", "XXX Adult")}
    source = dialog.tree.topLevelItem(0)
    items = {source.child(i).data(0, Qt.UserRole)[2]: source.child(i) for i in range(3)}
    assert items["XXX Adult"].checkState(0) == Qt.Checked
    items["Çocuk"].setCheckState(0, Qt.Checked)
    assert ("home", "Çocuk") in store.locked_groups() and changes
    dialog.search.setText("cocuk")
    assert items["Haber"].isHidden() and not items["Çocuk"].isHidden()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    assert dialog.turn_off() and store.pin_hash() is None


def test_profile_picker_editor_and_manager(qt_app, store, monkeypatch):
    store.create_profile("Ece", "#4FC3A1", kids=True)
    picker = ProfilePicker(store.profiles(), store.profile_id)
    picker.avatars[2].click()
    assert picker.chosen == 2 and picker.result() == QDialog.Accepted
    editor = ProfileEditor(store)
    editor.name.setText("ece")
    assert not editor.save() and editor.error.text()
    editor.name.setText("Misafir")
    editor.kids.setChecked(True)
    assert editor.save()
    assert store.profile(editor.profile_id)["kids"]
    manager = ProfilesDialog(store)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    assert manager.remove(store.profile(editor.profile_id))
    assert [p["name"] for p in store.profiles()] == ["Ben", "Ece"]
