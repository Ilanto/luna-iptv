"""Validation and bounded health checks for saved source connections."""

from __future__ import annotations

import stat
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, url2pathname, urlopen

from .models import Channel, Playlist
from .network import NetworkError, XtreamClient, channel_id, http_url, load_m3u

HEALTH_PREFIX_LIMIT = 4096


@dataclass(frozen=True)
class HealthResult:
    status: str
    checked_at: int


def _checked_at(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def _local_path(location: str) -> Path:
    parsed = urlsplit(location)
    if parsed.scheme not in ("", "file"):
        raise NetworkError("Bu yerel kaynak türü desteklenmiyor.")
    value = url2pathname(parsed.path) if parsed.scheme == "file" else location
    return Path(value).expanduser().resolve()


def _regular_file(location: str) -> Path:
    path = _local_path(location)
    try:
        mode = path.stat().st_mode
    except OSError:
        raise NetworkError("Kaynak dosyası bulunamadı veya okunamıyor.") from None
    if not stat.S_ISREG(mode):
        raise NetworkError("Kaynak normal bir dosya değil.")
    return path


def _remote_m3u_prefix(location: str) -> bytes:
    try:
        request = Request(
            http_url(location),
            headers={"User-Agent": "Luna-IPTV/0.1", "Accept-Encoding": "identity"},
        )
        with urlopen(request, timeout=20) as response:
            return response.read(HEALTH_PREFIX_LIMIT)
    except HTTPError as exc:
        raise NetworkError(f"Sunucu HTTP {exc.code} döndürdü.") from None
    except (URLError, OSError, ValueError):
        raise NetworkError("Kaynağa erişilemedi.") from None


def _direct_head(location: str) -> str:
    try:
        request = Request(
            http_url(location), method="HEAD", headers={"User-Agent": "Luna-IPTV/0.1"}
        )
        with urlopen(request, timeout=20):
            return "responding"
    except HTTPError as exc:
        if exc.code in (405, 501):
            return "unverified"
        raise NetworkError(f"Sunucu HTTP {exc.code} döndürdü.") from None
    except (URLError, OSError, ValueError):
        raise NetworkError("Kaynağa erişilemedi.") from None


def check_connection(source: dict[str, str], *, checked_at: int | None = None) -> HealthResult:
    """Perform one small, type-specific check without loading video or a catalogue."""

    try:
        kind = source.get("type", "")
        location = source.get("location", "")
        if kind == "xtream":
            profile = XtreamClient(
                location, source.get("username", ""), source.get("password", "")
            ).account_info()
            status = "available" if profile.status == "active" else "unavailable"
        elif kind == "m3u":
            if urlsplit(location).scheme in ("http", "https"):
                prefix = _remote_m3u_prefix(location)
            else:
                with _regular_file(location).open("rb") as file:
                    prefix = file.read(HEALTH_PREFIX_LIMIT)
            if not prefix.decode("utf-8-sig", errors="replace").lstrip().startswith("#EXTM3U"):
                raise NetworkError("Kaynak geçerli bir M3U listesi gibi görünmüyor.")
            status = "available"
        elif kind == "direct":
            scheme = urlsplit(location).scheme
            if scheme in ("http", "https"):
                status = _direct_head(location)
            elif scheme in ("rtsp", "rtp", "udp"):
                status = "unverified"
            else:
                _regular_file(location)
                status = "available"
        else:
            raise NetworkError("Kaynak türü desteklenmiyor.")
    except NetworkError:
        return HealthResult("unavailable", _checked_at(checked_at))
    return HealthResult(status, _checked_at(checked_at))


def validate_candidate(source: dict[str, str]) -> Playlist:
    """Fully prepare an edit candidate before any persistent state is changed."""

    kind = source.get("type", "")
    location = source.get("location", "")
    if kind == "xtream":
        return XtreamClient(
            location, source.get("username", ""), source.get("password", "")
        ).catalog()
    if kind == "m3u":
        return load_m3u(location)
    if kind != "direct":
        raise NetworkError("Kaynak türü desteklenmiyor.")

    scheme = urlsplit(location).scheme
    if scheme in ("http", "https"):
        _direct_head(location)
        normalized = http_url(location)
    elif scheme in ("rtsp", "rtp", "udp"):
        normalized = location
    elif scheme in ("", "file"):
        normalized = _regular_file(location).as_uri()
    else:
        raise NetworkError("Bu yayın protokolü desteklenmiyor.")
    movie = scheme in ("", "file") or urlsplit(normalized).path.lower().endswith(
        (".mp4", ".mkv", ".webm", ".mov", ".avi")
    )
    return Playlist(
        [
            Channel(
                channel_id(normalized),
                source.get("name", "") or "Tek yayın",
                normalized,
                group="Tek yayın",
                kind="movie" if movie else "live",
            )
        ],
        [],
        [],
    )


def episode_identity(channel: Channel) -> tuple[str, str] | None:
    if channel.provider_key.startswith("episode:"):
        parts = channel.provider_key.split(":", 2)
        if len(parts) == 3 and all(parts[1:]):
            return unquote(parts[1]), unquote(parts[2])
    if channel.kind != "movie" or not channel.series_id:
        return None
    parts = urlsplit(channel.url).path.rsplit("/", 1)
    if len(parts) != 2 or "/series/" not in urlsplit(channel.url).path:
        return None
    item = unquote(parts[1].rsplit(".", 1)[0]).strip()
    return (channel.series_id, item) if item else None


def retarget_cached_episodes(candidate: dict[str, str], channels: list[Channel]) -> list[Channel]:
    client = XtreamClient(
        candidate["location"], candidate.get("username", ""), candidate.get("password", "")
    )
    result = []
    for channel in channels:
        identity = episode_identity(channel)
        if identity is None:
            result.append(replace(channel, url="", provider_key=""))
            continue
        series_id, episode_id = identity
        suffix = urlsplit(channel.url).path.rsplit("/", 1)[-1]
        extension = suffix.rsplit(".", 1)[1] if "." in suffix else "mp4"
        key = f"episode:{quote(series_id, safe='')}:{quote(episode_id, safe='')}"
        result.append(
            replace(
                channel,
                url=client.stream_url("series", episode_id, extension),
                series_id=series_id,
                provider_key=key,
            )
        )
    return result
