"""Find a title's IMDb page: the page itself when the match is certain, else a search.

The lookup uses IMDb's public title-suggestion endpoint (the one behind its own
search box). It is undocumented, so every failure falls back to the search page.
Only the cleaned title and year are sent, and only when the person asks.
"""

from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import quote, urlencode

from .network import fetch

SUGGEST_URL = "https://v3.sg.media-imdb.com/suggestion/x/{}.json"
KINDS = {
    "movie": {"movie", "tvMovie", "video"},
    "series": {"tvSeries", "tvMiniSeries"},
}
_PREFIX = re.compile(r"^\s*(?:\[[^\]]{1,12}\]|[A-Z0-9]{2,4}\s*(?:[:|⇨>]|-\s))\s*")
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")


def clean_title(name: str, year: str = "") -> tuple[str, str]:
    """Strip provider decorations ("TR: ", "[4K] ", a trailing "(2024)") from a name."""
    text = name
    while (stripped := _PREFIX.sub("", text, count=1)) != text:
        text = stripped
    if match := _YEAR.search(text):
        year = year or match[1]
        text = text[: match.start()]
    year_digits = re.match(r"\d{4}", year or "")
    return text.strip(" -–:|"), year_digits[0] if year_digits else ""


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def title_url(imdb_id: str) -> str:
    return f"https://www.imdb.com/title/{imdb_id}/"


def search_url(title: str, year: str, kind: str) -> str:
    query = f"{title} {year}".strip()
    params = {"q": query, "s": "tt"}
    if kind == "movie":
        params["ttype"] = "ft"
    elif kind == "series":
        params["ttype"] = "tv"
    return "https://www.imdb.com/find/?" + urlencode(params)


def pick(payload: dict, title: str, year: str, kind: str) -> str | None:
    """The one suggestion that certainly is this title, or None."""
    wanted = _key(title)
    kinds = KINDS.get(kind, set().union(*KINDS.values()))
    matches = []
    for item in payload.get("d", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        imdb_id = item.get("id", "")
        if not re.fullmatch(r"tt[0-9]{7,}", str(imdb_id)) or item.get("qid") not in kinds:
            continue
        if _key(str(item.get("l", ""))) != wanted:
            continue
        found_year = item.get("y")
        if year and not (isinstance(found_year, int) and abs(found_year - int(year)) <= 1):
            continue
        matches.append(imdb_id)
    return matches[0] if len(matches) == 1 else None


def find(title: str, year: str, kind: str, fetcher=fetch) -> str:
    """URL to open for this title; never raises."""
    if not title:
        return search_url(title, year, kind)
    try:
        raw = fetcher(SUGGEST_URL.format(quote(_key(title) or title)), 512 * 1024)
        imdb_id = pick(json.loads(raw), title, year, kind)
    except Exception:
        imdb_id = None
    return title_url(imdb_id) if imdb_id else search_url(title, year, kind)
