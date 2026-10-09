"""Recording processes, deadlines and parental checks without network or ffmpeg."""

import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_guide_view import window as window

from luna_iptv.models import Channel, Programme
from luna_iptv.recordings import (
    NO_FFMPEG,
    RecordingService,
    recording_command,
    recording_filename,
)
from luna_iptv.storage import Store

NOW = 1_800_000_000


def programme(start=NOW + 120, seconds=600):
    moment = datetime.fromtimestamp(start, timezone.utc)
    return Programme("epg", "Gece / Ayı", moment, moment + timedelta(seconds=seconds), "")


class Process:
    def __init__(self):
        self.code = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1
        self.code = -9

    def wait(self, timeout=None):
        if self.code is None and timeout is not None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code


@pytest.fixture
def setup(qt_app, tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "s", "type": "m3u", "name": "Source"})
    channel = store.replace_channels(
        "s",
        [
            Channel(
                "c",
                "Kanal",
                "https://example.test/live",
                headers={"User-Agent": "Luna", "Referer": "https://example.test/"},
            )
        ],
    )[0]
    store.set_setting("recording_folder", str(tmp_path / "output"))
    state = SimpleNamespace(now=NOW, locked=False, kids=False, approve=False, present=True)
    calls, processes, approvals = [], [], []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        process = Process()
        processes.append(process)
        return process

    services = []

    def create():
        service = RecordingService(
            store,
            runner=runner,
            clock=lambda: state.now,
            find_ffmpeg=lambda: "/fake/ffmpeg" if state.present else None,
            is_locked=lambda c: state.locked,
            kids=lambda: state.kids,
            authorize=lambda c: approvals.append(c.id) or state.approve,
        )
        services.append(service)
        return service

    value = SimpleNamespace(
        store=store,
        channel=channel,
        state=state,
        calls=calls,
        processes=processes,
        create=create,
        approvals=approvals,
    )
    value.service = create()
    yield value
    for service in services:
        service.close()
    store.close()


def row(setup):
    return setup.store.recordings()[0]


def test_command_headers_duration_and_sanitized_filename(setup):
    path = recording_filename("../Kanal:*", "Program/../<Gece>\x00", NOW)
    assert path.endswith(".ts") and "/" not in path and "\x00" not in path
    assert len(recording_filename("ş" * 1000, "🌙" * 1000, NOW).encode()) < 255
    args = recording_command("/fake/ffmpeg", setup.channel, 61.1, path)
    assert args[args.index("-user_agent") + 1] == "Luna"
    assert args[args.index("-headers") + 1] == "Referer: https://example.test/\r\n"
    assert args[args.index("-t") + 1] == "62"
    assert args[args.index("-c") + 1] == "copy"
    assert args.index("-headers") < args.index("-i")
    assert "-n" in args
    setup.channel.headers["Bad"] = "injected\r\nHeader: value"
    with pytest.raises(ValueError, match="başlık"):
        recording_command("ffmpeg", setup.channel, 10, path)


def test_schedule_arms_early_and_finishes_with_padding(setup):
    service = setup.service
    identity = service.add(setup.channel, programme())
    assert service._timer.isActive()
    assert service._timer.interval() == 60_000
    assert row(setup)["status"] == "scheduled"
    assert not setup.calls
    setup.state.now += 60
    service.tick()
    args, kwargs = setup.calls[0]
    assert row(setup)["status"] == "running"
    assert args[args.index("-t") + 1] == str(60 + 600 + 180)
    assert kwargs["stderr"] == kwargs["stdout"] == subprocess.DEVNULL
    assert "shell" not in kwargs
    assert service.add(setup.channel, programme()) == identity
    assert len(setup.calls) == 1
    setup.processes[0].code = 0
    service.tick()
    assert row(setup)["status"] == "finished"
    assert not service._timer.isActive()


def test_current_programme_records_remaining_time_only(setup):
    setup.service.add(setup.channel, programme(NOW - 120, 600))
    args = setup.calls[0][0]
    assert args[args.index("-t") + 1] == str(480 + 180)


def test_reopen_rearms_future_marks_missed_and_interrupted(setup):
    setup.service.add(setup.channel, programme(NOW + 5000))
    setup.store.add_recording(setup.channel, "Missed", NOW - 600, NOW - 300, 3)
    interrupted = setup.store.add_recording(setup.channel, "Interrupted", NOW - 1, NOW + 300, 3)
    setup.store.update_recording(interrupted, "running")
    setup.service.close()
    service = setup.create()
    assert service._timer.isActive()
    assert {r["status"] for r in setup.store.recordings()} == {"scheduled", "missed", "interrupted"}
    assert not setup.calls


def test_cancel_and_stop_reap_without_touching_other_recordings(setup):
    scheduled = setup.service.add(setup.channel, programme())
    setup.service.cancel(scheduled)
    assert row(setup)["status"] == "cancelled"
    running = setup.service.add(setup.channel, programme(NOW - 5))
    process = setup.processes[0]
    setup.service.cancel(running)
    assert process.terminated == 1
    setup.state.now += 4
    setup.service.tick()
    assert process.killed == 1
    setup.service.tick()
    assert next(r for r in setup.store.recordings() if r["id"] == running)["status"] == "stopped"


def test_kids_cannot_record_locked_even_with_pin(setup):
    setup.state.locked = setup.state.kids = setup.state.approve = True
    with pytest.raises(ValueError, match="kapalı"):
        setup.service.add(setup.channel, programme())
    assert not setup.approvals and not setup.store.recordings()


def test_locked_adult_requires_pin_and_keeps_authorization_across_restart(setup):
    setup.state.locked = True
    assert setup.service.add(setup.channel, programme()) is None
    assert setup.approvals == [setup.channel.id]
    setup.state.approve = True
    setup.service.add(setup.channel, programme())
    setup.service.close()
    setup.state.now += 60
    service = setup.create()
    service.tick()
    assert row(setup)["status"] == "running"


def test_new_lock_or_kids_switch_prevents_scheduled_recording(setup):
    setup.service.add(setup.channel, programme())
    setup.state.locked = True
    setup.state.now += 60
    setup.service.tick()
    assert row(setup)["status"] == "cancelled"
    assert not setup.calls


def test_running_locked_recording_stops_on_kids_switch(setup):
    setup.state.locked = setup.state.approve = True
    setup.service.add(setup.channel, programme(NOW - 5))
    setup.state.kids = True
    setup.service.tick()
    assert setup.processes[0].terminated == 1


def test_missing_ffmpeg_and_start_failure_are_sanitized(setup):
    setup.state.present = False
    with pytest.raises(ValueError, match="ffmpeg") as error:
        setup.service.add(setup.channel, programme())
    assert str(error.value) == NO_FFMPEG
    setup.state.present = True
    setup.service.add(setup.channel, programme())
    setup.state.present = False
    setup.state.now += 60
    setup.service.tick()
    assert row(setup)["status"] == "failed"
    assert row(setup)["message"] == NO_FFMPEG


def test_process_start_error_does_not_expose_url(setup):
    def failing(*args, **kwargs):
        raise OSError("secret provider URL")

    setup.service._runner = failing
    setup.service.add(setup.channel, programme(NOW - 5))
    assert row(setup)["status"] == "failed"
    assert "secret" not in row(setup)["message"]


def test_sleep_past_program_marks_missed_without_process(setup):
    setup.service.add(setup.channel, programme())
    setup.state.now += 900
    setup.service.tick()
    assert row(setup)["status"] == "missed"
    assert not setup.calls


def test_shutdown_reaps_process_but_preserves_future_schedule(setup):
    setup.service.add(setup.channel, programme(NOW - 5))
    setup.service.add(setup.channel, programme())
    setup.service.close()
    assert setup.processes[0].killed == 1
    assert {r["status"] for r in setup.store.recordings()} == {"interrupted", "scheduled"}


def test_recordings_dialog_plays_local_file_and_checks_original_channel(
    window, tmp_path, monkeypatch
):
    from luna_iptv.recordings_dialog import RecordingsDialog

    channel = window.store.channels()[0]
    identity = window.store.add_recording(channel, "Gece Ayı", NOW, NOW + 60, 3)
    path = tmp_path / "recording.ts"
    path.write_bytes(b"synthetic")
    window.store.update_recording(identity, "finished", path=str(path))
    played, checked = [], []
    monkeypatch.setattr(window, "request_play", lambda c, **kw: played.append(c))
    monkeypatch.setattr(window, "unlock_channel", lambda c: checked.append(c.id) or True)
    dialog = RecordingsDialog(
        window.recording_service, window.play_recording, window._recording_allowed
    )
    dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
    assert dialog.play_button.isEnabled()
    dialog.play_button.click()
    assert checked == [channel.id]
    assert played[0].url == path.as_uri()
    assert played[0].kind == "movie" and played[0].parental_id == channel.id
    monkeypatch.setattr(window, "kids_profile", lambda: True)
    window.model.set_locked({channel.id})
    dialog.refresh()
    assert dialog.list.topLevelItemCount() == 0
    dialog.close()


def test_local_recording_playback_stays_transient_and_seekable(window, tmp_path, monkeypatch):
    channel = window.store.channels()[0]
    path = tmp_path / "recording.ts"
    path.write_bytes(b"synthetic")
    item = {"id": 5, "channel_id": channel.id, "title": "Kayıt", "path": str(path)}
    window.play_recording(item)
    window._mark_loaded()
    window.player_property("seekable", True)
    window.player_property("duration", 600)
    assert window.current.kind == "movie"
    assert window.seek.isEnabled()
    assert not window.favorite_button.isEnabled()
    assert not window.recovery._live
    assert window.store.progress(window.current.id) == (0, 0)
    window.refresh_library()
    assert window._current_persistent


def test_restarted_store_retains_global_schedule(qt_app, tmp_path):
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    store.save_source({"id": "s", "type": "m3u", "name": "Source"})
    channel = store.replace_channels("s", [Channel("c", "Kanal", "https://example.test/live")])[0]
    identity = store.add_recording(channel, "Program", NOW + 120, NOW + 600, 3)
    store.close()
    store = Store(path)
    service = RecordingService(store, clock=lambda: NOW, find_ffmpeg=lambda: None)
    assert store.recordings()[0]["id"] == identity
    assert service._timer.isActive()
    service.close()
    store.close()


def test_default_policy_reads_store_locks_and_kids_profile(setup):
    setup.store.set_pin_hash("synthetic-hash")
    setup.store.set_channel_locked(setup.channel.id, True)
    service = RecordingService(setup.store, clock=lambda: NOW, find_ffmpeg=lambda: "/fake/ffmpeg")
    try:
        assert service.add(setup.channel, programme()) is None
        assert not setup.store.recordings()
    finally:
        service.close()


def test_no_ffmpeg_current_ui_does_not_open_duration_prompt(window, monkeypatch):
    notices = []
    monkeypatch.setattr(window.recording_service, "_find_ffmpeg", lambda: None)
    monkeypatch.setattr(window, "status", lambda text, *a, **kw: notices.append(text))
    window.record_current()
    assert notices == [NO_FFMPEG]
