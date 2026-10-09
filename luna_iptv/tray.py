"""Tray integration with injectable availability and the window's normal routes."""

from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class TrayController:
    def __init__(self, window, *, available=None):
        self.window = window
        self._available = available
        self.notified = False
        self.menu = QMenu(window)
        self.title_action = self.menu.addAction("Henüz yayın seçilmedi")
        self.title_action.setEnabled(False)
        self.play_action = self.menu.addAction("Oynat/Duraklat", window.toggle_play)
        self.recent_menu = self.menu.addMenu("Son kanallar")
        self.menu.addSeparator()
        self.show_action = self.menu.addAction("Luna'yı göster", self.show_window)
        self.quit_action = self.menu.addAction("Çıkış", window.quit_application)
        self.menu.aboutToShow.connect(self.refresh)
        # Injecting availability exercises the full lifecycle without talking
        # to the desktop notification area (including during offscreen tests).
        self.icon = None
        if available is None:
            self.icon = QSystemTrayIcon(QApplication.windowIcon(), window)
            self.icon.setToolTip("Luna IPTV")
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self._activate)
            if self.available:
                self.icon.show()
        self.refresh()

    @property
    def available(self):
        return (
            QSystemTrayIcon.isSystemTrayAvailable() if self._available is None else self._available
        )

    def refresh(self):
        window = self.window
        if window._closed:
            return
        name = window.current.name if window.current else "Henüz yayın seçilmedi"
        self.title_action.setText(name.replace("&", "&&"))
        self.play_action.setEnabled(window.current is not None)
        if self.icon is not None:
            self.icon.setToolTip(f"Luna IPTV · {name}")
        self.recent_menu.clear()
        by_id = {c.id: c for c in window.model.channels if c.kind == "live"}
        added = 0
        for cid in window.store.recent_ids(len(window.model.channels)):
            channel = by_id.get(cid)
            if channel is None or (window.proxy.hide_locked and cid in window.model.locked):
                continue
            action = self.recent_menu.addAction(channel.name.replace("&", "&&"))
            action.triggered.connect(lambda checked=False, c=channel: self.play_recent(c))
            added += 1
            if added == 5:
                break
        self.recent_menu.setEnabled(added > 0)

    def play_recent(self, channel):
        # A PIN/resume dialog must be visible even when invoked from the tray.
        self.show_window()
        if any(c.id == channel.id for c in self.window.model.channels):
            self.window.request_play(channel)

    def _activate(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self):
        window = self.window
        if window._closed:
            return
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def hide_on_close(self):
        window = self.window
        if not self.available or window.store.setting("close_to_tray", False) is not True:
            return False
        if self.icon is not None:
            self.icon.show()
        window.hide()
        if not self.notified:
            self.notified = True
            window.status("Luna tepside çalışıyor")
            if self.icon is not None:
                self.icon.showMessage("Luna IPTV", "Luna tepside çalışıyor")
        return True

    def close(self):
        if self.icon is not None:
            self.icon.hide()
        self.menu.close()
