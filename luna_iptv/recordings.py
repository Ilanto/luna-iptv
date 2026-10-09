"""Persistent recording deadlines and independent, bounded ffmpeg processes."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .i18n import N_, _

NO_FFMPEG = N_("Kayıt için ffmpeg gerekli. ffmpeg kurup yeniden dene.")
PADDING_CHOICES = (
    (N_("3 dk"), 3),
    (N_("0 dk"), 0),
    (N_("1 dk"), 1),
    (N_("5 dk"), 5),
    (N_("10 dk"), 10),
)


def recording_folder(store):
    value = store.setting("recording_folder", "")
    return (
        Path(value).expanduser()
        if isinstance(value, str) and value.strip()
        else Path.home() / "Videos" / "Luna"
    )


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(text))
    # Bound UTF-8 bytes too: a filesystem component is usually limited to 255.
    return text.encode("utf-8")[:80].decode("utf-8", errors="ignore").strip(" .") or "İsimsiz"


def recording_filename(channel, title, start):
    stamp = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H.%M")
    return f"{safe_name(channel)} - {safe_name(title)} - {stamp}.ts"


def recording_command(executable, channel, duration, path):
    """Arguments only; never pass a provider URL through a shell or diagnostic."""
    if duration <= 0 or not math.isfinite(duration):
        raise ValueError(_("Kayıt süresi geçersiz."))
    args = [executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
    headers = []
    for name, value in channel.headers.items():
        if any(c in str(name) + str(value) for c in "\r\n\x00") or ":" in str(name):
            raise ValueError(_("Yayın HTTP başlıkları geçersiz."))
        if name.lower() == "user-agent":
            args += ["-user_agent", str(value)]
        else:
            headers.append(f"{name}: {value}\r\n")
    if headers:
        args += ["-headers", "".join(headers)]
    args += [
        "-i",
        channel.url,
        "-c",
        "copy",
        "-t",
        str(math.ceil(duration)),
        "-f",
        "mpegts",
        str(Path(path).absolute()),
    ]
    return args


class RecordingService(QObject):
    """Re-arm schedules on startup; never take ownership of the playing mpv."""

    changed = Signal()

    def __init__(
        self,
        store,
        parent=None,
        *,
        runner=subprocess.Popen,
        clock=time.time,
        find_ffmpeg=lambda: shutil.which("ffmpeg"),
        is_locked=None,
        kids=None,
        authorize=lambda channel: False,
    ):
        super().__init__(parent)
        self.store = store
        self._runner, self._clock, self._find_ffmpeg = runner, clock, find_ffmpeg
        self._is_locked = is_locked or self._stored_lock
        self._kids = kids or (
            lambda: any(p["id"] == store.profile_id and p["kids"] for p in store.profiles())
        )
        self._authorize = authorize
        self._processes = {}
        self._stopping = {}
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.tick)
        now = self._clock()
        for item in self.store.recordings():
            if item["status"] == "running":
                self.store.update_recording(
                    item["id"], "interrupted", message=N_("Kayıt yarıda kesildi.")
                )
            elif item["status"] == "scheduled" and item["end"] <= now:
                self.store.update_recording(
                    item["id"], "missed", message=N_("Program saati kaçırıldı.")
                )
        self._arm()

    def _stored_lock(self, channel):
        return bool(self.store.pin_hash()) and (
            channel.id in self.store.locked_channels()
            or (channel.id.split(":", 1)[0], channel.group) in self.store.locked_groups()
        )

    @property
    def available(self):
        return bool(self._find_ffmpeg())

    def add(self, channel, programme):
        if self._closed:
            return None
        if not self.available:
            raise ValueError(_(NO_FFMPEG))
        current = next((c for c in self.store.channels() if c.id == channel.id), None)
        if current is None:
            raise ValueError(_("Bu kanal artık kaynakta bulunmuyor."))
        channel = current
        if channel.kind != "live" or not channel.url:
            raise ValueError(_("Yalnızca canlı kanallar kaydedilebilir."))
        if programme.start.utcoffset() is None or programme.end.utcoffset() is None:
            raise ValueError(_("Program saat dilimi geçersiz."))
        start, end = int(programme.start.timestamp()), int(programme.end.timestamp())
        if end <= max(start, self._clock()):
            raise ValueError(_("Bitmiş program kaydedilemez."))
        locked, kids = self._is_locked(channel), self._kids()
        if locked and kids:
            raise ValueError(_("Bu içerik bu profilde kapalı."))
        if locked and not self._authorize(channel):
            return None
        # A PIN prompt can run a nested event loop and switch profiles.
        if locked and self._kids():
            return None
        padding = self.store.setting("recording_padding", 3)
        padding = padding if type(padding) is int and padding in (0, 1, 3, 5, 10) else 3
        identity = self.store.add_recording(
            channel, programme.title, start, end, padding, authorized=locked, kids=kids
        )
        self.tick()
        self.changed.emit()
        return identity

    def _arm(self):
        self._timer.stop()
        if self._closed:
            return
        deadlines = [r["start"] - 60 for r in self.store.recordings() if r["status"] == "scheduled"]
        if self._processes:
            deadlines.append(self._clock() + 1)
        if deadlines:
            delay = math.ceil(max(0, min(deadlines) - self._clock()) * 1000)
            # Recheck wall clock after sleep or a clock change.
            self._timer.start(min(delay, 60_000))

    def _permitted(self, channel, item):
        return not self._is_locked(channel) or (
            not self._kids() and not item["kids"] and bool(item["authorized"])
        )

    def tick(self):
        if self._closed:
            return
        now = self._clock()
        channels = {channel.id: channel for channel in self.store.channels()}
        changed = False
        for item in self.store.recordings():
            identity = item["id"]
            process = self._processes.get(identity)
            if process is not None:
                code = process.poll()
                if code is not None:
                    status = self._stopping.pop(
                        identity, ("finished" if code == 0 else "failed", 0)
                    )[0]
                    self.store.update_recording(
                        identity, status, message="" if code == 0 else N_("Kayıt sona erdi.")
                    )
                    del self._processes[identity]
                    changed = True
                elif identity in self._stopping:
                    if now >= self._stopping[identity][1]:
                        process.kill()
                elif now >= item["end"] + item["padding"] * 60:
                    self._stop(identity, "finished")
                elif (channel := channels.get(item["channel_id"])) is None or not self._permitted(
                    channel, item
                ):
                    self._stop(identity, "stopped")
                continue
            if item["status"] != "scheduled" or item["start"] - 60 > now:
                continue
            changed = True
            if item["end"] <= now:
                self.store.update_recording(
                    identity, "missed", message=N_("Program saati kaçırıldı.")
                )
                continue
            channel = channels.get(item["channel_id"])
            if channel is None or channel.kind != "live" or not self._permitted(channel, item):
                self.store.update_recording(
                    identity, "cancelled", message=N_("Kanal kullanılamıyor veya kilitli.")
                )
                continue
            executable = self._find_ffmpeg()
            if not executable:
                self.store.update_recording(identity, "failed", message=NO_FFMPEG)
                continue
            try:
                folder = recording_folder(self.store).absolute()
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / recording_filename(channel.name, item["title"], item["start"])
                if path.exists() or any(r["path"] == str(path) for r in self.store.recordings()):
                    path = path.with_stem(f"{path.stem} ({identity})")
                duration = item["end"] + item["padding"] * 60 - now
                args = recording_command(executable, channel, duration, path)
                process = self._runner(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, ValueError):
                self.store.update_recording(
                    identity,
                    "failed",
                    message=N_("Kayıt başlatılamadı. Klasörü ve ffmpeg'i kontrol et."),
                )
            else:
                self._processes[identity] = process
                self.store.update_recording(identity, "running", path=str(path))
        self._arm()
        if changed:
            self.changed.emit()

    def _stop(self, identity, status):
        if identity not in self._stopping:
            self._processes[identity].terminate()
            self._stopping[identity] = (status, self._clock() + 3)

    def cancel(self, identity):
        if self._closed:
            return
        if identity in self._processes:
            self._stop(identity, "stopped")
        elif any(
            r["id"] == identity and r["status"] == "scheduled" for r in self.store.recordings()
        ):
            self.store.update_recording(identity, "cancelled")
        self._arm()
        self.changed.emit()

    def close(self):
        self._closed = True
        self._timer.stop()
        for identity, process in self._processes.items():
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            self.store.update_recording(
                identity, "interrupted", message=N_("Uygulama kapanırken durduruldu.")
            )
        self._processes.clear()
        self._stopping.clear()
