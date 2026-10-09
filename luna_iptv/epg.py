from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from xml.etree import ElementTree
from xml.parsers import expat

from .models import Programme

_XMLTV_TIME = re.compile(r"^(\d{8,14})(?:\.(\d+))?(?:\s+([+-]\d{4}|Z))?$")


def _parse_time(value: str) -> datetime:
    match = _XMLTV_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid XMLTV timestamp: {value!r}")
    digits, fraction, offset = match.groups()
    formats = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}
    if len(digits) not in formats:
        raise ValueError(f"Invalid XMLTV timestamp: {value!r}")
    try:
        parsed = datetime.strptime(digits, formats[len(digits)])
    except ValueError as error:
        raise ValueError(f"Invalid XMLTV timestamp: {value!r}") from error
    if fraction:
        parsed = parsed.replace(microsecond=int((fraction + "000000")[:6]))
    if offset in (None, "Z"):
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = datetime.strptime(
            parsed.strftime("%Y%m%d%H%M%S") + offset, "%Y%m%d%H%M%S%z"
        ).replace(microsecond=parsed.microsecond)
    return parsed.astimezone(timezone.utc)


def _text(element: ElementTree.Element, name: str) -> str:
    child = element.find(name)
    return "" if child is None else "".join(child.itertext()).strip()


def parse_xmltv(data: bytes) -> list[Programme]:
    preflight = expat.ParserCreate()

    def reject_internal_subset(
        _name: str, _system_id: str | None, _public_id: str | None, has_internal_subset: bool
    ) -> None:
        if has_internal_subset:
            raise ValueError("XMLTV internal DTD subsets and entity declarations are not allowed")

    def reject_entity(*_args) -> None:
        raise ValueError("XMLTV entity declarations are not allowed")

    preflight.StartDoctypeDeclHandler = reject_internal_subset
    preflight.EntityDeclHandler = reject_entity
    try:
        preflight.Parse(data, True)
    except ValueError:
        raise
    except expat.ExpatError as error:
        raise ValueError(f"Invalid XMLTV document: {error}") from error
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError(f"Invalid XMLTV document: {error}") from error
    if root.tag != "tv":
        raise ValueError("Invalid XMLTV root element; expected tv")

    programmes: list[Programme] = []
    for element in root.findall("programme"):
        channel_id = element.get("channel", "").strip()
        start = element.get("start", "")
        end = element.get("stop", "")
        if not channel_id or not start or not end:
            continue
        programmes.append(
            Programme(
                channel_id=channel_id,
                title=_text(element, "title"),
                start=_parse_time(start),
                end=_parse_time(end),
                description=_text(element, "desc"),
            )
        )
    return programmes


def now_next(
    programmes: list[Programme], channel_id: str, when: datetime | None = None
) -> tuple[Programme | None, Programme | None]:
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must include a timezone")
    when = when.astimezone(timezone.utc)
    matching = sorted(
        (programme for programme in programmes if programme.channel_id == channel_id),
        key=lambda programme: programme.start,
    )
    current = next((item for item in matching if item.start <= when < item.end), None)
    following = next(
        (item for item in matching if item is not current and item.start >= when), None
    )
    return current, following


class GuideIndex:
    """Programmes grouped per channel and sorted by start, for lookups while painting."""

    def __init__(self, programmes=()):
        self._items = {}
        for programme in programmes:
            self._items.setdefault(programme.channel_id, []).append(programme)
        for items in self._items.values():
            items.sort(key=lambda programme: programme.start)
        self._starts = {key: [item.start for item in items] for key, items in self._items.items()}
        self._title_keys = {}  # titles repeat across days and channels

    def title_key(self, programme: Programme) -> str:
        """The programme title folded for searching, computed once per distinct title."""
        key = self._title_keys.get(programme.title)
        if key is None:
            from .library import search_key

            key = self._title_keys[programme.title] = search_key(programme.title)
        return key

    def search(self, key: str, start: datetime, end: datetime, channel_ids=None) -> list[Programme]:
        """Programmes overlapping [start, end) whose title contains ``key``, earliest first."""
        hits = []
        for channel_id in self._items if channel_ids is None else channel_ids:
            hits.extend(p for p in self.between(channel_id, start, end) if key in self.title_key(p))
        return sorted(hits, key=lambda p: p.start)

    def now(self, channel_id: str, when: datetime | None = None) -> Programme | None:
        items = self._items.get(channel_id)
        if not items:
            return None
        when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
        position = bisect_right(self._starts[channel_id], when) - 1
        if position >= 0 and when < items[position].end:
            return items[position]
        return None

    def channel_ids(self) -> set[str]:
        return set(self._items)

    def between(self, channel_id: str, start: datetime, end: datetime) -> list[Programme]:
        """Programmes overlapping [start, end), earliest first."""
        items = self._items.get(channel_id)
        if not items:
            return []
        starts = self._starts[channel_id]
        first = max(0, bisect_left(starts, start) - 1)
        last = bisect_left(starts, end)
        return [item for item in items[first:last] if item.end > start]

    def span(self) -> tuple[datetime, datetime] | None:
        """Earliest start and latest end in the guide."""
        if not self._items:
            return None
        return (
            min(items[0].start for items in self._items.values()),
            max(item.end for items in self._items.values() for item in items[-3:]),
        )

    def upcoming(
        self, channel_id: str, count: int, when: datetime | None = None
    ) -> list[Programme]:
        """Programmes starting after ``when``, earliest first."""
        items = self._items.get(channel_id, [])
        when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
        position = bisect_right(self._starts.get(channel_id, []), when)
        return items[position : position + count]
