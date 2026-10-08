"""Versioned personal backups, validated before any database write."""

from __future__ import annotations

import json
import math
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .preferences import normalize_preferences

MAX_FILE_SIZE = 32 * 1024 * 1024
_CONNECTION = {"location", "username", "password", "epg_url"}
_SOURCE_FIELDS = {"id", "name", "type", *_CONNECTION, "credentials_omitted"}
_CHANNEL_FIELDS = {"id", "source_id", "name", "kind", "series_id", "provider_key"}
_ROOT_FIELDS = {
    "format",
    "version",
    "created",
    "sources",
    "channels",
    "favorites",
    "favorite_folders",
    "playback_preferences",
    "app_settings",
    "history",
}


@dataclass(frozen=True)
class BackupSummary:
    sources: int
    favorites: int
    favorite_folders: int
    folder_members: int
    playback_preferences: int
    app_settings: int
    history: int
    credentials_present: bool
    incomplete_sources: int


def source_incomplete(source: dict) -> bool:
    return not source.get("location") or (
        source.get("type") == "xtream"
        and (not source.get("username") or not source.get("password"))
    )


def _private_url(value: str) -> bool:
    """Opaque URL paths can be access tokens too; omit them conservatively."""
    if "://" not in value and not value.startswith("//"):
        return False
    try:
        url = urlsplit(value)
        return bool(url.username or url.password) or (
            url.scheme != "file" and bool(url.query or url.fragment or url.path.strip("/"))
        )
    except ValueError:
        return True


def _redact_settings(value):
    if isinstance(value, str):
        return "" if _private_url(value) else value
    if isinstance(value, list):
        return [_redact_settings(item) for item in value]
    if isinstance(value, dict):
        return {
            key: ""
            if key.casefold() in {"username", "password", "token", "authorization"}
            else _redact_settings(item)
            for key, item in value.items()
        }
    return value


def export_backup(store, *, include_credentials=False, include_history=True) -> dict:
    data = store.backup_records(include_history=include_history)
    data.update(
        format="luna-iptv-backup", version=1, created=datetime.now(timezone.utc).isoformat()
    )
    for source in data["sources"]:
        omitted = []
        if not include_credentials:
            for field in sorted(_CONNECTION):
                if source[field] and (
                    field in {"username", "password"} or _private_url(source[field])
                ):
                    source[field] = ""
                    omitted.append(field)
        source["credentials_omitted"] = omitted
    if not include_credentials:
        data["app_settings"] = _redact_settings(data["app_settings"])
    validate_backup(data)
    return data


def _require(condition, message="Yedek dosyasının veri yapısı geçersiz."):
    if not condition:
        raise ValueError(message)


def _json_tree(value, depth=0):
    _require(depth <= 16, "Yedek dosyasında çok fazla iç içe veri var.")
    if isinstance(value, str):
        _require(len(value) <= 16384, "Yedek dosyasında çok uzun bir metin var.")
        _require(
            not any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value),
            "Yedek dosyası kontrol karakteri içeriyor.",
        )
    elif type(value) in (int, float):
        _require(math.isfinite(value) and abs(value) <= 2**63 - 1)
    elif isinstance(value, (dict, list)):
        _require(len(value) <= 100000, "Yedek dosyasında çok fazla kayıt var.")
        if isinstance(value, dict):
            for key, item in value.items():
                _require(isinstance(key, str))
                _json_tree(key, depth + 1)
                _json_tree(item, depth + 1)
        else:
            for item in value:
                _json_tree(item, depth + 1)
    else:
        _require(value is None or type(value) is bool)


def _text(value, *, nonempty=False, limit=16384):
    _require(isinstance(value, str) and len(value) <= limit)
    if nonempty:
        _require(bool(value.strip()))


def _records(data, name, fields, identity="id"):
    items = data[name]
    _require(isinstance(items, list))
    seen = set()
    for item in items:
        _require(isinstance(item, dict) and set(item) == fields)
        key = item[identity]
        _require(type(key) in (str, int))
        _require(key not in seen, "Yedek dosyasında yinelenen kayıt kimliği var.")
        seen.add(key)
    return items


def _ids(items, known):
    _require(isinstance(items, list))
    _require(all(isinstance(item, str) for item in items))
    _require(len(set(items)) == len(items) and set(items) <= known)


def _has_credentials(value):
    if isinstance(value, str):
        return _private_url(value)
    if isinstance(value, list):
        return any(_has_credentials(item) for item in value)
    if isinstance(value, dict):
        return any(
            (key.casefold() in {"username", "password", "token", "authorization"} and bool(item))
            or _has_credentials(item)
            for key, item in value.items()
        )
    return False


def validate_backup(data) -> BackupSummary:
    try:
        return _validate_backup(data)
    except (TypeError, KeyError, OverflowError, RecursionError, UnicodeError) as error:
        raise ValueError("Yedek dosyasının veri yapısı geçersiz.") from error


def _validate_backup(data) -> BackupSummary:
    _require(isinstance(data, dict) and set(data) == _ROOT_FIELDS)
    _require(data["format"] == "luna-iptv-backup", "Bu dosya bir Luna IPTV yedeği değil.")
    _require(
        type(data["version"]) is int and data["version"] == 1, "Bu yedek sürümü desteklenmiyor."
    )
    _json_tree(data)
    _require(
        len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= MAX_FILE_SIZE,
        "Yedek dosyası 32 MB sınırını aşıyor.",
    )
    _text(data["created"], nonempty=True, limit=64)
    try:
        created = datetime.fromisoformat(data["created"])
        _require(created.tzinfo is not None)
    except ValueError as error:
        raise ValueError("Yedek dosyasının tarihi geçersiz.") from error
    sources = _records(data, "sources", _SOURCE_FIELDS)
    _require(len(sources) <= 10000)
    for source in sources:
        for field in _SOURCE_FIELDS - {"credentials_omitted"}:
            _text(source[field], nonempty=field in {"id", "name", "type"})
        _text(source["id"], nonempty=True, limit=512)
        _text(source["name"], nonempty=True, limit=512)
        _require(source["type"] in {"xtream", "m3u", "direct"})
        _ids(source["credentials_omitted"], _CONNECTION)
        _require(all(not source[field] for field in source["credentials_omitted"]))
    source_ids = {item["id"] for item in sources}
    channels = _records(data, "channels", _CHANNEL_FIELDS)
    provider_keys = set()
    for channel in channels:
        for field in _CHANNEL_FIELDS:
            _text(channel[field], nonempty=field in {"id", "source_id", "name", "kind"})
        _require(channel["source_id"] in source_ids)
        _require(channel["id"].startswith(channel["source_id"] + ":"))
        _require(channel["kind"] in {"live", "movie", "series"})
        if channel["provider_key"]:
            key = (channel["source_id"], channel["provider_key"])
            _require(key not in provider_keys)
            provider_keys.add(key)
    channel_ids = {item["id"] for item in channels}
    _ids(data["favorites"], channel_ids)
    favorite_ids = set(data["favorites"])
    folders = _records(data, "favorite_folders", {"id", "name", "position", "members"})
    folder_names = set()
    for folder in folders:
        _require(type(folder["id"]) is int and folder["id"] > 0)
        _require(type(folder["position"]) is int and folder["position"] >= 0)
        _text(folder["name"], nonempty=True, limit=512)
        name = folder["name"].strip().casefold()
        _require(name not in folder_names)
        folder_names.add(name)
        _ids(folder["members"], favorite_ids)
    preferences = data["playback_preferences"]
    _require(isinstance(preferences, dict) and set(preferences) <= source_ids)
    for value in preferences.values():
        _require(
            isinstance(value, dict)
            and json.dumps(normalize_preferences(value), sort_keys=True)
            == json.dumps(value, sort_keys=True),
            "Yedekteki oynatma tercihleri geçersiz.",
        )
    _require(isinstance(data["app_settings"], dict))
    for key in data["app_settings"]:
        _text(key, nonempty=True, limit=512)
    history = data["history"]
    if history is not None:
        history = _records(
            data,
            "history",
            {
                "channel_id",
                "position",
                "duration",
                "updated_at",
                "history_hidden",
            },
            "channel_id",
        )
        for item in history:
            _require(item["channel_id"] in channel_ids)
            for key in ("position", "duration"):
                _require(type(item[key]) in (int, float) and item[key] >= 0)
            _require(type(item["updated_at"]) is int and item["updated_at"] >= 0)
            _require(type(item["history_hidden"]) is bool)
    referenced = favorite_ids | {item["channel_id"] for item in history or []}
    _require(channel_ids == referenced, "Yedek yalnızca kişisel kanal başvuruları içermeli.")
    return BackupSummary(
        len(sources),
        len(data["favorites"]),
        len(folders),
        sum(len(item["members"]) for item in folders),
        len(preferences),
        len(data["app_settings"]),
        len(history or []),
        _has_credentials(data),
        sum(bool(source_incomplete(source)) for source in sources),
    )


def apply_backup(store, data) -> BackupSummary:
    summary = validate_backup(data)
    store.apply_backup_records(data)
    return summary


def read_backup(path) -> dict:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE_SIZE + 1)
    _require(len(raw) <= MAX_FILE_SIZE, "Yedek dosyası 32 MB sınırını aşıyor.")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Yedek dosyasında yinelenen JSON alanı var.")
            result[key] = value
        return result

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Yedek dosyası geçerli bir UTF-8 JSON dosyası değil.") from error
    validate_backup(data)
    return data


def write_backup(path, data) -> None:
    validate_backup(data)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".luna-backup-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
