"""Expose a playback controller to desktop media controls through Qt D-Bus."""

from __future__ import annotations

import ctypes
import re

from PySide6.QtCore import ClassInfo, Property, QMetaType, QObject, Signal, Slot
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusArgument,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
    QDBusVariant,
)
from shiboken6 import getCppPointer

_PATH = "/org/mpris/MediaPlayer2"
_ROOT = "org.mpris.MediaPlayer2"
_PLAYER = _ROOT + ".Player"
_NO_TRACK = _PATH + "/TrackList/NoTrack"
_OBJECT_PATH = re.compile(r"/(?:[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)?\Z")
_append_int64 = None


def _int64(value: int) -> QDBusArgument | None:
    """Marshal a signed 64-bit value, including values that fit in int32.

    Returns None when the Qt symbol cannot be reached; callers then leave the
    optional field out rather than publish it with the wrong type.
    """
    global _append_int64
    if not -(2**63) <= value < 2**63:
        raise OverflowError("MPRIS times must fit in signed int64")
    if _append_int64 is None:
        # PySide has no QVariant(type, value). appendVariant(int) picks int32
        # for small values, and operator<< resolves to ushort. Call the public
        # Qt overload directly on the binding's QDBusArgument (Linux Qt ABI).
        try:
            library = ctypes.CDLL("libQt6DBus.so.6")
            _append_int64 = library._ZN13QDBusArgumentlsEx
        except (OSError, AttributeError):
            _append_int64 = False
        else:
            _append_int64.argtypes = (ctypes.c_void_p, ctypes.c_longlong)
            _append_int64.restype = ctypes.c_void_p
    if not _append_int64:
        return None
    argument = QDBusArgument()
    _append_int64(getCppPointer(argument)[0], value)
    return argument


def _strings(values: list[str]) -> QDBusArgument:
    """Keep empty lists typed as as, too."""
    argument = QDBusArgument()
    argument.beginArray(QMetaType.Type.QString)
    for value in values:
        argument.appendVariant(value)
    argument.endArray()
    return argument


def _metadata(values: dict) -> dict:
    result = dict(values)
    result["mpris:trackid"] = QDBusObjectPath(result["mpris:trackid"])
    result["xesam:artist"] = _strings(result["xesam:artist"])
    if "mpris:length" in result:
        length = _int64(result["mpris:length"])
        if length is None:
            del result["mpris:length"]
        else:
            result["mpris:length"] = length
    return result


def _variant_map(values: dict) -> QDBusArgument:
    """Avoid Python dict being stored as an opaque PyObject in a QVariant."""
    argument = QDBusArgument()
    # Construct a variant first so its metatype is registered by PySide.
    variant = QDBusVariant()
    argument.beginMap(int(QMetaType.Type.QString), QMetaType.fromName(b"QDBusVariant").id())
    for key, value in values.items():
        argument.beginMapEntry()
        argument.appendVariant(key)
        variant.setVariant(_variant_map(value) if isinstance(value, dict) else value)
        argument << variant
        argument.endMapEntry()
    argument.endMap()
    return argument


def _state_property(name, qt_type):
    return Property(qt_type, lambda self: self.parent()._state[name])


@ClassInfo(**{"D-Bus Interface": _ROOT})
class _RootAdaptor(QDBusAbstractAdaptor):
    Identity = Property(str, lambda self: "Luna IPTV", constant=True)
    DesktopEntry = Property(str, lambda self: "luna-iptv", constant=True)
    CanRaise = Property(bool, lambda self: True, constant=True)
    CanQuit = Property(bool, lambda self: False, constant=True)
    HasTrackList = Property(bool, lambda self: False, constant=True)
    SupportedUriSchemes = Property("QStringList", lambda self: [], constant=True)
    SupportedMimeTypes = Property("QStringList", lambda self: [], constant=True)

    @Slot()
    def Raise(self):
        self.parent()._controller.raise_window()

    @Slot()
    def Quit(self):
        pass


@ClassInfo(**{"D-Bus Interface": _PLAYER})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    Seeked = Signal("qlonglong", arguments=["Position"])

    PlaybackStatus = _state_property("PlaybackStatus", str)
    LoopStatus = Property(str, lambda self: "None", lambda self, value: None)
    Rate = Property(float, lambda self: 1.0, lambda self, value: None)
    Shuffle = Property(bool, lambda self: False, lambda self, value: None)
    Metadata = Property("QVariantMap", lambda self: _metadata(self.parent()._state["Metadata"]))
    Volume = Property(float, lambda self: 1.0, lambda self, value: None)
    Position = Property("qlonglong", lambda self: self.parent()._position)
    MinimumRate = Property(float, lambda self: 1.0, constant=True)
    MaximumRate = Property(float, lambda self: 1.0, constant=True)
    CanGoNext = _state_property("CanGoNext", bool)
    CanGoPrevious = _state_property("CanGoPrevious", bool)
    CanPlay = Property(bool, lambda self: True, constant=True)
    CanPause = Property(bool, lambda self: True, constant=True)
    CanSeek = _state_property("CanSeek", bool)
    CanControl = Property(bool, lambda self: True, constant=True)

    @Slot()
    def PlayPause(self):
        self.parent()._controller.play_pause()

    @Slot()
    def Play(self):
        self.parent()._controller.play()

    @Slot()
    def Pause(self):
        self.parent()._controller.pause()

    @Slot()
    def Stop(self):
        self.parent()._controller.stop()

    @Slot()
    def Next(self):
        if self.parent()._state["CanGoNext"]:
            self.parent()._controller.next()

    @Slot()
    def Previous(self):
        if self.parent()._state["CanGoPrevious"]:
            self.parent()._controller.previous()

    @Slot("qlonglong")
    def Seek(self, offset):
        if self.parent()._state["CanSeek"]:
            self.parent()._controller.seek(offset)

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track_id, position):
        service = self.parent()
        metadata = service._state["Metadata"]
        if (
            service._state["CanSeek"]
            and track_id.path() == metadata["mpris:trackid"]
            and track_id.path() != _NO_TRACK
            and position >= 0
            and ("mpris:length" not in metadata or position <= metadata["mpris:length"])
        ):
            service._controller.set_position(position)

    @Slot(str)
    def OpenUri(self, uri):
        pass


class MprisService(QObject):
    """Publish cached state; call update/seeked from the Qt GUI thread.

    The controller owns playback. Call close before discarding the service.
    Missing D-Bus and registration conflicts leave the service inactive.
    """

    def __init__(
        self,
        controller,
        parent=None,
        *,
        connection=None,
        service_name="org.mpris.MediaPlayer2.luna_iptv",
    ):
        super().__init__(parent)
        self._controller = controller
        self._connection = connection if connection is not None else QDBusConnection.sessionBus()
        self._service_name = service_name
        self._registered = False
        self._position = 0
        self._state = {
            "PlaybackStatus": "Stopped",
            "Metadata": {
                "mpris:trackid": _NO_TRACK,
                "xesam:title": "",
                "xesam:artist": [],
                "mpris:artUrl": "",
            },
            "CanSeek": False,
            "CanGoNext": False,
            "CanGoPrevious": False,
        }
        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)
        if not self._connection.isConnected():
            return
        # Never unregister somebody else's object when sharing a connection.
        if not self._connection.registerObject(_PATH, self, QDBusConnection.ExportAdaptors):
            return
        if not self._connection.registerService(service_name):
            self._connection.unregisterObject(_PATH)
            return
        self._registered = True

    @property
    def active(self) -> bool:
        return self._registered and self._connection.isConnected()

    def update(
        self,
        *,
        status: str,
        title: str = "",
        artist: str = "",
        art_url: str = "",
        length_us: int | None = None,
        can_seek: bool = False,
        can_go_next: bool = False,
        can_go_previous: bool = False,
        track_id: str = "",
    ) -> None:
        """Publish only changed properties, with metadata as one atomic value."""
        if not self.active:
            return
        if status not in ("Playing", "Paused", "Stopped"):
            raise ValueError("Invalid MPRIS playback status")
        metadata = {
            "mpris:trackid": track_id if _OBJECT_PATH.fullmatch(track_id) else _NO_TRACK,
            "xesam:title": title,
            "xesam:artist": [artist] if artist else [],
            "mpris:artUrl": art_url,
        }
        if length_us is not None:
            metadata["mpris:length"] = length_us
        state = {
            "PlaybackStatus": status,
            "Metadata": metadata,
            "CanSeek": can_seek,
            "CanGoNext": can_go_next,
            "CanGoPrevious": can_go_previous,
        }
        changed = {key: value for key, value in state.items() if self._state[key] != value}
        if not changed:
            return
        if "Metadata" in changed:
            changed["Metadata"] = _metadata(metadata)
        self._state = state
        message = QDBusMessage.createSignal(
            _PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged"
        )
        message.setArguments([_PLAYER, _variant_map(changed), _strings([])])
        self._connection.send(message)

    def set_position(self, position_us: int) -> None:
        """Cache position without emitting PropertiesChanged."""
        self._position = position_us

    def seeked(self, position_us: int) -> None:
        """Cache and announce a discontinuous position change."""
        self.set_position(position_us)
        if self.active:
            self._player.Seeked.emit(position_us)

    def close(self) -> None:
        """Release only the object and name this service registered."""
        if self._registered:
            self._registered = False
            self._connection.unregisterObject(_PATH)
            self._connection.unregisterService(self._service_name)
