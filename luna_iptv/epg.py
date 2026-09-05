from __future__ import annotations

import re
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
