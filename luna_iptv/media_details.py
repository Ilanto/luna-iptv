"""Display-only provider metadata, independent of playback and Qt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urlsplit

from .models import Channel
from .playlist import resolve_logo


@dataclass
class MediaDetails:
    info: dict[str, str] = field(default_factory=dict)
    episodes: list[Channel] = field(default_factory=list)
    episode_info: dict[str, dict[str, str]] = field(default_factory=dict)
    series_title: str = ""


def _text(value: object) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = " ".join(re.sub(r"<[^>]*>", " ", unescape(str(value))).split())
    return "" if text.casefold() in {"", "null", "none", "n/a", "nan", "inf"} else text


def _names(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(text for item in value if (text := _text(item)))
    return _text(value)


def _imdb_id(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"tt[0-9]{7,}", text):
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or parts.hostname not in {
        "imdb.com",
        "www.imdb.com",
        "m.imdb.com",
    }:
        return ""
    match = re.fullmatch(r"/title/(tt[0-9]{7,})/?", parts.path)
    return match[1] if match else ""


def _rating(value: object) -> str:
    text = _text(value)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:\s*/\s*10)?", text)
    if match and 0 <= float(match[1]) <= 10:
        return format(float(match[1]), "g")
    return ""


def _duration(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"[0-9]{1,3}:[0-9]{2}(?::[0-9]{2})?", text):
        parts = [int(part) for part in text.split(":")]
        if any(parts) and all(part < 60 for part in parts[1:]):
            return ":".join(f"{part:02d}" for part in parts)
        return ""
    minutes = re.fullmatch(r"([0-9]{1,6}(?:\.[0-9]{1,3})?)\s*(?:dk|min|minutes?)?", text)
    if minutes and float(minutes[1]) > 0:
        return f"{float(minutes[1]):g} dk"
    return ""


def normalize_info(*records: object, base_url: str = "") -> dict[str, str]:
    """Pick the first usable field; never infer rating provenance from a title ID."""
    result: dict[str, str] = {}
    fields = {
        "description": ("description", "plot", "overview"),
        "genre": ("genre", "genres"),
        "duration": ("duration", "runtime", "episode_run_time"),
        "director": ("director",),
        "cast": ("cast", "actors"),
        "country": ("country", "countries"),
        "language": ("language", "languages"),
        "year": ("year", "releasedate", "release_date", "releaseDate", "air_date"),
        "season": ("season",),
        "episode": ("episode", "episode_num"),
        "imdb_id": ("imdb_id", "imdb", "imdb_url"),
        "poster": ("poster", "movie_image", "cover_big", "cover", "stream_icon"),
    }
    for record in records:
        if not isinstance(record, dict):
            continue
        for target, aliases in fields.items():
            if target in result:
                continue
            for alias in aliases:
                value = record.get(alias)
                text = (
                    _names(value)
                    if target in {"genre", "director", "cast", "country", "language"}
                    else _text(value)
                )
                if target == "imdb_id":
                    text = _imdb_id(value)
                elif target == "year":
                    match = re.match(r"([12][0-9]{3})(?:$|[-/])", text)
                    text = match[1] if match else ""
                elif target == "poster":
                    text = resolve_logo(text, base_url)
                elif target in {"season", "episode"}:
                    text = text if re.fullmatch(r"[0-9]{1,5}", text) else ""
                elif target == "duration":
                    text = _duration(value)
                if text:
                    result[target] = text
                    break
        if "duration" not in result:
            seconds = _text(record.get("duration_secs"))
            if re.fullmatch(r"[0-9]{1,8}", seconds) and 0 < int(seconds) < 10**8:
                hours, remainder = divmod(int(seconds), 3600)
                minutes, seconds = divmod(remainder, 60)
                result["duration"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if "rating" not in result:
            for key, source in (("imdb_rating", "IMDb"), ("rating_imdb", "IMDb"), ("rating", "")):
                rating = _rating(record.get(key))
                if not rating:
                    continue
                if not source:
                    source = {"imdb": "IMDb", "tmdb": "TMDb"}.get(
                        _text(record.get("rating_source")).casefold(), ""
                    )
                result["rating"] = rating
                if source:
                    result["rating_source"] = source
                break
    return result
