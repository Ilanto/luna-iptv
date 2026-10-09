"""Opt-in OpenSubtitles search and bounded SRT downloads, independent of Qt."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from .online import OnlineError, read_json, request, write_bytes, write_json
from .tmdb import clean_title

API = "https://api.opensubtitles.com/api/v1"
SRT_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class Subtitle:
    file_id: int
    language: str
    release: str
    downloads: int


def file_hash(url):
    """Hash only regular local media; never read streams, devices or remote URLs."""
    parts = urlsplit(url)
    if parts.scheme not in ("", "file") or parts.netloc not in ("", "localhost"):
        return ""
    path = Path(unquote(parts.path) if parts.scheme else url)
    try:
        # Resolve symlinks and reject devices before opening anything.
        path = path.resolve()
        if not stat.S_ISREG(path.stat().st_mode):
            return ""
        size = path.stat().st_size
        if size < 131072:
            return ""
        with path.open("rb") as stream:
            head = stream.read(65536)
            stream.seek(-65536, 2)
            tail = stream.read(65536)
        if len(head) != 65536 or len(tail) != 65536:
            return ""
        value = size + sum(n[0] for n in struct.iter_unpack("<Q", head + tail))
        return f"{value & 0xFFFFFFFFFFFFFFFF:016x}"
    except (OSError, ValueError):
        return ""


def episode_query(channel, series_title="", info=None):
    """Use provider numbers or explicit SxxExx/Bölüm labels; never guess row order."""
    info = info or {}
    title, year = clean_title(series_title or channel.name, info.get("year", ""))
    result = {"query": title}
    if year and not channel.series_id:
        result["year"] = year
    if channel.series_id:
        season = re.search(
            r"(?i)(?:sezon\s*|season\s*|\bS)(\d+)", channel.group + " " + channel.name
        )
        episode = re.search(r"(?i)(?:bölüm\s*|episode\s*|S\d+E)(\d+)", channel.name)
        for key, value in (
            ("season_number", info.get("season") or (season[1] if season else "")),
            ("episode_number", info.get("episode") or (episode[1] if episode else "")),
        ):
            if str(value).isdigit():
                result[key] = int(value)
        if not series_title:
            result["query"] = re.split(r"(?i)\bS\d+E\d+\b", title)[0].strip(" -") or title
        result["type"] = "episode"
    else:
        result["type"] = "movie"
    return result


class SubtitleCache:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "subtitles"

    def _record(self, channel_id):
        return self.directory / (hashlib.sha256(channel_id.encode()).hexdigest() + ".json")

    def remember(self, channel_id, path):
        path = Path(path)
        if path.parent != self.directory or not re.fullmatch(r"\d+\.srt", path.name):
            raise ValueError("Geçersiz altyazı yolu.")
        write_json(self._record(channel_id), {"file": path.name})

    def remembered(self, channel_id):
        record = read_json(self._record(channel_id))
        name = record.get("file") if isinstance(record, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"\d+\.srt", name):
            return None
        path = self.directory / name
        try:
            if not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= SRT_LIMIT:
                return path
        except OSError:
            pass
        return None


class OpenSubtitlesClient:
    def __init__(self, key, data_dir, username="", password="", *, fetcher=request):
        self.key, self.username, self.password = key.strip(), username.strip(), password
        self.fetcher = fetcher
        self.cache = SubtitleCache(data_dir)
        self.token = ""
        self.base = API

    def _api(self, endpoint, *, params=None, data=None):
        if not self.key:
            raise OnlineError()
        headers = {"Api-Key": self.key}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base}/{endpoint}"
        if params:
            url += "?" + urlencode(params)
        raw = self.fetcher(url, headers=headers, data=data)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, TypeError):
            raise OnlineError() from None

    def verify(self):
        if not self.key:
            return False
        return isinstance(self._api("infos/languages").get("data"), list)

    def search(self, channel, series_title="", info=None):
        if not self.key:
            return []
        params = episode_query(channel, series_title, info)
        if digest := file_hash(channel.url):
            params["moviehash"] = digest
        results = []
        seen = set()
        for language in ("tr", "en"):
            payload = self._api(
                "subtitles", params={**params, "languages": language, "order_by": "download_count"}
            )
            for item in payload.get("data", []):
                attributes = item.get("attributes", {})
                for file in attributes.get("files", []):
                    file_id = file.get("file_id")
                    if type(file_id) is not int or file_id <= 0 or file_id in seen:
                        continue
                    seen.add(file_id)
                    downloads = attributes.get("download_count", 0)
                    results.append(
                        Subtitle(
                            file_id,
                            str(attributes.get("language") or language),
                            str(attributes.get("release") or file.get("file_name") or "Altyazı"),
                            downloads if type(downloads) is int else 0,
                        )
                    )
        return results

    def _login(self):
        if self.token or not (self.username and self.password):
            return
        response = self._api("login", data={"username": self.username, "password": self.password})
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise OnlineError(401)
        host = response.get("base_url", "api.opensubtitles.com")
        if host not in {"api.opensubtitles.com", "vip-api.opensubtitles.com"}:
            raise OnlineError()
        self.token, self.base = token, f"https://{host}/api/v1"

    def download(self, subtitle):
        if not self.key or type(subtitle.file_id) is not int or subtitle.file_id <= 0:
            raise OnlineError()
        path = self.cache.directory / f"{subtitle.file_id}.srt"
        # Re-selecting a downloaded result must not consume another download.
        try:
            if not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= SRT_LIMIT:
                return path
        except OSError:
            pass
        self._login()
        try:
            response = self._api(
                "download", data={"file_id": subtitle.file_id, "sub_format": "srt"}
            )
        except OnlineError as exc:
            if exc.status == 401 and not self.token:
                raise OnlineErrorLogin() from None
            raise
        link = response.get("link", "")
        try:
            parts = urlsplit(link)
            valid = (
                parts.scheme == "https"
                and parts.hostname
                and not parts.username
                and (
                    parts.hostname == "opensubtitles.com"
                    or parts.hostname.endswith(".opensubtitles.com")
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise OnlineError()
        # Signed download URL needs no API key or login header.
        raw = self.fetcher(link, max_bytes=SRT_LIMIT)
        if not raw or len(raw) > SRT_LIMIT or b"-->" not in raw or b"\x00" in raw:
            raise OnlineError()
        write_bytes(path, raw)
        return path


class OnlineErrorLogin(OnlineError):
    def __init__(self):
        super().__init__(401)
        self.args = ("İndirmek için Ayarlar’da OpenSubtitles kullanıcı adı ve şifresini gir.",)
