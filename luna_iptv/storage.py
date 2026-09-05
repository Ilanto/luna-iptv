from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .accounts import AccountProfile, sanitize_profile
from .models import Channel, Playlist

_SOURCE_FIELDS = ("id", "name", "type", "location", "username", "password", "epg_url")


class Store:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        try:
            self._db = sqlite3.connect(self.path)
            os.chmod(self.path, 0o600)
            self._db.execute("PRAGMA foreign_keys = ON")
            self._create_schema()
            check = self._db.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                detail = "no result" if check is None else str(check[0])
                raise sqlite3.DatabaseError(detail)
        except sqlite3.DatabaseError as error:
            try:
                self._db.close()
            except (AttributeError, sqlite3.Error):
                pass
            raise RuntimeError(f"Database {self.path} is corrupt or unreadable: {error}") from error

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                location TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                epg_url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                group_name TEXT NOT NULL,
                tvg_id TEXT NOT NULL,
                logo TEXT NOT NULL,
                kind TEXT NOT NULL,
                series_id TEXT NOT NULL,
                headers TEXT NOT NULL,
                provider_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS channels_source_idx ON channels(source_id);
            CREATE TABLE IF NOT EXISTS favorites (
                channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS progress (
                channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
                position REAL NOT NULL,
                duration REAL NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS account_snapshots (
                source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                created_at INTEGER,
                expires_at INTEGER,
                active_connections INTEGER,
                max_connections INTEGER,
                checked_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_health (
                source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                checked_at INTEGER NOT NULL
            );
            """
        )
        progress_columns = {
            row[1] for row in self._db.execute("PRAGMA table_info(progress)").fetchall()
        }
        if "updated_at" not in progress_columns:
            self._db.execute(
                "ALTER TABLE progress ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"
            )
        channel_columns = {
            row[1] for row in self._db.execute("PRAGMA table_info(channels)").fetchall()
        }
        if "provider_key" not in channel_columns:
            self._db.execute(
                "ALTER TABLE channels ADD COLUMN provider_key TEXT NOT NULL DEFAULT ''"
            )
        self._backfill_all_provider_keys()
        self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS channels_provider_key_idx
            ON channels(source_id, provider_key) WHERE provider_key <> ''
            """
        )
        self._db.commit()

    def rename_source(self, source_id: str, name: str) -> bool:
        name = name.strip()
        if not name or any(unicodedata.category(c) == "Cc" for c in name):
            raise ValueError("Kaynak adı boş olamaz veya kontrol karakteri içeremez.")
        with self._db:
            return (
                self._db.execute("UPDATE sources SET name=? WHERE id=?", (name, source_id)).rowcount
                > 0
            )

    def save_source(self, source: dict[str, Any]) -> str:
        source_id = str(source.get("id") or uuid.uuid4())
        values = [str(source.get(field, "")) for field in _SOURCE_FIELDS[1:]]
        with self._db:
            self._db.execute(
                """
                INSERT INTO sources(id,name,type,location,username,password,epg_url)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, type=excluded.type, location=excluded.location,
                  username=excluded.username, password=excluded.password, epg_url=excluded.epg_url
                """,
                [source_id, *values],
            )
        return source_id

    def sources(self) -> list[dict[str, str]]:
        rows = self._db.execute(
            "SELECT id,name,type,location,username,password,epg_url FROM sources ORDER BY rowid"
        ).fetchall()
        return [dict(zip(_SOURCE_FIELDS, row, strict=True)) for row in rows]

    @staticmethod
    def _stored_id(source_id: str, channel_id: str) -> str:
        prefix = f"{source_id}:"
        return channel_id if channel_id.startswith(prefix) else prefix + channel_id

    def replace_channels(self, source_id: str, channels: list[Channel]) -> list[Channel]:
        with self._db:
            channels = self._reconcile_provider_channels(source_id, channels)
            rows = self._channel_rows(source_id, channels)
            incoming_ids = {row[0] for row in rows}
            self._upsert_channel_rows(rows)
            existing = self._db.execute(
                "SELECT id FROM channels WHERE source_id = ?", (source_id,)
            ).fetchall()
            removed = [
                (channel_id,) for (channel_id,) in existing if channel_id not in incoming_ids
            ]
            self._db.executemany("DELETE FROM channels WHERE id = ?", removed)
        return self._channels_from_rows(rows)

    def upsert_channels(self, source_id: str, channels: list[Channel]) -> list[Channel]:
        with self._db:
            channels = self._reconcile_provider_channels(source_id, channels)
            rows = self._channel_rows(source_id, channels)
            self._upsert_channel_rows(rows)
        return self._channels_from_rows(rows)

    def _channel_rows(self, source_id: str, channels: list[Channel]) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        incoming_ids: set[str] = set()
        for channel in channels:
            stored_id = self._stored_id(source_id, channel.id)
            if stored_id in incoming_ids:
                raise ValueError(f"Duplicate channel id in source {source_id}: {channel.id}")
            incoming_ids.add(stored_id)
            rows.append(
                (
                    stored_id,
                    source_id,
                    channel.name,
                    channel.url,
                    channel.group,
                    channel.tvg_id,
                    channel.logo,
                    channel.kind,
                    channel.series_id,
                    json.dumps(channel.headers, ensure_ascii=False, sort_keys=True),
                    channel.provider_key,
                )
            )
        return rows

    def _upsert_channel_rows(self, rows: list[tuple[Any, ...]]) -> None:
        self._db.executemany(
            """
            INSERT INTO channels(
                id,source_id,name,url,group_name,tvg_id,logo,kind,series_id,headers,provider_key
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id, name=excluded.name,
              url=excluded.url, group_name=excluded.group_name, tvg_id=excluded.tvg_id,
              logo=excluded.logo, kind=excluded.kind, series_id=excluded.series_id,
              headers=excluded.headers, provider_key=excluded.provider_key
            """,
            rows,
        )

    def channels(self, source_id: str | None = None) -> list[Channel]:
        sql = (
            "SELECT id,name,url,group_name,tvg_id,logo,kind,series_id,headers,provider_key "
            "FROM channels"
        )
        parameters: tuple[str, ...] = ()
        if source_id is not None:
            sql += " WHERE source_id = ?"
            parameters = (source_id,)
        sql += " ORDER BY rowid"
        return [
            Channel(
                id=row[0],
                name=row[1],
                url=row[2],
                group=row[3],
                tvg_id=row[4],
                logo=row[5],
                kind=row[6],
                series_id=row[7],
                headers=json.loads(row[8]),
                provider_key=row[9],
            )
            for row in self._db.execute(sql, parameters)
        ]

    @staticmethod
    def _channels_from_rows(rows: list[tuple[Any, ...]]) -> list[Channel]:
        return [
            Channel(
                id=row[0],
                name=row[2],
                url=row[3],
                group=row[4],
                tvg_id=row[5],
                logo=row[6],
                kind=row[7],
                series_id=row[8],
                headers=json.loads(row[9]),
                provider_key=row[10],
            )
            for row in rows
        ]

    @staticmethod
    def _legacy_provider_key(channel: Channel) -> str:
        from .source_connections import episode_identity

        if channel.kind == "series" and channel.series_id:
            return f"series:{quote(channel.series_id, safe='')}"
        episode = episode_identity(channel)
        if episode is not None:
            return f"episode:{quote(episode[0], safe='')}:{quote(episode[1], safe='')}"
        path = urlsplit(channel.url).path if channel.url else ""
        if not path:
            return ""
        marker = "/live/" if channel.kind == "live" else "/movie/"
        if marker not in path:
            return ""
        item = unquote(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        return f"{channel.kind}:{quote(item, safe='')}" if item else ""

    def _backfill_all_provider_keys(self) -> None:
        xtream_ids = {
            row[0] for row in self._db.execute("SELECT id FROM sources WHERE type='xtream'")
        }
        for source_id in xtream_ids:
            self._backfill_provider_keys(source_id)

    def _backfill_provider_keys(self, source_id: str) -> None:
        source_type = self._db.execute(
            "SELECT type FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        if source_type is None or source_type[0] != "xtream":
            return
        rows = self._db.execute(
            """
            SELECT id,name,url,group_name,tvg_id,logo,kind,series_id,headers,provider_key
            FROM channels WHERE source_id=? ORDER BY rowid
            """,
            (source_id,),
        ).fetchall()
        used = {row[9] for row in rows if row[9]}
        for row in rows:
            if row[9]:
                continue
            channel = Channel(
                id=row[0],
                name=row[1],
                url=row[2],
                group=row[3],
                tvg_id=row[4],
                logo=row[5],
                kind=row[6],
                series_id=row[7],
                headers=json.loads(row[8]),
            )
            key = self._legacy_provider_key(channel)
            if key and key not in used:
                self._db.execute("UPDATE channels SET provider_key=? WHERE id=?", (key, row[0]))
                used.add(key)

    def _reconcile_provider_channels(
        self, source_id: str, channels: list[Channel]
    ) -> list[Channel]:
        from dataclasses import replace

        self._backfill_provider_keys(source_id)
        existing = dict(
            self._db.execute(
                "SELECT provider_key,id FROM channels WHERE source_id=? AND provider_key<>''",
                (source_id,),
            ).fetchall()
        )
        seen: set[str] = set()
        result = []
        for channel in channels:
            if channel.provider_key:
                if channel.provider_key in seen:
                    raise ValueError("Duplicate provider channel identity")
                seen.add(channel.provider_key)
                channel = replace(channel, id=existing.get(channel.provider_key, channel.id))
            result.append(channel)
        return result

    def apply_source_connection(
        self,
        expected_source: dict[str, Any],
        candidate_source: dict[str, Any],
        playlist: Playlist,
    ) -> bool:
        """Compare-and-swap connection metadata and its prepared catalogue atomically."""

        from .source_connections import retarget_cached_episodes

        if not playlist.channels:
            raise ValueError("Candidate catalogue is empty")
        source_id = str(expected_source.get("id", ""))
        if (
            not source_id
            or candidate_source.get("id") != source_id
            or candidate_source.get("type") != expected_source.get("type")
        ):
            raise ValueError("Source identity or type cannot change")
        expected = tuple(str(expected_source.get(field, "")) for field in _SOURCE_FIELDS)
        candidate = dict(candidate_source)
        if not candidate.get("epg_url") and playlist.epg_urls:
            candidate["epg_url"] = playlist.epg_urls[0]
        values = tuple(str(candidate.get(field, "")) for field in _SOURCE_FIELDS)
        profile = (
            sanitize_profile(playlist.account_profile)
            if playlist.account_profile is not None
            else None
        )
        if profile is not None and profile.checked_at is None:
            raise ValueError("Account profile check timestamp is invalid")

        with self._db:
            current = self._db.execute(
                "SELECT id,name,type,location,username,password,epg_url FROM sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if current != expected:
                return False
            channels = self._reconcile_provider_channels(source_id, list(playlist.channels))
            if candidate["type"] == "xtream":
                series_ids = {c.series_id for c in channels if c.kind == "series"}
                cached = [
                    channel
                    for channel in self.channels(source_id)
                    if channel.kind != "series" and channel.series_id in series_ids
                ]
                channels.extend(retarget_cached_episodes(candidate, cached))
                channels = self._reconcile_provider_channels(source_id, channels)
            rows = self._channel_rows(source_id, channels)
            incoming_ids = {row[0] for row in rows}
            self._db.execute(
                """
                UPDATE sources SET name=?,type=?,location=?,username=?,password=?,epg_url=?
                WHERE id=?
                """,
                (*values[1:], source_id),
            )
            self._db.execute("DELETE FROM source_health WHERE source_id=?", (source_id,))
            if profile is not None:
                self._save_account_profile_row(source_id, profile)
            self._upsert_channel_rows(rows)
            existing_ids = self._db.execute(
                "SELECT id FROM channels WHERE source_id=?", (source_id,)
            ).fetchall()
            self._db.executemany(
                "DELETE FROM channels WHERE id=?",
                [(item_id,) for (item_id,) in existing_ids if item_id not in incoming_ids],
            )
        return True

    def set_favorite(self, channel_id: str, favorite: bool) -> None:
        with self._db:
            if favorite:
                self._db.execute(
                    "INSERT OR IGNORE INTO favorites(channel_id) VALUES(?)", (channel_id,)
                )
            else:
                self._db.execute("DELETE FROM favorites WHERE channel_id = ?", (channel_id,))

    def favorites(self) -> set[str]:
        return {row[0] for row in self._db.execute("SELECT channel_id FROM favorites")}

    def save_progress(self, channel_id: str, position: float, duration: float) -> None:
        with self._db:
            updated_at = self._db.execute(
                "SELECT COALESCE(MAX(updated_at), 0) + 1 FROM progress"
            ).fetchone()[0]
            self._db.execute(
                """
                INSERT INTO progress(channel_id,position,duration,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET position=excluded.position,
                  duration=excluded.duration, updated_at=excluded.updated_at
                """,
                (channel_id, float(position), float(duration), updated_at),
            )

    def progress(self, channel_id: str) -> tuple[float, float]:
        row = self._db.execute(
            "SELECT position,duration FROM progress WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return (0.0, 0.0) if row is None else (float(row[0]), float(row[1]))

    def recent_ids(self, limit: int = 50) -> list[str]:
        if limit <= 0:
            return []
        return [
            row[0]
            for row in self._db.execute(
                """
                SELECT channel_id FROM progress
                WHERE position >= 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        ]

    def save_account_profile(self, source_id: str, profile: AccountProfile) -> None:
        profile = sanitize_profile(profile)
        if profile.checked_at is None:
            raise ValueError("Account profile check timestamp is invalid")
        with self._db:
            self._save_account_profile_row(source_id, profile)

    def _save_account_profile_row(self, source_id: str, profile: AccountProfile) -> None:
        self._db.execute(
            """
            INSERT INTO account_snapshots(
                source_id,status,created_at,expires_at,
                active_connections,max_connections,checked_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              status=excluded.status, created_at=excluded.created_at,
              expires_at=excluded.expires_at,
              active_connections=excluded.active_connections,
              max_connections=excluded.max_connections,
              checked_at=excluded.checked_at
            """,
            (
                source_id,
                profile.status,
                profile.created_at,
                profile.expires_at,
                profile.active_connections,
                profile.max_connections,
                profile.checked_at,
            ),
        )

    def account_profile(self, source_id: str) -> AccountProfile | None:
        row = self._db.execute(
            """
            SELECT status,created_at,expires_at,active_connections,max_connections,checked_at
            FROM account_snapshots WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        return sanitize_profile(AccountProfile(*row)) if row is not None else None

    def save_source_health(self, source_id: str, status: str, checked_at: int) -> bool:
        from .accounts import bounded_timestamp

        if status not in {"available", "responding", "unverified", "unavailable"}:
            raise ValueError("Unknown source health status")
        checked_at = bounded_timestamp(checked_at)
        if checked_at is None:
            raise ValueError("Invalid source health timestamp")
        with self._db:
            return (
                self._db.execute(
                    """
                    INSERT INTO source_health(source_id,status,checked_at)
                    SELECT id,?,? FROM sources WHERE id=?
                    ON CONFLICT(source_id) DO UPDATE SET
                      status=excluded.status,checked_at=excluded.checked_at
                    """,
                    (status, int(checked_at), source_id),
                ).rowcount
                > 0
            )

    def source_health(self, source_id: str) -> tuple[str, int] | None:
        row = self._db.execute(
            "SELECT status,checked_at FROM source_health WHERE source_id=?", (source_id,)
        ).fetchone()
        return (str(row[0]), int(row[1])) if row is not None else None

    def remove_source(self, source_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def close(self) -> None:
        self._db.close()
