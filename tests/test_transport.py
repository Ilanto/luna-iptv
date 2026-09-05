from __future__ import annotations

import math
from concurrent.futures import Future

import pytest

import luna_iptv.transport as transport_module
from luna_iptv.transport import TransportController


class FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def emit(self) -> None:
        if self.callback is not None:
            self.callback()


class FakeTimer:
    def __init__(self, _parent=None) -> None:
        self.timeout = FakeSignal()
        self.interval = None
        self.active = False

    def setInterval(self, interval: int) -> None:
        self.interval = interval

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def fire(self) -> None:
        assert self.timeout.callback is not None
        self.timeout.callback()


class FakeFuture:
    def __init__(self) -> None:
        self._callbacks = []
        self._exception = None

    def add_done_callback(self, callback) -> None:
        self._callbacks.append(callback)

    def complete(self, exception=None) -> None:
        self._exception = exception
        for callback in self._callbacks:
            callback(self)

    def exception(self):
        return self._exception


class FakePlayer:
    def __init__(self) -> None:
        self.commands: list[list[object]] = []
        self.properties: list[tuple[str, object]] = []
        self.property_futures: list[FakeFuture] = []
        self.next_property_future = None

    def command(self, args: list[object]) -> None:
        self.commands.append(args)

    def set_property(self, name: str, value: object) -> FakeFuture:
        self.properties.append((name, value))
        future = self.next_property_future or FakeFuture()
        self.next_property_future = None
        self.property_futures.append(future)
        return future


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(transport_module, "QTimer", FakeTimer)
    clock = Clock()
    monkeypatch.setattr(transport_module.time, "monotonic", clock)
    player = FakePlayer()
    controller = TransportController(player)
    return controller, player, clock


def make_vod(controller: TransportController, *, paused: bool = False) -> None:
    controller.prepare(live=False)
    controller.observe("duration", 100.0)
    controller.observe("time-pos", 10.0)
    controller.observe("seekable", True)
    controller.observe("pause", paused)
    controller.loaded()


def test_capabilities_distinguish_seekable_vod_partial_cache_and_live(setup) -> None:
    controller, _player, _clock = setup

    assert (controller.can_seek, controller.can_scan, controller.rate, controller.label) == (
        False,
        False,
        0,
        "1×",
    )

    make_vod(controller)
    assert controller.can_seek is True
    assert controller.can_scan is True

    controller.observe("partially-seekable", True)
    assert controller.can_seek is True
    assert controller.can_scan is False

    controller.prepare(live=True)
    controller.observe("duration", 100.0)
    controller.observe("seekable", True)
    controller.loaded()
    assert controller.can_seek is True
    assert controller.can_scan is False

    controller.prepare(live=False)
    controller.observe("seekable", True)
    controller.loaded()
    assert controller.can_seek is True
    assert controller.can_scan is False


def test_changed_is_not_emitted_for_each_position_update(setup) -> None:
    controller, _player, _clock = setup
    changes: list[tuple[bool, bool, int]] = []
    controller.changed.connect(
        lambda: changes.append((controller.can_seek, controller.can_scan, controller.rate))
    )
    make_vod(controller)
    baseline = len(changes)

    controller.observe("time-pos", 20.0)
    controller.observe("time-pos", 21.0)

    assert len(changes) == baseline
    controller.observe("seekable", False)
    assert changes[-1] == (False, False, 0)


def test_five_second_seek_is_exact_and_disabled_when_not_seekable(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller)

    assert controller.seek_relative(-5) is True
    assert controller.seek_relative(5) is True
    assert player.commands == [
        ["seek", -5.0, "relative+exact"],
        ["seek", 5.0, "relative+exact"],
    ]

    controller.observe("seekable", False)
    assert controller.seek_relative(5) is False
    assert controller.seek_relative(math.nan) is False
    assert len(player.commands) == 2


def test_scan_waits_for_pause_command_completion_without_property_notification(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=False)

    assert controller.cycle(1) == 2
    assert controller.label == "+2×"
    assert player.properties == [("pause", True)]
    assert controller._timer.interval == 500
    assert controller._timer.active is True

    clock.now = 100.5
    controller._timer.fire()
    assert player.commands == []

    player.property_futures[-1].complete()
    controller._timer.fire()
    assert player.commands == [["seek", 11.0, "absolute+keyframes"]]

    controller.observe("seeking", True)
    clock.now = 101.0
    controller._timer.fire()
    assert len(player.commands) == 1

    controller.observe("seeking", False)
    controller._timer.fire()
    assert player.commands[-1] == ["seek", 12.0, "absolute+keyframes"]


def test_rate_changes_reanchor_to_current_position_and_opposite_starts_at_two(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=True)

    assert controller.cycle(1) == 2
    player.property_futures[-1].complete()
    controller.observe("time-pos", 12.0)
    clock.now = 101.0
    assert controller.cycle(1) == 4

    clock.now = 101.5
    controller._timer.fire()
    assert player.commands[-1] == ["seek", 14.0, "absolute+keyframes"]

    controller.observe("time-pos", 14.0)
    clock.now = 102.0
    assert controller.cycle(-1) == -2
    assert controller.label == "-2×"

    clock.now = 102.5
    controller._timer.fire()
    assert player.commands[-1] == ["seek", 13.0, "absolute+keyframes"]


def test_cycle_after_sixteen_returns_to_normal_and_restores_previous_pause(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=False)

    controller.cycle(1)
    controller.observe("pause", True)
    assert [controller.cycle(1) for _ in range(3)] == [4, 8, 16]
    assert controller.cycle(1) == 0

    assert controller.rate == 0
    assert controller.label == "1×"
    assert controller._timer.active is False
    assert player.properties == [("pause", True), ("pause", False)]


@pytest.mark.parametrize(
    ("direction", "start", "elapsed", "expected"),
    [(1, 95.0, 3.0, 100.0), (-1, 3.0, 2.0, 0.0)],
)
def test_scan_clamps_at_media_edges_and_stops(
    setup, direction: int, start: float, elapsed: float, expected: float
) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=True)
    controller.observe("time-pos", start)
    controller.cycle(direction)
    player.property_futures[-1].complete()

    clock.now += elapsed
    controller._timer.fire()

    assert player.commands[-1] == ["seek", expected, "absolute+keyframes"]
    assert controller.rate == 0
    assert controller._timer.active is False


def test_manual_seek_cancels_scan_and_restores_entry_pause_first(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=False)
    controller.cycle(-1)
    controller.observe("pause", True)

    assert controller.seek_relative(-5) is True

    assert controller.rate == 0
    assert player.properties[-1] == ("pause", False)
    assert player.commands[-1] == ["seek", -5.0, "relative+exact"]


def test_prepare_finished_and_close_cancel_without_restoring_old_pause(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=False)
    controller.cycle(1)
    controller.observe("pause", True)
    player.properties.clear()

    controller.prepare(live=False)
    assert controller.rate == 0
    assert player.properties == []

    make_vod(controller, paused=False)
    controller.cycle(1)
    controller.observe("pause", True)
    player.properties.clear()
    controller.finished()
    assert player.properties == []
    assert controller.can_seek is False

    make_vod(controller, paused=False)
    controller.cycle(1)
    player.properties.clear()
    controller.close()
    assert player.properties == []
    assert controller.rate == 0


def test_normal_play_always_leaves_scan_at_one_x_and_unpauses(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=True)
    controller.cycle(-1)

    controller.normal_play()

    assert controller.rate == 0
    assert controller.label == "1×"
    assert player.properties[-1] == ("pause", False)


def test_scan_rejects_invalid_direction_and_non_scannable_sources(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller)

    with pytest.raises(ValueError):
        controller.cycle(0)

    controller.observe("partially-seekable", True)
    assert controller.cycle(1) == 0
    assert player.properties == []


def test_rapid_cancel_and_rescan_orders_a_new_pause_barrier(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=False)
    controller.cycle(1)
    controller.cancel(restore_pause=True)

    assert player.properties == [("pause", True), ("pause", False)]
    assert controller.cycle(1) == 2
    assert player.properties[-1] == ("pause", True)

    clock.now += 0.5
    controller._timer.fire()
    player.property_futures[0].complete()
    controller._timer.fire()
    player.property_futures[1].complete()
    controller._timer.fire()
    assert player.commands == []

    player.property_futures[2].complete()
    controller._timer.fire()
    assert player.commands == [["seek", 11.0, "absolute+keyframes"]]

    controller.cancel(restore_pause=True)
    assert player.properties[-1] == ("pause", False)


def test_normal_play_then_immediate_scan_preserves_logical_playing_state(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=True)
    controller.normal_play()

    assert player.properties == [("pause", False)]
    controller.cycle(-1)
    assert player.properties[-1] == ("pause", True)

    clock.now += 0.5
    controller._timer.fire()
    player.property_futures[0].complete()
    controller._timer.fire()
    assert player.commands == []

    player.property_futures[1].complete()
    controller._timer.fire()
    assert player.commands == [["seek", 9.0, "absolute+keyframes"]]

    controller.cancel(restore_pause=True)
    assert player.properties[-1] == ("pause", False)


def test_cancel_while_waiting_for_pause_restores_playing_order(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=False)

    controller.cycle(1)
    controller.cancel(restore_pause=True)

    assert player.properties == [("pause", True), ("pause", False)]
    assert controller.rate == 0
    assert controller._timer.active is False


def test_normal_play_does_not_send_pause_to_an_idle_or_closed_player(setup) -> None:
    controller, player, _clock = setup

    controller.normal_play()
    controller.prepare(live=False)
    controller.normal_play()
    controller.close()
    controller.normal_play()

    assert player.properties == []


def test_rapid_normal_scan_seek_scan_waits_for_current_command_completion(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=False)
    assert [controller.cycle(1) for _ in range(4)] == [2, 4, 8, 16]

    assert controller.cycle(1) == 0
    assert controller.cycle(1) == 2
    assert controller.seek_relative(-5) is True
    assert controller.cycle(-1) == -2
    assert player.properties[-4:] == [
        ("pause", False),
        ("pause", True),
        ("pause", False),
        ("pause", True),
    ]

    clock.now += 0.5
    for stale_future in player.property_futures[:-1]:
        stale_future.complete()
        controller._timer.fire()
    assert not any(command[-1] == "absolute+keyframes" for command in player.commands)

    player.property_futures[-1].complete()
    controller._timer.fire()
    assert player.commands[-1] == ["seek", 9.0, "absolute+keyframes"]


@pytest.mark.parametrize("old_error", [None, RuntimeError("old command failed")])
def test_old_media_pause_completion_cannot_unlock_new_scan(setup, old_error) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=False)
    controller.cycle(1)
    old_future = player.property_futures[-1]

    controller.finished()
    make_vod(controller, paused=False)
    controller.cycle(-1)
    current_future = player.property_futures[-1]
    clock.now += 0.5

    old_future.complete(old_error)
    controller._timer.fire()
    assert player.commands == []

    current_future.complete()
    controller._timer.fire()
    assert player.commands == [["seek", 9.0, "absolute+keyframes"]]


def test_current_pause_command_failure_stops_scan(setup) -> None:
    controller, player, clock = setup
    make_vod(controller, paused=False)
    controller.cycle(1)

    player.property_futures[-1].complete(RuntimeError("pause unsupported"))
    clock.now += 0.5
    controller._timer.fire()

    assert controller.rate == 0
    assert controller._timer.active is False
    assert player.commands == []


def test_already_failed_pause_future_does_not_leave_idle_timer_running(setup) -> None:
    controller, player, _clock = setup
    make_vod(controller, paused=False)
    failed = Future()
    failed.set_exception(RuntimeError("pause unsupported"))
    player.next_property_future = failed

    assert controller.cycle(1) == 0

    assert controller._timer.active is False
    assert player.commands == []
