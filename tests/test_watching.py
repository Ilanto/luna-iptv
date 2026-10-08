"""Sleep timer, the next episode and channel numbers."""

import pytest
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.watching import Countdown, NumberEntry, SleepTimer, next_episode
from luna_iptv.window import MainWindow


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_sleep_timer_counts_down_extends_and_expires(qt_app):
    clock = Clock()
    timer = SleepTimer(clock=clock)
    fired = []
    timer.expired.connect(lambda: fired.append(True))
    timer.start(30)
    assert timer.active and timer.label() == "30 dk"
    clock.now += 10 * 60
    assert timer.remaining_minutes() == 20
    timer.extend(15)
    assert timer.remaining_minutes() == 35
    timer._fire()  # too early (clock jump): re-arms instead of stopping
    assert timer.active and fired == []
    clock.now += 35 * 60
    timer._fire()
    assert fired == [True] and not timer.active
    timer.start(5)
    timer.cancel()
    assert not timer.active and timer.label() == ""


def test_countdown_ticks_then_delivers(qt_app):
    countdown = Countdown(seconds=3)
    ticks, due = [], []
    countdown.tick.connect(ticks.append)
    countdown.due.connect(due.append)
    countdown.start("next")
    countdown._step()
    countdown._step()
    assert ticks == [3, 2, 1] and due == []
    countdown._step()
    assert due == ["next"] and not countdown.running


def test_number_entry_joins_digits(qt_app):
    entry = NumberEntry()
    typed, chosen = [], []
    entry.typing.connect(typed.append)
    entry.chosen.connect(chosen.append)
    for digit in (1, 2):
        entry.digit(digit)
    entry.commit()
    assert typed == ["1", "12"] and chosen == [12]
    entry.digit(0)
    entry.commit()
    assert chosen == [12]  # zero is no channel


def test_next_episode_follows_catalogue_order():
    episodes = [
        Channel(f"s:e{i}", f"Bölüm {i}", f"file:///{i}", kind="movie", series_id="7")
        for i in range(1, 4)
    ]
    other = Channel("s:x", "Başka", "file:///x", kind="movie", series_id="8")
    channels = [episodes[0], other, episodes[1], episodes[2]]
    assert next_episode(channels, episodes[0]) is episodes[1]
    assert next_episode(channels, episodes[2]) is None
    assert next_episode(channels, other) is None
    assert next_episode(channels, Channel("s:m", "Film", "file:///m", kind="movie")) is None


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels(
        "home",
        [
            Channel("one", "Bir", "file:///1.ts"),
            Channel("two", "İki", "file:///2.ts"),
            Channel("e1", "Bölüm 1", "file:///e1.mkv", kind="movie", series_id="9"),
            Channel("e2", "Bölüm 2", "file:///e2.mkv", kind="movie", series_id="9"),
        ],
    )
    value = MainWindow(store)
    played = []
    monkeypatch.setattr(value.player, "load", lambda url, *a, **kw: played.append(url))
    value.played = played
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def episode(window, name):
    return next(c for c in window.model.channels if c.name == name)


def finish(window, reason="eof"):
    window._playback_active = True
    window.playback_finished(window._playback_token, reason, "")


def test_finished_episode_counts_down_to_the_next(window):
    window.request_play(episode(window, "Bölüm 1"))
    finish(window)
    assert window.next_countdown.running
    assert "Bölüm 2" in window.watch_notice.text.text()
    window.next_countdown.finish_now()
    assert window.played[-1] == "file:///e2.mkv"
    assert window.watch_notice.isHidden()


def test_stopping_or_cancelling_does_not_start_the_next_episode(window):
    window.request_play(episode(window, "Bölüm 1"))
    finish(window, "stop")
    assert not window.next_countdown.running
    finish(window)
    window.cancel_next_episode()
    assert not window.next_countdown.running and window.played == ["file:///e1.mkv"]


def test_autoplay_off_only_offers_the_next_episode(window):
    window.store.set_setting("autoplay_next", "off")
    window.request_play(episode(window, "Bölüm 1"))
    finish(window)
    assert not window.next_countdown.running
    assert window.watch_notice.primary.text() == "Oynat"
    window.watch_notice.primary.click()
    assert window.played[-1] == "file:///e2.mkv"


def test_sleep_until_the_end_stops_before_the_next_episode(window):
    window.request_play(episode(window, "Bölüm 1"))
    window.sleep_timer.start_until(window.sleep_timer._clock() + 600)
    finish(window)
    assert not window.next_countdown.running and not window.sleep_timer.active


def test_sleep_timer_stops_playback_and_shows_time_left(window):
    window.request_play(episode(window, "Bir"))
    window._playback_active = True
    labels = [a.text() for a in window.build_sleep_menu().actions()]
    assert "30 dakika sonra" in labels
    window.start_sleep(30)
    assert window.sleep_label.text() == "30 dk" and not window.sleep_label.isHidden()
    window.sleep_expired()
    assert not window._playback_active
    assert "İyi geceler" in window.message.text()


def test_typing_a_number_opens_that_channel(window):
    for digit in "2":
        window.number_entry.digit(int(digit))
    assert window.watch_notice.text.text() == "Kanal 2_"
    window.number_entry.commit()
    assert window.played == ["file:///2.ts"]
    assert window.watch_notice.text.text() == "2 · İki"
    assert not window.jump_to_number(9)
    assert "yok" in window.watch_notice.text.text()
