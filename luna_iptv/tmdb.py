"""Opt-in TMDB enrichment with language-aware, offline-friendly disk caching."""

from __future__ import annotations

import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode

from .imdb import _key
from .imdb import clean_title as imdb_clean_title
from .media_details import normalize_info
from .online import OnlineError, read_json, request, write_json

API = "https://api.themoviedb.org/3"
TTL = 7 * 86400
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
_TAGS = re.compile(
    r"(?i)(?<!\w)(?:2160p|1080p|720p|480p|4k|uhd|fhd|hd|sd|tr|dual|multi|"
    r"bluray|blu-ray|web-dl|webrip|h\.?26[45]|x26[45])(?!\w)"
)


def clean_title(name, year=""):
    text = _TAGS.sub(" ", name)
    text = re.sub(r"[\[({]\s*[\])}]", " ", text)
    text, year = imdb_clean_title(text.strip(" -–:|"), year)
    match = re.search(r"(?:\(|\b)((?:19|20)\d{2})(?:\)|\b)\s*$", text)
    if match:
        year = year or match[1]
        text = text[: match.start()]
    return " ".join(text.strip(" -–:|[]()").split()), year


def pick(results, title, year=""):
    """Prefer exact localized/original titles and the requested release year."""
    wanted = _key(title)
    ranked = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
            continue
        names = [
            str(item.get(key) or "") for key in ("title", "original_title", "name", "original_name")
        ]
        similarity = max(SequenceMatcher(None, wanted, _key(name)).ratio() for name in names)
        exact = any(_key(name) == wanted for name in names)
        date = str(item.get("release_date") or item.get("first_air_date") or "")[:4]
        if not wanted or similarity < 0.65:
            continue
        ranked.append(((exact, bool(year and date == year), similarity), item))
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None


def trailer_url(key):
    return (
        f"https://www.youtube.com/watch?v={key}"
        if re.fullmatch(r"[\w-]{11}", key, re.ASCII)
        else ""
    )


def image_url(path, size):
    if isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", path):
        return f"https://image.tmdb.org/t/p/{size}{path}"
    return ""


def metadata(payload):
    credits = payload.get("credits") or {}
    directors = [p.get("name", "") for p in credits.get("crew", []) if p.get("job") == "Director"]
    creators = [p.get("name", "") for p in payload.get("created_by", [])]
    runtimes = payload.get("episode_run_time") or []
    info = normalize_info(
        {
            "overview": payload.get("overview"),
            "genres": [g.get("name", "") for g in payload.get("genres", [])],
            "runtime": payload.get("runtime") or (runtimes[0] if runtimes else None),
            "rating": payload.get("vote_average"),
            "rating_source": "tmdb",
            "cast": [p.get("name", "") for p in credits.get("cast", [])[:6]],
            "director": directors or creators,
            "year": payload.get("release_date") or payload.get("first_air_date"),
            "imdb_id": payload.get("imdb_id"),
        }
    )
    info["rating_source"] = "TMDB" if info.get("rating") else ""
    for key, size in (("poster", "w500"), ("backdrop", "w1280")):
        info[key] = image_url(payload.get(f"{key}_path"), size)
    videos = (payload.get("videos") or {}).get("results", [])
    trailers = [v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    trailers.sort(key=lambda v: not v.get("official", False))
    info["trailer"] = next(
        (url for v in trailers if (url := trailer_url(str(v.get("key", ""))))), ""
    )
    info["tmdb_attribution"] = ATTRIBUTION
    return {key: value for key, value in info.items() if value}


def merge(provider, extra):
    """Provider fields win; TMDB artwork is an explicitly separate presentation choice."""
    result = dict(extra)
    result.update({key: value for key, value in provider.items() if value})
    if provider.get("rating"):
        result.pop("rating_source", None)
        if provider.get("rating_source"):
            result["rating_source"] = provider["rating_source"]
    if extra.get("poster"):
        result["tmdb_poster"] = extra["poster"]
    return result


class TMDBClient:
    def __init__(self, key, data_dir, language="tr-TR", *, fetcher=request, clock=time.time):
        self.key = key.strip()
        self.directory = Path(data_dir) / "tmdb"
        self.language = language if language in ("tr-TR", "en-US") else "tr-TR"
        self.fetcher, self.clock = fetcher, clock

    def _api(self, endpoint, **params):
        if not self.key:
            raise OnlineError()
        params["api_key"] = self.key
        try:
            result = json.loads(self.fetcher(f"{API}/{endpoint}?{urlencode(params)}"))
            if not isinstance(result, dict) or result.get("success") is False:
                raise OnlineError()
            return result
        except (ValueError, TypeError):
            raise OnlineError() from None

    def verify(self):
        return bool(self.key) and self._api("authentication").get("success") is True

    def lookup(self, channel_id, title, kind="movie", year=""):
        if not self.key:
            return {}
        title, year = clean_title(title, year)
        if not title:
            return {}
        kind = "tv" if kind == "series" else "movie"
        identity = hashlib.sha256(json.dumps([channel_id, title, kind, year]).encode()).hexdigest()
        match_path = self.directory / f"match-{identity}.json"
        match = read_json(match_path)
        cached = None
        if isinstance(match, dict) and type(match.get("id")) is int:
            cached = read_json(self.directory / f"{kind}-{match['id']}-{self.language}.json")
        usable = (
            isinstance(cached, dict)
            and isinstance(cached.get("info"), dict)
            and all(isinstance(v, str) for v in cached["info"].values())
        )
        if usable and isinstance(cached.get("time"), (int, float)):
            if 0 <= self.clock() - cached["time"] < TTL:
                return cached["info"]
        try:
            # Refresh the match too once the corresponding details expire.
            params = {"query": title, "language": self.language}
            if year:
                params["first_air_date_year" if kind == "tv" else "year"] = year
            found = pick(self._api(f"search/{kind}", **params).get("results"), title, year)
            if not found:
                return cached["info"] if usable else {}
            payload = self._api(
                f"{kind}/{found['id']}", language=self.language, append_to_response="credits,videos"
            )
            if not payload.get("overview") and self.language != "en-US":
                try:
                    english = self._api(f"{kind}/{found['id']}", language="en-US")
                    payload["overview"] = english.get("overview")
                except OnlineError:
                    pass
            info = metadata(payload)
            try:
                write_json(
                    self.directory / f"{kind}-{found['id']}-{self.language}.json",
                    {"time": self.clock(), "info": info},
                )
                write_json(match_path, {"id": found["id"]})
            except OSError:
                pass
            return info
        except Exception:
            if usable:
                return cached["info"]
            raise OnlineError() from None
