from __future__ import annotations

import pytest

import luna_iptv.recovery as recovery_module
from luna_iptv.recovery import RecoveryController


class FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def disconnect(self, callback) -> None:
        if self.callback == callback:
            self.callback = None


class FakeTimer:
    def __init__(self, _parent=None) -> None:
        self.timeout = FakeSignal()
        self.single_shot = False
        self.interval = 0
        self.active = False

    def setSingleShot(self, enabled: bool) -> None:
        self.single_shot = enabled

    def start(self, interval: int | None = None) -> None:
        if interval is not None:
            self.interval = interval
        self.active = True

    def stop(self) -> None:
        self.active = False

    def fire(self) -> None:
        callback = self.timeout.callback
        if callback is None:
            return
        if self.single_shot:
            self.active = False
        callback()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def recovery(monkeypatch):
    monkeypatch.setattr(recovery_module, "QTimer", FakeTimer)
    clock = FakeClock()
    monkeypatch.setattr(recovery_module, "monotonic", clock, raising=False)
    controller = RecoveryController()
    retries = []
    controller.retry_requested.connect(retries.append)
    return controller, retries, clock


def begin_live(controller: RecoveryController, token: int = 10) -> None:
    controller.begin("source:channel", live=True)
    controller.watch(token)


def test_live_failures_retry_three_times_with_one_two_four_second_delays(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)

    for token, delay, attempt in ((10, 1000, 1), (11, 2000, 2), (12, 4000, 3)):
        assert controller.failure(token, "error") is True
        assert controller.state == "waiting"
        assert controller.attempt == attempt
        assert controller._timer.interval == delay
        controller._timer.fire()
        assert retries[-1] == "source:channel"
        assert controller.state == "connecting"
        controller.watch(token + 1)

    assert controller.failure(13, "error") is True
    assert controller.state == "failed"
    assert controller._timer.active is False
    assert retries == ["source:channel"] * 3


def test_connect_and_buffer_watchdogs_are_bounded(recovery) -> None:
    controller, _retries, _clock = recovery
    begin_live(controller)
    assert controller._timer.interval == 12_000

    controller._timer.fire()
    assert controller.state == "waiting"
    assert controller._timer.interval == 1000

    controller.cancel()
    begin_live(controller, token=20)
    controller.loaded(20)
    assert controller.buffering(20, True) is True
    assert controller.state == "buffering"
    assert controller._timer.interval == 15_000
    controller._timer.fire()
    assert controller.state == "waiting"


def test_stable_progress_resets_retry_budget(recovery) -> None:
    controller, _retries, clock = recovery
    begin_live(controller)
    controller.failure(10, "error")
    controller._timer.fire()
    controller.watch(11)
    controller.loaded(11)

    controller.progress(11, 1.0)
    assert controller._timer.active is False
    controller.progress(11, 1.5)
    assert controller._timer.interval == 15_000
    clock.advance(14.0)
    controller.progress(11, 15.5)
    clock.advance(1.0)
    controller._timer.fire()
    assert controller.attempt == 0

    controller.failure(11, "error")
    assert controller.attempt == 1
    assert controller._timer.interval == 1000


def test_stalled_playback_does_not_reset_retry_budget(recovery) -> None:
    controller, _retries, clock = recovery
    begin_live(controller)
    controller.failure(10, "error")
    controller._timer.fire()
    controller.watch(11)
    controller.loaded(11)

    controller.progress(11, 1.0)
    controller.progress(11, 1.5)
    clock.advance(15.0)
    controller._timer.fire()

    assert controller.attempt == 1
    assert controller._timer.active is False


def test_pause_and_vod_eof_never_schedule_recovery(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)
    controller.loaded(10)
    controller.paused(10, True)

    assert controller.state == "paused"
    assert controller._timer.active is False
    controller.buffering(10, True)
    assert controller._timer.active is False
    controller.failure(10, "error")
    assert controller.state == "idle"
    assert controller._timer.active is False
    assert retries == []

    controller.begin("source:movie", live=False)
    controller.watch(20)
    controller.loaded(20)
    assert controller.failure(20, "eof") is True
    assert controller.state == "idle"
    assert controller._timer.active is False
    assert retries == []


def test_unpausing_while_still_buffering_keeps_buffer_watchdog(recovery) -> None:
    controller, _retries, _clock = recovery
    begin_live(controller)
    controller.loaded(10)
    controller.paused(10, True)
    controller.buffering(10, True)

    controller.paused(10, False)

    assert controller.state == "buffering"
    assert controller._timer.interval == 15_000
    assert controller._timer.active is True


def test_cancel_and_new_channel_discard_old_timers_and_events(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)
    controller.failure(10, "error")
    controller.cancel()
    controller._timer.fire()
    assert retries == []

    controller.begin("other:channel", live=True)
    controller.watch(20)
    assert controller.loaded(10) is False
    assert controller.failure(10, "eof") is False
    assert controller.state == "connecting"
    assert controller.current_token == 20


def test_old_timer_callback_cannot_expire_new_channel_watchdog(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)
    old_timeout = controller._timer.timeout.callback

    controller.cancel()
    controller.begin("other:channel", live=True)
    controller.watch(20)
    old_timeout()

    assert controller.state == "connecting"
    assert controller.attempt == 0
    assert controller._timer.interval == 12_000
    assert retries == []


def test_duplicate_failure_cannot_create_overlapping_retry(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)

    controller.failure(10, "error")
    controller.failure(10, "eof")
    assert controller.attempt == 1
    assert controller._timer.interval == 1000
    controller._timer.fire()
    assert retries == ["source:channel"]


def test_late_health_events_cannot_replace_a_scheduled_retry(recovery) -> None:
    controller, _retries, _clock = recovery
    begin_live(controller)
    controller.loaded(10)
    controller.failure(10, "error")
    retry_callback = controller._timer.timeout.callback

    controller.paused(10, True)
    controller.buffering(10, True)
    controller.paused(10, False)
    controller.progress(10, 4.0)

    assert controller.state == "waiting"
    assert controller.attempt == 1
    assert controller._timer.interval == 1000
    assert controller._timer.timeout.callback is retry_callback


def test_suppressed_recovery_bounds_untracked_load_without_retry(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)

    assert controller.suppress_retries(10) is True
    assert controller.state == "untracked-connecting"
    assert controller._timer.interval == 12_000
    controller._timer.fire()

    assert controller.state == "untracked-failed"
    assert controller.message == "Yayın başlatılamadı."
    assert controller.attempt == 0
    assert retries == []


def test_live_eof_recovers_but_stop_and_close_do_not(recovery) -> None:
    controller, retries, _clock = recovery
    begin_live(controller)
    controller.loaded(10)
    controller.failure(10, "eof")
    assert controller.state == "waiting"

    controller.cancel()
    begin_live(controller, token=20)
    controller.failure(20, "stop")
    assert controller.state == "idle"
    assert controller._timer.active is False

    begin_live(controller, token=30)
    controller.failure(30, "error")
    controller.close()
    controller._timer.fire()
    assert retries == []
    assert controller.can_cancel is False


def test_recovery_exposes_concise_cancellable_status(recovery) -> None:
    controller, _retries, _clock = recovery
    begin_live(controller)
    assert controller.can_cancel is True
    assert controller.message == "Canlı yayına bağlanılıyor…"

    controller.failure(10, "error")
    assert controller.can_cancel is True
    assert "1 sn sonra" in controller.message
    assert "1/3" in controller.message

    controller.cancel()
    assert controller.state == "idle"
    assert controller.message == ""
    assert controller.can_cancel is False
