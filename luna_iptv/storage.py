from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .accounts import AccountProfile, bounded_timestamp, sanitize_profile
from .media_details import MediaDetails, normalize_info
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
            CREATE TABLE IF NOT EXISTS favorite_folders (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                position INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favorite_folder_items (
                folder_id INTEGER REFERENCES favorite_folders(id) ON DELETE CASCADE,
                channel_id TEXT NOT NULL,
                PRIMARY KEY(folder_id, channel_id)
            );
            CREATE TRIGGER IF NOT EXISTS favorite_folder_cleanup
            AFTER DELETE ON favorites BEGIN
                DELETE FROM favorite_folder_items WHERE channel_id = OLD.channel_id;
            END;
            CREATE TABLE IF NOT EXISTS progress (
                channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
                position REAL NOT NULL,
                duration REAL NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0,
                history_hidden INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS media_detail_cache (
                channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
                fingerprint TEXT NOT NULL,
                data TEXT NOT NULL,
                checked_at INTEGER NOT NULL
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
            CREATE TABLE IF NOT EXISTS playback_preferences (
                source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                lead_minutes REAL NOT NULL DEFAULT 5,
                UNIQUE(channel_id, start)
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
        if "history_hidden" not in progress_columns:
            self._db.execute(
                "ALTER TABLE progress ADD COLUMN history_hidden INTEGER NOT NULL DEFAULT 0"
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

    def setting(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError, RecursionError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False)
        with self._db:
            self._db.execute(
                """INSERT INTO app_settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, data),
            )

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

        source_type = self._db.execute(
            "SELECT type FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        if source_type is not None and source_type[0] == "direct" and len(channels) == 1:
            existing = self._db.execute(
                "SELECT id FROM channels WHERE source_id=?", (source_id,)
            ).fetchall()
            if len(existing) == 1:
                return [replace(channels[0], id=existing[0][0])]
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

    def media_details(self, channel_id: str, fingerprint: str) -> tuple[MediaDetails, int] | None:
        row = self._db.execute(
            "SELECT data,checked_at FROM media_detail_cache WHERE channel_id=? AND fingerprint=?",
            (channel_id, fingerprint),
        ).fetchone()
        if row is None:
            return None
        checked_at = bounded_timestamp(row[1])
        if checked_at is None:
            return None
        try:
            payload = json.loads(row[0])
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("info"), dict)
                or not isinstance(payload.get("episodes"), list)
                or not isinstance(payload.get("episode_info"), dict)
                or not isinstance(payload.get("series_title"), str)
                or any(not isinstance(value, str) for value in payload["info"].values())
                or any(
                    not isinstance(info, dict)
                    or any(not isinstance(value, str) for value in info.values())
                    for info in payload["episode_info"].values()
                )
            ):
                return None
            episodes = []
            channel_fields = {field.name for field in fields(Channel)}
            for item in payload["episodes"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != channel_fields
                    or any(
                        not isinstance(value, str)
                        for key, value in item.items()
                        if key != "headers"
                    )
                    or not isinstance(item["headers"], dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, str)
                        for key, value in item["headers"].items()
                    )
                    or not item["id"]
                    or not item["url"]
                    or item["kind"] != "movie"
                    or not item["series_id"]
                ):
                    return None
                episodes.append(Channel(**item))
            return MediaDetails(
                normalize_info(payload["info"]),
                episodes,
                {key: normalize_info(info) for key, info in payload["episode_info"].items()},
                payload["series_title"],
            ), checked_at
        except (ValueError, TypeError, KeyError, RecursionError):
            return None

    def save_media_details(
        self, channel_id: str, fingerprint: str, details: MediaDetails, checked_at: int
    ) -> None:
        checked_at = bounded_timestamp(checked_at)
        if checked_at is None:
            raise ValueError("Invalid media details timestamp")
        data = json.dumps(
            {
                "info": normalize_info(details.info),
                "episodes": [asdict(channel) for channel in details.episodes],
                "episode_info": {
                    key: normalize_info(info) for key, info in details.episode_info.items()
                },
                "series_title": details.series_title,
            },
            ensure_ascii=False,
        )
        with self._db:
            self._db.execute(
                """
                INSERT INTO media_detail_cache(channel_id,fingerprint,data,checked_at)
                VALUES(?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                  data=excluded.data, checked_at=excluded.checked_at
                """,
                (channel_id, fingerprint, data, checked_at),
            )

    def set_favorite(self, channel_id: str, favorite: bool) -> None:
        with self._db:
            if favorite:
                self._db.execute(
                    "INSERT OR IGNORE INTO favorites(channel_id) VALUES(?)", (channel_id,)
                )
            else:
                self._db.execute("DELETE FROM favorites WHERE channel_id = ?", (channel_id,))
                self._db.execute(
                    "DELETE FROM favorite_folder_items WHERE channel_id = ?", (channel_id,)
                )

    def favorites(self) -> set[str]:
        return {row[0] for row in self._db.execute("SELECT channel_id FROM favorites")}

    def folders(self) -> list[tuple[int, str]]:
        return self._db.execute(
            "SELECT id,name FROM favorite_folders ORDER BY position,id"
        ).fetchall()

    def _folder_name(self, name: str, folder_id: int | None = None) -> str:
        if not name.strip() or any(unicodedata.category(c) == "Cc" for c in name):
            raise ValueError("Klasör adı boş olamaz veya kontrol karakteri içeremez.")
        name = name.strip()
        if any(
            other_id != folder_id and other_name.casefold() == name.casefold()
            for other_id, other_name in self.folders()
        ):
            raise ValueError("Bu adda bir klasör zaten var.")
        return name

    def create_folder(self, name: str) -> int:
        with self._db:
            name = self._folder_name(name)
            return self._db.execute(
                """INSERT INTO favorite_folders(name,position)
                SELECT ?,COALESCE(MAX(position), -1) + 1 FROM favorite_folders""",
                (name,),
            ).lastrowid

    def rename_folder(self, folder_id: int, name: str) -> bool:
        with self._db:
            name = self._folder_name(name, folder_id)
            return (
                self._db.execute(
                    "UPDATE favorite_folders SET name=? WHERE id=?", (name, folder_id)
                ).rowcount
                > 0
            )

    def delete_folder(self, folder_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM favorite_folders WHERE id=?", (folder_id,))

    def folder_items(self, folder_id: int) -> set[str]:
        return {
            row[0]
            for row in self._db.execute(
                "SELECT channel_id FROM favorite_folder_items WHERE folder_id=?", (folder_id,)
            )
        }

    def folders_of(self, channel_id: str) -> set[int]:
        return {
            row[0]
            for row in self._db.execute(
                "SELECT folder_id FROM favorite_folder_items WHERE channel_id=?", (channel_id,)
            )
        }

    def set_in_folder(self, folder_id: int, channel_id: str, member: bool) -> None:
        with self._db:
            if member:
                self._db.execute(
                    "INSERT OR IGNORE INTO favorites(channel_id) VALUES(?)", (channel_id,)
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO favorite_folder_items(folder_id,channel_id) VALUES(?,?)",
                    (folder_id, channel_id),
                )
            else:
                self._db.execute(
                    "DELETE FROM favorite_folder_items WHERE folder_id=? AND channel_id=?",
                    (folder_id, channel_id),
                )

    def save_progress(
        self, channel_id: str, position: float, duration: float, *, mark_recent: bool = True
    ) -> None:
        with self._db:
            updated_at = self._db.execute(
                "SELECT COALESCE(MAX(updated_at), 0) + 1 FROM progress"
            ).fetchone()[0]
            self._db.execute(
                """
                INSERT INTO progress(channel_id,position,duration,updated_at,history_hidden)
                VALUES(?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET position=excluded.position,
                  duration=excluded.duration, updated_at=excluded.updated_at,
                  history_hidden=excluded.history_hidden
                """,
                (channel_id, float(position), float(duration), updated_at, int(not mark_recent)),
            )

    def progress(self, channel_id: str) -> tuple[float, float]:
        row = self._db.execute(
            "SELECT position,duration FROM progress WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return (0.0, 0.0) if row is None else (float(row[0]), float(row[1]))

    def progress_map(self) -> dict[str, tuple[float, float]]:
        """Every saved position with a known duration, for painting without a query per card."""
        return {
            row[0]: (float(row[1]), float(row[2]))
            for row in self._db.execute(
                "SELECT channel_id,position,duration FROM progress WHERE duration > 0"
            )
        }

    def recent_ids(self, limit: int = 50) -> list[str]:
        if limit <= 0:
            return []
        return [
            row[0]
            for row in self._db.execute(
                """
                SELECT channel_id FROM progress
                WHERE position >= 0 AND history_hidden = 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        ]

    def clear_history(self, source_id: str | None = None, *, reset_progress: bool = False) -> None:
        assignments = "history_hidden = 1"
        if reset_progress:
            assignments += ", position = 0, duration = 0"
        sql = f"UPDATE progress SET {assignments}"
        args = ()
        if source_id is not None:
            sql += " WHERE channel_id IN (SELECT id FROM channels WHERE source_id = ?)"
            args = (source_id,)
        with self._db:
            self._db.execute(sql, args)

    def playback_preferences(self, source_id: str) -> dict:
        from .preferences import normalize_preferences

        row = self._db.execute(
            "SELECT data FROM playback_preferences WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return {}
        try:
            return normalize_preferences(json.loads(row[0]))
        except (ValueError, TypeError):
            return {}

    def save_playback_preferences(self, source_id: str, preferences: dict) -> None:
        from .preferences import normalize_preferences

        data = json.dumps(normalize_preferences(preferences), ensure_ascii=False)
        with self._db:
            self._db.execute(
                """INSERT INTO playback_preferences(source_id,data) VALUES(?,?)
                ON CONFLICT(source_id) DO UPDATE SET data=excluded.data""",
                (source_id, data),
            )

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

    def backup_records(self, *, include_history: bool = True) -> dict:
        """Read personal rows, without catalogue URLs or account snapshots."""
        history = None
        if include_history:
            history = [
                dict(
                    channel_id=row[0],
                    position=row[1],
                    duration=row[2],
                    updated_at=row[3],
                    history_hidden=bool(row[4]),
                )
                for row in self._db.execute(
                    "SELECT channel_id,position,duration,updated_at,history_hidden FROM progress"
                )
            ]
        favorites = self.favorites()
        referenced = favorites | {item["channel_id"] for item in history or []}
        channels = [
            dict(
                zip(
                    ("id", "source_id", "name", "kind", "series_id", "provider_key"),
                    row,
                    strict=True,
                )
            )
            for row in self._db.execute(
                "SELECT id,source_id,name,kind,series_id,provider_key FROM channels ORDER BY rowid"
            )
            if row[0] in referenced
        ]
        return {
            "sources": self.sources(),
            "channels": channels,
            "favorites": sorted(favorites),
            "favorite_folders": [
                dict(
                    id=identity,
                    name=name,
                    position=position,
                    members=sorted(self.folder_items(identity)),
                )
                for identity, name, position in self._db.execute(
                    "SELECT id,name,position FROM favorite_folders ORDER BY position,id"
                )
            ],
            "playback_preferences": {
                identity: self.playback_preferences(identity)
                for (identity,) in self._db.execute("SELECT source_id FROM playback_preferences")
            },
            "app_settings": {
                key: self.setting(key)
                for (key,) in self._db.execute("SELECT key FROM app_settings")
            },
            "history": history,
        }

    def apply_backup_records(self, data: dict) -> None:
        """Merge validated personal rows in one transaction, preserving unrelated data."""
        from .backup import source_incomplete

        if self._db.in_transaction:
            raise ValueError("Başka bir kayıt işlemi sürerken yedek geri yüklenemez.")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing_sources = {item["id"]: item for item in self.sources()}
            for source in data["sources"]:
                existing = existing_sources.get(source["id"])
                if existing is not None and existing["type"] != source["type"]:
                    raise ValueError(
                        "Yedekteki bir kaynak kimliği farklı türde bir kaynakla çakışıyor."
                    )
                values = dict(source)
                if existing is not None:
                    preserved = (
                        ("location", "username", "password", "epg_url")
                        if source_incomplete(source)
                        else source["credentials_omitted"]
                    )
                    for field in preserved:
                        values[field] = existing[field]
                self._db.execute(
                    """INSERT INTO sources(id,name,type,location,username,password,epg_url)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name, location=excluded.location,
                      username=excluded.username, password=excluded.password,
                      epg_url=excluded.epg_url""",
                    tuple(values[field] for field in _SOURCE_FIELDS),
                )

            channel_ids = {}
            source_types = {item["id"]: item["type"] for item in data["sources"]}
            for channel in data["channels"]:
                identity, source_id = channel["id"], channel["source_id"]
                existing = self._db.execute(
                    "SELECT source_id,provider_key FROM channels WHERE id=?", (identity,)
                ).fetchone()
                if existing is not None and (
                    existing[0] != source_id
                    or (
                        existing[1]
                        and channel["provider_key"]
                        and existing[1] != channel["provider_key"]
                    )
                ):
                    raise ValueError("Yedekteki bir kanal kimliği mevcut kanalla çakışıyor.")
                by_provider = self._db.execute(
                    "SELECT id FROM channels "
                    "WHERE source_id=? AND provider_key=? AND provider_key<>''",
                    (source_id, channel["provider_key"]),
                ).fetchone()
                if existing is None and by_provider is not None:
                    channel_ids[identity] = by_provider[0]
                    continue
                if existing is None and source_types[source_id] == "direct":
                    direct = self._db.execute(
                        "SELECT id FROM channels WHERE source_id=?", (source_id,)
                    ).fetchall()
                    if len(direct) == 1:
                        channel_ids[identity] = direct[0][0]
                        continue
                if existing is None:
                    # Empty URLs leave restored references unplayable until catalogue refresh.
                    self._db.execute(
                        """INSERT INTO channels(
                            id,source_id,name,url,group_name,tvg_id,logo,kind,
                            series_id,headers,provider_key
                        ) VALUES(?,?,?,'','','','',?,?, '{}',?)""",
                        (
                            identity,
                            source_id,
                            channel["name"],
                            channel["kind"],
                            channel["series_id"],
                            channel["provider_key"],
                        ),
                    )
                channel_ids[identity] = identity

            self._db.executemany(
                "INSERT OR IGNORE INTO favorites(channel_id) VALUES(?)",
                [(channel_ids[identity],) for identity in data["favorites"]],
            )
            folders = dict(self.folders())
            folder_names = {name.casefold(): identity for identity, name in folders.items()}
            for folder in data["favorite_folders"]:
                identity, name = folder["id"], folder["name"].strip()
                matching = folder_names.get(name.casefold())
                if identity not in folders and matching is not None:
                    identity = matching
                elif matching is not None and matching != identity:
                    raise ValueError("Yedekteki klasör adı mevcut bir klasörle çakışıyor.")
                if identity in folders:
                    folder_names.pop(folders[identity].casefold(), None)
                folders[identity] = name
                folder_names[name.casefold()] = identity
                self._db.execute(
                    """INSERT INTO favorite_folders(id,name,position) VALUES(?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,position=excluded.position""",
                    (identity, name, folder["position"]),
                )
                self._db.executemany(
                    "INSERT OR IGNORE INTO favorite_folder_items(folder_id,channel_id) VALUES(?,?)",
                    [(identity, channel_ids[item]) for item in folder["members"]],
                )
            for source_id, preferences in data["playback_preferences"].items():
                merged = self.playback_preferences(source_id) | preferences
                self._db.execute(
                    """INSERT INTO playback_preferences(source_id,data) VALUES(?,?)
                    ON CONFLICT(source_id) DO UPDATE SET data=excluded.data""",
                    (source_id, json.dumps(merged, ensure_ascii=False)),
                )
            self._db.executemany(
                """INSERT INTO app_settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [
                    (key, json.dumps(value, ensure_ascii=False))
                    for key, value in data["app_settings"].items()
                ],
            )
            self._db.executemany(
                """INSERT INTO progress(channel_id,position,duration,updated_at,history_hidden)
                VALUES(?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
                  position=excluded.position,duration=excluded.duration,
                  updated_at=excluded.updated_at,history_hidden=excluded.history_hidden""",
                [
                    (
                        channel_ids[item["channel_id"]],
                        item["position"],
                        item["duration"],
                        item["updated_at"],
                        int(item["history_hidden"]),
                    )
                    for item in data["history"] or []
                ],
            )

    def close(self) -> None:
        self._db.close()

    def add_reminder(
        self, channel_id: str, title: str, start: int, end: int, lead_minutes: float = 5
    ) -> int:
        """Keep duplicate programmes idempotent, including their notification state."""
        with self._db:
            self._db.execute(
                """INSERT INTO reminders(channel_id,title,start,end,lead_minutes)
                VALUES(?,?,?,?,?) ON CONFLICT(channel_id,start) DO NOTHING""",
                (channel_id, title, start, end, float(lead_minutes)),
            )
            return self._db.execute(
                "SELECT id FROM reminders WHERE channel_id=? AND start=?", (channel_id, start)
            ).fetchone()[0]

    def remove_reminder(self, reminder_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))

    def reminders(self) -> list[dict[str, Any]]:
        columns = ("id", "channel_id", "title", "start", "end", "notified", "lead_minutes")
        rows = self._db.execute(
            "SELECT id,channel_id,title,start,end,notified,lead_minutes FROM reminders "
            "ORDER BY start,id"
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def mark_reminder_notified(self, reminder_id: int) -> None:
        with self._db:
            self._db.execute("UPDATE reminders SET notified=1 WHERE id=?", (reminder_id,))

    def drop_expired_reminders(self, now: float) -> int:
        with self._db:
            return self._db.execute("DELETE FROM reminders WHERE end<=?", (now,)).rowcount
