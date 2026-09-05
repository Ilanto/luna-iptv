from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

from .models import Channel, Playlist

_ATTRIBUTE = re.compile(r"([\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,]+))")
_ALLOWED_SCHEMES = {"http", "https", "rtsp", "rtp", "udp", "file"}


def _split_unquoted_comma(value: str) -> tuple[str, str]:
    quote = ""
    for index, character in enumerate(value):
        if character in {'"', "'"}:
            if not quote:
                quote = character
            elif quote == character:
                quote = ""
        elif character == "," and not quote:
            return value[:index], value[index + 1 :]
    return value, ""


def _attributes(value: str) -> dict[str, str]:
    return {
        match.group(1).lower(): next(group for group in match.groups()[1:] if group is not None)
        for match in _ATTRIBUTE.finditer(value)
    }


def _base_is_remote(base_url: str) -> bool:
    return urlparse(base_url).scheme.lower() in {"http", "https"}


def _resolve_url(value: str, base_url: str) -> tuple[str | None, str | None]:
    raw = value.strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    remote_base = _base_is_remote(base_url)

    if scheme:
        if scheme not in _ALLOWED_SCHEMES:
            return None, f"Unsupported stream scheme: {scheme}"
        if scheme == "file" and remote_base:
            return None, "Remote playlists cannot reference local files"
        if scheme == "file":
            return unquote(parsed.path), None
        return raw, None

    if remote_base:
        return urljoin(base_url, raw), None
    if raw.startswith("//"):
        return None, "Unsupported scheme-relative stream URL"
    if not base_url:
        return str(Path(raw).expanduser()), None

    base_parsed = urlparse(base_url)
    if base_parsed.scheme == "file":
        base_path = Path(unquote(base_parsed.path))
    else:
        base_path = Path(base_url).expanduser()
    return str((base_path.parent / raw).resolve(strict=False)), None


def resolve_logo(value: str, base_url: str = "") -> str:
    if not value.strip():
        return ""
    try:
        resolved, _ = _resolve_url(value, base_url)
        if _base_is_remote(base_url) and urlparse(resolved or "").scheme not in {"http", "https"}:
            return ""
        return resolved or ""
    except ValueError:
        return ""


def _pipe_headers(value: str) -> tuple[str, dict[str, str]]:
    if "|" not in value:
        return value, {}
    url, encoded = value.split("|", 1)
    return url, {key: val for key, val in parse_qsl(encoded, keep_blank_values=True)}


def _channel_id(tvg_id: str, name: str, url: str) -> str:
    identity = f"tvg:{tvg_id}" if tvg_id else f"stream:{name}\0{url}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _hls_playlist(text: str, base_url: str) -> Playlist | None:
    if not any(line.lstrip().upper().startswith("#EXT-X-") for line in text.splitlines()):
        return None
    resolved, warning = (
        _resolve_url(base_url, "") if base_url else (None, "HLS manifest has no source URL")
    )
    if not resolved:
        return Playlist([], [], [warning or "HLS manifest has no source URL"])
    name = Path(urlparse(resolved).path).stem or "HLS stream"
    return Playlist([Channel(_channel_id("", name, resolved), name, resolved)], [], [])


def parse_m3u(text: str, base_url: str = "") -> Playlist:
    text = text.lstrip("\ufeff")
    first_content = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_content.upper().startswith("#EXTM3U") and first_content.startswith(
        ("<", "{", "[")
    ):
        return Playlist([], [], ["Source response is not an M3U playlist"])
    hls = _hls_playlist(text, base_url)
    if hls is not None:
        return hls

    channels: list[Channel] = []
    warnings: list[str] = []
    epg_urls: list[str] = []
    pending: tuple[dict[str, str], str] | None = None
    pending_headers: dict[str, str] = {}
    id_counts: dict[str, int] = {}

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            attrs = _attributes(line[len("#EXTM3U") :])
            guides = attrs.get("url-tvg") or attrs.get("x-tvg-url") or ""
            for guide in (url.strip() for url in guides.split(",") if url.strip()):
                resolved_guide, warning = _resolve_url(guide, base_url)
                if warning:
                    warnings.append(f"Invalid EPG URL: {warning}")
                elif resolved_guide and (
                    not _base_is_remote(base_url)
                    or urlparse(resolved_guide).scheme.lower() in {"http", "https"}
                ):
                    epg_urls.append(resolved_guide)
                else:
                    warnings.append("Remote playlists may only reference HTTP(S) EPG URLs")
            continue
        if upper.startswith("#EXTINF:"):
            if pending is not None:
                warnings.append(f"Missing stream URL for {pending[1] or 'entry'}")
            metadata, name = _split_unquoted_comma(line[len("#EXTINF:") :])
            pending = (_attributes(metadata), name.strip())
            pending_headers = {}
            continue
        if upper.startswith("#EXTVLCOPT:"):
            option = line[len("#EXTVLCOPT:") :]
            key, separator, value = option.partition("=")
            if separator:
                normalized = {
                    "http-user-agent": "User-Agent",
                    "http-referrer": "Referer",
                    "http-referer": "Referer",
                }.get(key.strip().lower())
                if normalized:
                    pending_headers[normalized] = value.strip()
            continue
        if line.startswith("#"):
            continue

        attrs, name = pending if pending is not None else ({}, "")
        raw_url, inline_headers = _pipe_headers(line)
        resolved, warning = _resolve_url(raw_url, base_url)
        if warning:
            warnings.append(f"Line {line_number}: {warning}")
        elif resolved:
            channel_name = name or Path(urlparse(resolved).path).stem or "Untitled stream"
            base_id = _channel_id(attrs.get("tvg-id", ""), channel_name, resolved)
            ordinal = id_counts.get(base_id, 0) + 1
            id_counts[base_id] = ordinal
            channel_id = base_id if ordinal == 1 else f"{base_id}-{ordinal}"
            declared_kind = attrs.get("kind", "").lower()
            if declared_kind in {"live", "movie", "series"}:
                kind = declared_kind
            elif declared_kind:
                kind = "live"
            else:
                extension = Path(urlparse(resolved).path).suffix.lower()
                kind = "movie" if extension in {".mp4", ".mkv", ".avi", ".webm", ".mov"} else "live"
            channels.append(
                Channel(
                    id=channel_id,
                    name=channel_name,
                    url=resolved,
                    group=attrs.get("group-title", ""),
                    tvg_id=attrs.get("tvg-id", ""),
                    logo=resolve_logo(attrs.get("tvg-logo", ""), base_url),
                    kind=kind,
                    series_id=attrs.get("series-id", ""),
                    headers={**pending_headers, **inline_headers},
                )
            )
        pending = None
        pending_headers = {}

    if pending is not None:
        warnings.append(f"Missing stream URL for {pending[1] or 'entry'}")
    return Playlist(channels, list(dict.fromkeys(epg_urls)), warnings)
