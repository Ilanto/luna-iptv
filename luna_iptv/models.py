from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .accounts import AccountProfile


@dataclass
class Channel:
    id: str
    name: str
    url: str
    group: str = ""
    tvg_id: str = ""
    logo: str = ""
    kind: str = "live"
    series_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Playlist:
    channels: list[Channel]
    epg_urls: list[str]
    warnings: list[str]
    account_profile: AccountProfile | None = None


@dataclass
class Programme:
    channel_id: str
    title: str
    start: datetime
    end: datetime
    description: str
