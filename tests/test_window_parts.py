"""The window facade remains patchable after methods move to concern mixins."""

from types import SimpleNamespace

import pytest

from luna_iptv import window as window_module
from luna_iptv.models import Channel, Playlist
from luna_iptv.window import MainWindow


def test_source_loader_patch_is_resolved_when_deferred_task_runs(monkeypatch):
    jobs = []
    window = SimpleNamespace(
        _busy=False,
        _importing=False,
        _auto_refresh_available=lambda source: True,
        run_task=lambda function, *args, **kwargs: jobs.append(function),
    )
    source = {"id": "test", "type": "m3u", "location": "https://example.test/list.m3u"}
    assert MainWindow.import_source(window, source, quiet=True)
    assert window._importing

    def load(location):
        assert location == "https://example.test/list.m3u"
        return Playlist([Channel("one", "Bir", "https://example.test/one")], [], [])

    monkeypatch.setattr(window_module, "load_m3u", load)
    assert [channel.name for channel in jobs[0]().channels] == ["Bir"]
    monkeypatch.setattr(window_module, "load_m3u", lambda _: Playlist([], [], []))
    assert jobs[0]().channels == []


@pytest.mark.parametrize("use_tv_parent", [False, True])
def test_pin_patch_is_resolved_on_each_call(monkeypatch, use_tv_parent):
    store = object()
    tv_parent = object() if use_tv_parent else None
    window = SimpleNamespace(store=store, tv_mode=tv_parent)
    calls = []

    def deny(store_arg, reason, parent):
        calls.append((store_arg, reason, parent))
        return False

    monkeypatch.setattr(window_module, "ask_pin", deny)
    assert not MainWindow.guard(window, "Onay")
    assert calls == [(store, "Onay", tv_parent or window)]
    monkeypatch.setattr(window_module, "ask_pin", lambda *args: True)
    assert MainWindow.guard(window, "Onay")


def test_guide_task_uses_patched_fetch_and_parser_at_execution_time(tmp_path, monkeypatch):
    jobs = []
    window = SimpleNamespace(
        store=SimpleNamespace(path=tmp_path / "library.sqlite3"),
        run_task=lambda function, *args, **kwargs: jobs.append(function),
    )
    MainWindow.load_guide(window, {"id": "test", "epg_url": "https://example.test/guide.xml"})
    payload = b'<tv><channel id="one"/></tv>'
    monkeypatch.setattr(window_module, "fetch", lambda _: payload)
    monkeypatch.setattr(window_module, "parse_xmltv", lambda raw: [raw.decode()])
    assert jobs[0]() == (payload, ['<tv><channel id="one"/></tv>'])


def test_next_episode_patch_preserves_deferred_play_action(monkeypatch):
    following = Channel("two", "İkinci bölüm", "https://example.test/two", kind="movie")
    notices, played = [], []
    window = SimpleNamespace(
        model=SimpleNamespace(channels=[]),
        current=None,
        store=SimpleNamespace(setting=lambda key, default: "off"),
        _notice=lambda *args: notices.append(args),
        play_next_episode=played.append,
    )
    monkeypatch.setattr(window_module, "next_episode", lambda channels, current: following)
    assert MainWindow.offer_next_episode(window)
    assert notices[0][0:2] == ("next", "Sonraki bölüm: İkinci bölüm")
    assert played == []
    notices[0][2][1]()
    assert played == [following]


def test_recording_playback_uses_patched_channel_constructor(tmp_path, monkeypatch):
    path = tmp_path / "recording.ts"
    path.touch()
    played = []
    window = SimpleNamespace(
        _recording_allowed=lambda item, prompt: True,
        request_play=lambda channel, **kwargs: played.append((channel, kwargs)),
    )
    monkeypatch.setattr(window_module, "Channel", lambda *args, **kwargs: (args, kwargs))
    MainWindow.play_recording(
        window, {"id": 8, "title": "Haber", "path": str(path), "channel_id": "a:one"}
    )
    assert played == [
        (
            (
                ("recording:8", "Haber", path.as_uri()),
                {"kind": "movie", "group": "Kayıtlar", "parental_id": "a:one"},
            ),
            {"approved": True},
        )
    ]


def test_static_source_comparison_remains_callable_without_window():
    source = {"id": "one", "name": "Kaynak", "type": "m3u", "location": "/list.m3u"}
    assert MainWindow._same_source(source, dict(source))
    assert not MainWindow._same_source(source, dict(source, location="/other.m3u"))


def test_session_check_uses_patched_application(monkeypatch):
    app = SimpleNamespace(isSavingSession=lambda: True)
    monkeypatch.setattr(window_module, "QGuiApplication", SimpleNamespace(instance=lambda: app))
    assert MainWindow._session_ending()
    app.isSavingSession = lambda: False
    assert not MainWindow._session_ending()


def test_mixin_can_be_imported_before_window_in_fresh_interpreter():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from luna_iptv.window_parts.sources import SourcesMixin"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
