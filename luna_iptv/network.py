"""Bounded provider requests. Errors deliberately omit credential-bearing URLs."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .accounts import AccountProfile, normalize_profile
from .catchup import archive_days
from .media_details import MediaDetails, normalize_info
from .models import Channel, Playlist
from .playlist import parse_m3u, resolve_logo

LIMIT = 64 * 1024 * 1024


class NetworkError(ValueError):
    pass


def http_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NetworkError("Geçerli bir HTTP veya HTTPS adresi girin.")
    return urlunsplit(parts)


def fetch(url: str, max_bytes: int = LIMIT, *, with_url: bool = False):
    url = http_url(url)
    try:
        request = Request(url, headers={"User-Agent": "Luna-IPTV/0.1", "Accept-Encoding": "gzip"})
        with urlopen(request, timeout=20) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise NetworkError("Kaynak boyut sınırını aşıyor (64 MB).")
            if raw.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                    raw = zipped.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise NetworkError("Açılmış kaynak boyut sınırını aşıyor.")
            return (raw, response.geturl()) if with_url else raw
    except HTTPError as exc:
        raise NetworkError(
            f"Sunucu HTTP {exc.code} döndürdü. Hesabı ve kaynak adresini kontrol edin."
        ) from None
    except (URLError, OSError, EOFError, ValueError) as exc:
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError("Kaynağa erişilemedi. Ağ bağlantısını ve adresi kontrol edin.") from None


def load_m3u(location: str) -> Playlist:
    if urlsplit(location).scheme in ("http", "https"):
        raw, base = fetch(location, with_url=True)
    else:
        if urlsplit(location).scheme and urlsplit(location).scheme != "file":
            raise NetworkError("M3U için yerel dosya veya HTTP(S) adresi kullanın.")
        from urllib.request import url2pathname

        path = (
            Path(
                url2pathname(urlsplit(location).path) if location.startswith("file:") else location
            )
            .expanduser()
            .resolve()
        )
        try:
            with path.open("rb") as file:
                raw = file.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise NetworkError("Liste boyut sınırını aşıyor (64 MB).")
        except OSError:
            raise NetworkError("Liste dosyası okunamadı.") from None
        base = path.as_uri()
    return parse_m3u(raw.decode("utf-8-sig", errors="replace"), base)


def channel_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def provider_channel_id(provider_key: str) -> str:
    return channel_id("xtream:" + provider_key)


class XtreamClient:
    def __init__(self, url: str, username: str, password: str):
        self.base = http_url(url).rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.query or parsed.fragment or parsed.username:
            raise NetworkError(
                "Sunucu adresine yalnızca ana adresi yazın; kullanıcı ve şifreyi ayrı girin."
            )
        self.username, self.password = username, password
        if not username or not password:
            raise NetworkError("Kullanıcı adı ve şifre gerekli.")

    def _api(self, action: str = "", **params):
        query = {"username": self.username, "password": self.password, **params}
        if action:
            query["action"] = action
        try:
            return json.loads(fetch(f"{self.base}/player_api.php?{urlencode(query)}"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise NetworkError("Sağlayıcı geçerli JSON yanıtı vermedi.") from None

    def stream_url(self, kind: str, item_id, extension="ts") -> str:
        extension = str(extension)
        if not extension.isalnum() or len(extension) > 8:
            extension = "ts" if kind == "live" else "mp4"
        return f"{self.base}/{kind}/{quote(self.username, safe='')}/{quote(self.password, safe='')}/{quote(str(item_id), safe='')}.{extension}"

    def timeshift_url(self, stream_id, start, duration_minutes):
        """Build an archive request using the programme's supplied timezone."""
        if start.utcoffset() is None or duration_minutes <= 0:
            raise ValueError("Geçerli arşiv zamanı ve süresi gerekli.")
        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        stream = quote(str(stream_id), safe="")
        return (
            f"{self.base}/timeshift/{user}/{password}/{int(duration_minutes)}/"
            f"{start:%Y-%m-%d:%H-%M}/{stream}.ts"
        )

    def _list(self, action):
        data = self._api(action)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise NetworkError("Sağlayıcının katalog biçimi desteklenmiyor.")
        return data

    @staticmethod
    def _profile(response: object) -> AccountProfile:
        user_info = response.get("user_info") if isinstance(response, dict) else None
        if not isinstance(user_info, dict):
            raise NetworkError("Sağlayıcının hesap profil biçimi desteklenmiyor.")
        return normalize_profile(response)

    def account_info(self) -> AccountProfile:
        return self._profile(self._api())

    def catalog(self) -> Playlist:
        account = self._api()
        user_info = account.get("user_info") if isinstance(account, dict) else None
        if not isinstance(user_info, dict) or str(user_info.get("auth", 0)) != "1":
            raise NetworkError("Oturum açılamadı. Kullanıcı adı ve şifreyi kontrol edin.")
        account_profile = self._profile(account)
        channels = []
        for mode, api, kind in [
            ("live", "get_live_streams", "live"),
            ("vod", "get_vod_streams", "movie"),
            ("series", "get_series", "series"),
        ]:
            categories = {
                str(c.get("category_id")): str(c.get("category_name", ""))
                for c in self._list(f"get_{mode}_categories")
            }
            for row in self._list(api):
                item_id = row.get("series_id" if mode == "series" else "stream_id")
                if item_id is None or str(item_id).strip() == "":
                    continue
                url = (
                    ""
                    if mode == "series"
                    else self.stream_url(
                        "movie" if mode == "vod" else mode,
                        item_id,
                        row.get("container_extension") or ("ts" if mode == "live" else "mp4"),
                    )
                )
                provider_key = f"{kind}:{quote(str(item_id), safe='')}"
                channels.append(
                    Channel(
                        id=provider_channel_id(provider_key),
                        name=str(row.get("name") or "İsimsiz yayın"),
                        url=url,
                        group=categories.get(str(row.get("category_id")), "Diğer"),
                        tvg_id=str(row.get("epg_channel_id") or ""),
                        logo=resolve_logo(
                            str(row.get("stream_icon") or row.get("cover") or ""), self.base + "/"
                        ),
                        kind=kind,
                        series_id=str(item_id) if mode == "series" else "",
                        provider_key=provider_key,
                        tv_archive=mode == "live" and str(row.get("tv_archive")) == "1",
                        tv_archive_duration=(
                            archive_days(row.get("tv_archive_duration")) if mode == "live" else 0
                        ),
                    )
                )
        return Playlist(
            channels=channels,
            epg_urls=[self.epg_url()],
            warnings=[],
            account_profile=account_profile,
        )

    def episodes(self, series_id: str) -> list[Channel]:
        response = self._api("get_series_info", series_id=series_id)
        return [
            self._episode_channel(
                season,
                row,
                series_id,
                normalize_info(row.get("info"), row, base_url=self.base + "/"),
            )
            for season, row in self._episode_rows(response)
        ]

    def media_details(self, channel: Channel) -> MediaDetails:
        from .source_connections import episode_identity

        episode = episode_identity(channel)
        if channel.kind == "series" or episode is not None:
            series_id = episode[0] if episode else channel.series_id
            if not series_id and channel.provider_key.startswith("series:"):
                series_id = unquote(channel.provider_key.split(":", 1)[1])
            if not series_id:
                raise NetworkError("Dizi kimliği bulunamadı.")
            response = self._api("get_series_info", series_id=series_id)
            self._validate_detail_response(response, "Dizi bilgisi alınamadı.")
            channels = []
            episode_info = {}
            info = normalize_info(response.get("info"), base_url=self.base + "/")
            for season, row in self._episode_rows(response):
                item_info = normalize_info(row.get("info"), row, base_url=self.base + "/")
                item = self._episode_channel(season, row, series_id, item_info)
                channels.append(item)
                episode_info[item.provider_key] = item_info
            series_info = response.get("info")
            title = series_info.get("name") if isinstance(series_info, dict) else None
            series_title = title.strip() if isinstance(title, str) else ""
            if not series_title and channel.kind == "series":
                series_title = channel.name
            return MediaDetails(info, channels, episode_info, series_title)
        if channel.kind != "movie":
            raise NetworkError("Bu yayın için medya bilgisi desteklenmiyor.")
        vod_id = ""
        if channel.provider_key.startswith("movie:"):
            vod_id = unquote(channel.provider_key.split(":", 1)[1])
        elif not channel.provider_key:
            try:
                path = urlsplit(channel.url).path
            except ValueError:
                path = ""
            if "/movie/" in path:
                vod_id = unquote(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        if not vod_id:
            raise NetworkError("Film kimliği bulunamadı.")
        response = self._api("get_vod_info", vod_id=vod_id)
        self._validate_detail_response(response, "Film bilgisi alınamadı.")
        if not any(isinstance(response.get(key), dict) for key in ("info", "movie_data")):
            raise NetworkError("Film bilgisi alınamadı.")
        return MediaDetails(
            normalize_info(
                response.get("info"), response.get("movie_data"), base_url=self.base + "/"
            )
        )

    @staticmethod
    def _validate_detail_response(response: object, message: str) -> None:
        """Reject provider failure envelopes without exposing their remote messages."""
        if not isinstance(response, dict):
            raise NetworkError(message)
        for envelope in (response, response.get("user_info")):
            if not isinstance(envelope, dict):
                continue
            if any(envelope.get(key) for key in ("error", "errors")):
                raise NetworkError(message)
            for key in ("auth", "success", "status"):
                marker = envelope.get(key)
                if isinstance(marker, (str, int, float, bool)) and str(
                    marker
                ).strip().casefold() in {
                    "0",
                    "0.0",
                    "false",
                    "no",
                    "error",
                    "failed",
                    "failure",
                    "denied",
                    "unauthorized",
                    "forbidden",
                    "expired",
                    "disabled",
                    "inactive",
                    "banned",
                }:
                    raise NetworkError(message)

    @staticmethod
    def _episode_rows(response: dict):
        XtreamClient._validate_detail_response(response, "Dizi bölüm bilgisi alınamadı.")
        if not isinstance(response, dict) or not isinstance(response.get("episodes"), (dict, list)):
            raise NetworkError("Dizi bölüm bilgisi alınamadı.")
        seasons = response["episodes"]
        if isinstance(seasons, list):
            grouped = {}
            for index, rows in enumerate(seasons):
                if isinstance(rows, dict):
                    season = rows.get("season", 0)
                    season = str(season) if isinstance(season, (str, int)) else "0"
                    grouped.setdefault(season, []).append(rows)
                elif isinstance(rows, list):
                    grouped.setdefault(str(index), []).extend(rows)
            seasons = grouped
        seen = set()
        for season, rows in sorted(
            seasons.items(),
            key=lambda pair: (
                int(pair[0]) if str(pair[0]).isascii() and str(pair[0]).isdigit() else 9999
            ),
        ):
            if not isinstance(rows, list):
                continue
            for row in sorted(
                (r for r in rows if isinstance(r, dict)),
                key=lambda r: (
                    int(r.get("episode_num") or 0)
                    if str(r.get("episode_num") or 0).isascii()
                    and str(r.get("episode_num") or 0).isdigit()
                    else 0
                ),
            ):
                item_id = row.get("id")
                if (
                    not isinstance(item_id, (str, int))
                    or isinstance(item_id, bool)
                    or not str(item_id).strip()
                    or str(item_id) in seen
                ):
                    continue
                seen.add(str(item_id))
                yield season, row

    def _episode_channel(
        self, season: str, row: dict, series_id: str, info: dict[str, str]
    ) -> Channel:
        provider_key = f"episode:{quote(str(series_id), safe='')}:{quote(str(row['id']), safe='')}"
        url = self.stream_url("series", row["id"], row.get("container_extension") or "mp4")
        title = row.get("title")
        number = row.get("episode_num", "")
        if not isinstance(number, (str, int)):
            number = ""
        return Channel(
            id=provider_channel_id(provider_key),
            name=title if isinstance(title, str) and title.strip() else f"Bölüm {number}",
            url=url,
            group=f"Sezon {season}",
            logo=info.get("poster", ""),
            kind="movie",
            series_id=series_id,
            provider_key=provider_key,
        )

    def epg_url(self) -> str:
        return f"{self.base}/xmltv.php?{urlencode({'username': self.username, 'password': self.password})}"
