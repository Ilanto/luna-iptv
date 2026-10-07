"""Exercise the MPRIS service through independent D-Bus clients."""

import os
import shlex
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

import pytest
from PySide6.QtCore import Q_ARG, SLOT, QMetaObject, QObject, Qt, Slot
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

from luna_iptv.mpris import MprisService, _int64, _strings, _variant_map

PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = ROOT + ".Player"
PROPERTIES = "org.freedesktop.DBus.Properties"
TRACK = PATH + "/track/test"


class Controller:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))


def spin(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert predicate(), "Timed out waiting for D-Bus"


@pytest.fixture(scope="session")
def available_bus():
    # Check before qt_app: headless/sandbox runs must skip without loading Wayland.
    if not shutil.which("busctl"):
        pytest.skip("busctl is required to verify wire types")
    probe = subprocess.run(
        ["busctl", "--user", "--no-pager", "list"], capture_output=True, timeout=5
    )
    if probe.returncode:
        pytest.skip("No accessible session bus")


@pytest.fixture
def service(available_bus, qt_app):
    suffix = f"p{os.getpid()}_{uuid.uuid4().hex}"
    server_name, client_name = "mpris_server_" + suffix, "mpris_client_" + suffix
    server = QDBusConnection.connectToBus(QDBusConnection.SessionBus, server_name)
    client = QDBusConnection.connectToBus(QDBusConnection.SessionBus, client_name)
    controller = Controller()
    name = ROOT + ".luna_test." + suffix
    value = MprisService(controller, connection=server, service_name=name)
    try:
        assert server.isConnected() and client.isConnected()
        assert server.baseService() != client.baseService()
        assert value.active
        yield value, controller, server, client, name, qt_app
    finally:
        value.close()
        QDBusConnection.disconnectFromBus(client_name)
        QDBusConnection.disconnectFromBus(server_name)


def call(service, interface, method, *args):
    _, _, _, client, name, app = service
    message = QDBusMessage.createMethodCall(name, PATH, interface, method)
    message.setArguments(list(args))
    pending = client.asyncCall(message, 2000)
    spin(app, pending.isFinished)
    reply = pending.reply()
    assert reply.type() == QDBusMessage.ReplyMessage, reply.errorMessage()
    return reply.arguments()


def busctl(service, *args):
    _, _, _, _, name, app = service
    process = subprocess.Popen(
        ["busctl", "--user", "--no-pager", "--", *args[:1], name, PATH, *args[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        spin(app, lambda: process.poll() is not None)
        stdout, stderr = process.communicate(timeout=1)
        assert process.returncode == 0, stderr
        # busctl prints non-ASCII bytes as octal escapes (Sanat\303\247\304\261).
        return [unescape(token) for token in shlex.split(stdout)]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def unescape(token):
    return (
        token.encode("latin-1", "backslashreplace")
        .decode("unicode_escape")
        .encode("latin-1")
        .decode("utf-8")
    )


def wire_property(service, interface, name):
    tokens = busctl(service, "get-property", interface, name)
    if tokens[0] != "a{sv}":
        return tokens
    count = int(tokens[1])
    fields = {}
    index = 2
    for _ in range(count):
        key, signature = tokens[index : index + 2]
        index += 2
        if signature == "as":
            size = int(tokens[index])
            fields[key] = (signature, tokens[index + 1 : index + 1 + size])
            index += size + 1
        else:
            fields[key] = (signature, tokens[index])
            index += 1
    assert index == len(tokens)
    return fields


def test_registration_and_signatures(service):
    xml = call(service, "org.freedesktop.DBus.Introspectable", "Introspect")[0]
    node = ET.fromstring(xml)
    root = node.find(f"interface[@name='{ROOT}']")
    player = node.find(f"interface[@name='{PLAYER}']")
    assert root is not None and player is not None
    expected = {
        "PlaybackStatus": "s",
        "LoopStatus": "s",
        "Rate": "d",
        "Shuffle": "b",
        "Metadata": "a{sv}",
        "Volume": "d",
        "Position": "x",
        "MinimumRate": "d",
        "MaximumRate": "d",
        "CanGoNext": "b",
        "CanGoPrevious": "b",
        "CanPlay": "b",
        "CanPause": "b",
        "CanSeek": "b",
        "CanControl": "b",
    }
    assert {p.get("name"): p.get("type") for p in player.findall("property")} == expected
    for method, signature in {"Seek": "x", "SetPosition": "ox"}.items():
        args = player.findall(f"method[@name='{method}']/arg")
        assert "".join(a.get("type") for a in args if a.get("direction", "in") == "in") == signature
    assert player.find("signal[@name='Seeked']/arg").get("type") == "x"
    for prop in ("SupportedUriSchemes", "SupportedMimeTypes"):
        assert root.find(f"property[@name='{prop}']").get("type") == "as"


@pytest.mark.parametrize("length", [0, 123, 2**40, None])
def test_readback_and_wire_types(service, length):
    value = service[0]
    value.update(
        status="Playing",
        title="Luna",
        artist="Sanatçı",
        art_url="https://example.org/art.png",
        length_us=length,
        can_seek=True,
        can_go_next=True,
        track_id=TRACK,
    )
    value.set_position(123)
    assert call(service, PROPERTIES, "Get", PLAYER, "PlaybackStatus")[0].variant() == "Playing"
    assert call(service, PROPERTIES, "Get", PLAYER, "Position")[0].variant() == 123
    # Nested maps are checked from outside with busctl: PySide cannot read a{sv} back.
    assert wire_property(service, PLAYER, "Position") == ["x", "123"]
    data = wire_property(service, PLAYER, "Metadata")
    assert data["mpris:trackid"] == ("o", TRACK)
    assert data["xesam:title"] == ("s", "Luna")
    assert data["xesam:artist"] == ("as", ["Sanatçı"])
    assert data["mpris:artUrl"] == ("s", "https://example.org/art.png")
    if length is None:
        assert "mpris:length" not in data
    else:
        assert data["mpris:length"] == ("x", str(length))
    assert wire_property(service, ROOT, "Identity") == ["s", "Luna IPTV"]
    assert wire_property(service, ROOT, "DesktopEntry") == ["s", "luna-iptv"]
    for prop in ("SupportedUriSchemes", "SupportedMimeTypes"):
        assert wire_property(service, ROOT, prop) == ["as", "0"]


def test_controller_dispatch(service):
    value, controller, *_ = service
    value.update(
        status="Playing", track_id=TRACK, can_seek=True, can_go_next=True, can_go_previous=True
    )
    for method in ("PlayPause", "Play", "Pause", "Stop", "Next", "Previous"):
        call(service, PLAYER, method)
    busctl(service, "call", PLAYER, "Seek", "x", "-5000000000")
    busctl(service, "call", PLAYER, "SetPosition", "ox", TRACK, "5000000000")
    busctl(service, "call", PLAYER, "SetPosition", "ox", TRACK + "_old", "42")
    call(service, ROOT, "Raise")
    assert controller.calls == [
        ("play_pause", ()),
        ("play", ()),
        ("pause", ()),
        ("stop", ()),
        ("next", ()),
        ("previous", ()),
        ("seek", (-5000000000,)),
        ("set_position", (5000000000,)),
        ("raise_window", ()),
    ]


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.changes = []
        self.positions = []

    @Slot(QDBusMessage)
    def changed(self, message):
        self.changes.append(message)

    @Slot("qlonglong")
    def seeked(self, position):
        self.positions.append(position)


def test_signals_only_for_changes(service):
    value, _, _, client, name, app = service
    receiver = Receiver()
    assert client.connect(
        name, PATH, PROPERTIES, "PropertiesChanged", receiver, SLOT("changed(QDBusMessage)")
    )
    assert client.connect(name, PATH, PLAYER, "Seeked", receiver, SLOT("seeked(qlonglong)"))
    # A round trip ensures signal subscriptions have reached the bus.
    call(service, PROPERTIES, "Get", PLAYER, "PlaybackStatus")
    value.update(status="Playing", title="Luna", length_us=123, track_id=TRACK)
    spin(app, lambda: len(receiver.changes) == 1)
    assert receiver.changes[0].signature() == "sa{sv}as"
    for _ in range(3):
        value.update(status="Playing", title="Luna", length_us=123, track_id=TRACK)
    value.set_position(7)
    value.seeked(2**40)
    spin(app, lambda: receiver.positions == [2**40])
    assert wire_property(service, PLAYER, "Position") == ["x", str(2**40)]
    value.update(status="Paused", title="Luna", length_us=123, track_id=TRACK)
    spin(app, lambda: len(receiver.changes) == 2)
    call(service, PROPERTIES, "Get", PLAYER, "Position")
    assert len(receiver.changes) == 2

    assert receiver.changes[-1].arguments()[0] == PLAYER
    assert wire_property(service, PLAYER, "PlaybackStatus") == ["s", "Paused"]


def test_registration_conflicts_preserve_owner(service):
    value, controller, server, _, name, _ = service
    same_connection = MprisService(controller, connection=server, service_name=name)
    assert not same_connection.active
    same_connection.close()
    assert value.active
    connection_name = "conflict_" + uuid.uuid4().hex
    other = QDBusConnection.connectToBus(QDBusConnection.SessionBus, connection_name)
    try:
        conflicting = MprisService(controller, connection=other, service_name=name)
        assert not conflicting.active
        conflicting.close()
        assert value.active
        call(service, ROOT, "Raise")
        # Name failure must also release the object on the losing connection.
        replacement = MprisService(controller, connection=other, service_name=name + "_other")
        try:
            assert replacement.active
        finally:
            replacement.close()
    finally:
        QDBusConnection.disconnectFromBus(connection_name)


def test_close_releases_name_and_is_idempotent(service):
    value, controller, server, client, name, _ = service
    value.close()
    value.close()
    assert not value.active
    assert not client.interface().isServiceRegistered(name).value()
    replacement = MprisService(controller, connection=server, service_name=name)
    try:
        assert replacement.active
    finally:
        replacement.close()


def test_unavailable_connection_is_quiet(capfd):
    value = MprisService(Controller(), connection=QDBusConnection("missing_" + uuid.uuid4().hex))
    assert not value.active
    value.update(status="Playing", title="Luna", length_us=1)
    value.set_position(1)
    value.seeked(2)
    value.close()
    value.close()
    assert not capfd.readouterr().err


@pytest.mark.parametrize("value", [0, 1, -1, 2**31 - 1, 2**31, -(2**63), 2**63 - 1])
def test_int64_marshalling_does_not_narrow(value):
    assert _int64(value).currentSignature() == "x"


@pytest.mark.parametrize("values", [[], ["Sanatçı"]])
def test_string_list_marshalling_is_typed_even_when_empty(values):
    assert _strings(values).currentSignature() == "as"


class RecordingConnection:
    """Exercise state publication without needing a desktop bus."""

    def __init__(self, object_available=True, name_available=True):
        self.object_available = object_available
        self.name_available = name_available
        self.messages = []
        self.released = []

    def isConnected(self):
        return True

    def registerObject(self, *args):
        return self.object_available

    def registerService(self, name):
        return self.name_available

    def unregisterObject(self, path):
        self.released.append(("object", path))

    def unregisterService(self, name):
        self.released.append(("name", name))

    def send(self, message):
        self.messages.append(message)
        return True


def player_adaptor(value):
    return next(
        adaptor
        for adaptor in value.findChildren(QDBusAbstractAdaptor)
        if adaptor.metaObject().indexOfProperty("PlaybackStatus") >= 0
    )


def test_cached_state_and_change_suppression_without_bus():
    connection = RecordingConnection()
    value = MprisService(Controller(), connection=connection)
    try:
        value.update(status="Stopped")
        assert not connection.messages
        value.update(status="Playing", title="Luna", length_us=123, track_id=TRACK)
        value.update(status="Playing", title="Luna", length_us=123, track_id=TRACK)
        assert len(connection.messages) == 1
        args = connection.messages[0].arguments()
        assert args[0] == PLAYER
        assert args[1].currentSignature() == "a{sv}"
        assert args[2].currentSignature() == "as"
        player = player_adaptor(value)
        assert player.property("PlaybackStatus") == "Playing"
        metadata = player.property("Metadata")
        assert isinstance(metadata["mpris:trackid"], QDBusObjectPath)
        assert metadata["mpris:trackid"].path() == TRACK
        assert metadata["mpris:length"].currentSignature() == "x"
        assert metadata["xesam:artist"].currentSignature() == "as"
        value.set_position(2**40)
        assert player.property("Position") == 2**40
        assert len(connection.messages) == 1
        value.update(status="Paused", title="Luna", length_us=123, track_id=TRACK)
        assert len(connection.messages) == 2
        value.update(status="Paused", title="Luna", track_id=TRACK)
        assert len(connection.messages) == 3
        assert "mpris:length" not in player.property("Metadata")
    finally:
        value.close()


def test_nested_variant_map_marshalling():
    argument = _variant_map({"Metadata": {"mpris:length": _int64(1)}})
    assert argument.currentSignature() == "a{sv}"


def test_typed_controller_dispatch_without_bus():
    controller = Controller()
    connection = RecordingConnection()
    value = MprisService(controller, connection=connection)
    try:
        value.update(
            status="Playing", track_id=TRACK, can_seek=True, can_go_next=True, can_go_previous=True
        )
        player = player_adaptor(value)
        for method in ("PlayPause", "Play", "Pause", "Stop", "Next", "Previous"):
            assert QMetaObject.invokeMethod(player, method, Qt.DirectConnection)
        assert QMetaObject.invokeMethod(
            player, "Seek", Qt.DirectConnection, Q_ARG("qlonglong", -(2**40))
        )
        for track in (TRACK, TRACK + "_old"):
            assert QMetaObject.invokeMethod(
                player,
                "SetPosition",
                Qt.DirectConnection,
                Q_ARG(QDBusObjectPath, QDBusObjectPath(track)),
                Q_ARG("qlonglong", 2**40),
            )
        root = next(a for a in value.findChildren(QDBusAbstractAdaptor) if a is not player)
        assert QMetaObject.invokeMethod(root, "Raise", Qt.DirectConnection)
        assert controller.calls == [
            ("play_pause", ()),
            ("play", ()),
            ("pause", ()),
            ("stop", ()),
            ("next", ()),
            ("previous", ()),
            ("seek", (-(2**40),)),
            ("set_position", (2**40,)),
            ("raise_window", ()),
        ]
        value.update(status="Stopped")
        assert QMetaObject.invokeMethod(player, "Next", Qt.DirectConnection)
        assert QMetaObject.invokeMethod(player, "Previous", Qt.DirectConnection)
        assert QMetaObject.invokeMethod(player, "Seek", Qt.DirectConnection, Q_ARG("qlonglong", 1))
        assert len(controller.calls) == 9
    finally:
        value.close()
        value.close()
    assert connection.released == [("object", PATH), ("name", ROOT + ".luna_iptv")]


@pytest.mark.parametrize("object_available", [True, False])
def test_registration_failure_is_quiet_and_does_not_release_another_owner(object_available, capfd):
    connection = RecordingConnection(object_available=object_available, name_available=False)
    value = MprisService(Controller(), connection=connection)
    value.close()
    value.close()
    assert not value.active
    assert connection.released == ([("object", PATH)] if object_available else [])
    assert not capfd.readouterr().err
