"""Small, source-oriented playback metadata state for the player UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNKNOWN = "Bilgi yok"


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_dimension(value: Any) -> int | None:
    number = _positive_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _decimal(value: float, precision: int = 2) -> str:
    return f"{value:.{precision}f}".rstrip("0").rstrip(".").replace(".", ",")


def _codec_name(track: dict[str, Any] | None) -> str:
    if not track:
        return UNKNOWN
    description = str(track.get("codec-desc") or "").strip()
    if description:
        return description
    codec = str(track.get("codec") or "").strip().lower()
    known = {
        "aac": "AAC",
        "ac3": "AC-3",
        "eac3": "E-AC-3",
        "flac": "FLAC",
        "h264": "H.264 / AVC",
        "hevc": "H.265 / HEVC",
        "mp2": "MP2",
        "mp3": "MP3",
        "mpeg2video": "MPEG-2 Video",
        "opus": "Opus",
        "vp9": "VP9",
    }
    return known.get(codec, codec.upper() if codec else UNKNOWN)


@dataclass
class MediaInfo:
    """Consume libmpv property changes without querying or polling the backend."""

    _video_params: dict[str, Any] = field(default_factory=dict)
    _video_dec_params: dict[str, Any] = field(default_factory=dict)
    _audio_params: dict[str, Any] = field(default_factory=dict)
    _tracks: list[dict[str, Any]] = field(default_factory=list)
    _interlaced: bool | None = None
    _container_fps: float | None = None
    _video_bitrate: float | None = None
    _audio_bitrate: float | None = None
    _loading: bool = False
    _idle: bool = True
    _paused: bool = False
    _paused_for_cache: bool = False
    _buffer_percent: int | None = None

    def _clear_stream_fields(self) -> None:
        self._video_params = {}
        self._video_dec_params = {}
        self._audio_params = {}
        self._tracks = []
        self._interlaced = None
        self._container_fps = None
        self._video_bitrate = None
        self._audio_bitrate = None
        self._paused = False
        self._paused_for_cache = False
        self._buffer_percent = None

    def reset(self) -> None:
        self._clear_stream_fields()
        self._loading = False
        self._idle = True

    def begin_load(self) -> None:
        self._clear_stream_fields()
        self._loading = True
        self._idle = False

    def mark_loaded(self) -> None:
        self._loading = False
        self._idle = False

    def update(self, name: str, value: Any) -> bool:
        handled = True
        if name == "video-params":
            self._video_params = value if isinstance(value, dict) else {}
        elif name == "video-dec-params":
            self._video_dec_params = value if isinstance(value, dict) else {}
        elif name == "audio-params":
            self._audio_params = value if isinstance(value, dict) else {}
        elif name == "track-list":
            self._tracks = [track for track in (value or []) if isinstance(track, dict)]
        elif name == "video-frame-info/interlaced":
            if isinstance(value, bool):
                self._interlaced = value
            elif isinstance(value, str) and value.lower() in {"yes", "no"}:
                self._interlaced = value.lower() == "yes"
            else:
                self._interlaced = None
        elif name == "container-fps":
            self._container_fps = _positive_number(value)
        elif name == "video-bitrate":
            self._video_bitrate = _positive_number(value)
        elif name == "audio-bitrate":
            self._audio_bitrate = _positive_number(value)
        elif name == "pause":
            self._paused = bool(value)
        elif name == "paused-for-cache":
            self._paused_for_cache = bool(value)
        elif name == "cache-buffering-state":
            number = _positive_number(value)
            if value == 0:
                number = 0
            self._buffer_percent = (
                min(100, max(0, round(number))) if number is not None else None
            )
        elif name == "idle-active":
            if value:
                self.reset()
            else:
                self._idle = False
        else:
            handled = False
        return handled

    def _dimensions(self) -> tuple[int | None, int | None]:
        for params in (self._video_dec_params, self._video_params):
            width = _positive_dimension(params.get("w"))
            height = _positive_dimension(params.get("h"))
            if width and height:
                return width, height
        return None, None

    def _selected_track(self, kind: str) -> dict[str, Any] | None:
        selected = [
            track
            for track in self._tracks
            if track.get("type") == kind and bool(track.get("selected"))
        ]
        return next((track for track in selected if track.get("main-selection") == 0), None) or (
            selected[0] if selected else None
        )

    @property
    def dimensions(self) -> str:
        width, height = self._dimensions()
        return f"{width} × {height}" if width and height else UNKNOWN

    @property
    def quality(self) -> str:
        width, height = self._dimensions()
        if width is None or height is None:
            return UNKNOWN
        scan_lines = min(width, height)
        if scan_lines <= 576:
            name = "SD"
        elif scan_lines < 1080:
            name = "HD"
        elif scan_lines < 1440:
            name = "Full HD"
        elif scan_lines < 2160:
            name = "QHD"
        else:
            name = "4K"
        if scan_lines in {576, 720, 1080, 1440, 2160} and self._interlaced is not None:
            name += f" · {scan_lines}{'i' if self._interlaced else 'p'}"
        return name

    @property
    def video_codec(self) -> str:
        return _codec_name(self._selected_track("video"))

    @property
    def audio_codec(self) -> str:
        return _codec_name(self._selected_track("audio"))

    @property
    def audio_layout(self) -> str:
        for key in ("hr-channels", "channels"):
            value = str(self._audio_params.get(key) or "").strip()
            if value:
                return value
        track = self._selected_track("audio")
        value = str((track or {}).get("demux-channels") or "").strip()
        return value or UNKNOWN

    @property
    def fps(self) -> str:
        return (
            f"{_decimal(self._container_fps, 3)} FPS"
            if self._container_fps is not None
            else UNKNOWN
        )

    @staticmethod
    def _format_bitrate(value: float) -> str:
        if value >= 1_000_000:
            return f"{_decimal(value / 1_000_000)} Mb/sn"
        if value >= 1_000:
            return f"{_decimal(value / 1_000)} kb/sn"
        return f"{_decimal(value, 0)} b/sn"

    @property
    def bitrate(self) -> str:
        parts = []
        if self._video_bitrate is not None:
            parts.append("Video " + self._format_bitrate(self._video_bitrate))
        if self._audio_bitrate is not None:
            parts.append("Ses " + self._format_bitrate(self._audio_bitrate))
        return " · ".join(parts) or UNKNOWN

    @property
    def dynamic_range(self) -> str:
        track = self._selected_track("video")
        if track and _positive_number(track.get("dolby-vision-profile")) is not None:
            return "Dolby Vision"
        gamma = str(self._video_dec_params.get("gamma") or "").strip().lower()
        if gamma in {"pq", "st2084", "smpte2084"}:
            return "HDR (PQ)"
        if gamma in {"hlg", "arib-std-b67"}:
            return "HDR (HLG)"
        if gamma in {
            "bt.1886",
            "gamma1.8",
            "gamma2.0",
            "gamma2.2",
            "gamma2.4",
            "gamma2.6",
            "gamma2.8",
            "linear",
            "prophoto",
            "srgb",
        }:
            return "SDR"
        return UNKNOWN

    @property
    def buffer_text(self) -> str:
        if self._idle:
            return ""
        if self._paused_for_cache:
            return (
                f"Arabellek · %{self._buffer_percent}"
                if self._buffer_percent is not None
                else "Arabellek…"
            )
        if self._loading:
            return "Bağlanıyor…"
        return ""
