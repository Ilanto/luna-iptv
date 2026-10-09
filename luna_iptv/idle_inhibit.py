"""Keep the desktop session awake while media is playing."""

from __future__ import annotations

import logging

from .i18n import N_, _

log = logging.getLogger(__name__)

APP_ID = "luna-iptv"
REASON = N_("Luna IPTV oynatıyor")
# org.gnome.SessionManager.Inhibit flags: suspend (4) | idle (8).
_GNOME_FLAGS = 4 | 8
_CALL_TIMEOUT = 2.0


def _session_bus():
    import dbus

    return dbus.SessionBus()


class IdleInhibit:
    """Hold at most one desktop idle inhibit while playback runs.

    GNOME's session manager is preferred; the freedesktop screensaver API is the
    fallback. Without D-Bus every call is a quiet no-op. Desktops drop the inhibit
    when our bus connection closes, so a crash cannot leave the screen awake.
    """

    def __init__(self, bus_factory=_session_bus):
        self._bus_factory = bus_factory
        self._bus = None
        self._held = None

    @property
    def active(self) -> bool:
        return self._held is not None

    def set_active(self, active: bool):
        if active and self._held is None:
            self._held = self._acquire()
        elif not active and self._held is not None:
            held, self._held = self._held, None
            self._release(*held)

    def close(self):
        self.set_active(False)

    def _connect(self):
        if self._bus is None:
            try:
                self._bus = self._bus_factory()
            except Exception as exc:
                log.debug("session bus unavailable: %s", exc)
        return self._bus

    def _acquire(self):
        bus = self._connect()
        if bus is None:
            return None
        attempts = (
            (
                ("org.gnome.SessionManager", "/org/gnome/SessionManager"),
                "org.gnome.SessionManager",
                "Uninhibit",
                ("Inhibit", "susu", (APP_ID, 0, _(REASON), _GNOME_FLAGS)),
            ),
            (
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
                "org.freedesktop.ScreenSaver",
                "UnInhibit",
                ("Inhibit", "ss", (APP_ID, _(REASON))),
            ),
        )
        for (service, path), interface, release, (method, signature, args) in attempts:
            try:
                cookie = bus.call_blocking(
                    service, path, interface, method, signature, args, timeout=_CALL_TIMEOUT
                )
            except Exception as exc:
                log.debug("%s.%s failed: %s", interface, method, exc)
                continue
            return service, path, interface, release, int(cookie)
        log.debug("no idle inhibit service is available")
        return None

    def _release(self, service, path, interface, method, cookie):
        try:
            self._bus.call_blocking(
                service, path, interface, method, "u", (cookie,), timeout=_CALL_TIMEOUT
            )
        except Exception as exc:
            log.debug("%s.%s failed: %s", interface, method, exc)
