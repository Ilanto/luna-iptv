from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .accounts import AccountProfile, bounded_timestamp, sanitize_profile
from .media_details import MediaDetails, normalize_info
from .models import Channel, Playlist

_SOURCE_FIELDS = ("id", "name", "type", "location", "username", "password", "epg_url")
_UNCHANGED = object()  # distinguish an omitted avatar from explicit None (initial)


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
            profiles = self.profiles()
            active = self.setting("active_profile")
            self.profile_id = next(
                (item["id"] for item in profiles if item["id"] == active), profiles[0]["id"]
            )
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
        # SQLite table rebuilds must disable FK actions before opening the transaction.
        self._db.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._db:
                self._initialize_schema()
                if self._db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise sqlite3.IntegrityError("Profil taşımasında geçersiz veri ilişkisi.")
        finally:
            self._db.execute("PRAGMA foreign_keys = ON")

    def _initialize_schema(self) -> None:
        self._db.executescript(
            """
            BEGIN IMMEDIATE;
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
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                kids INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                avatar TEXT
            );
            CREATE TABLE IF NOT EXISTS watch_log (
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                seconds REAL NOT NULL CHECK(seconds >= 0),
                PRIMARY KEY(profile_id, day, channel_id)
            );
            CREATE TABLE IF NOT EXISTS secrets (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS category_prefs (
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                group_name TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                position INTEGER,
                PRIMARY KEY(profile_id, source_id, kind, group_name)
            );
            CREATE TABLE IF NOT EXISTS group_locks (
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                group_name TEXT NOT NULL,
                locked INTEGER NOT NULL,
                PRIMARY KEY(source_id, group_name)
            );
            CREATE TABLE IF NOT EXISTS channel_locks (
                channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE
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
        profile_columns = {row[1] for row in self._db.execute("PRAGMA table_info(profiles)")}
        if "avatar" not in profile_columns:
            self._db.execute("ALTER TABLE profiles ADD COLUMN avatar TEXT")
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
        self._migrate_profiles()
        self._backfill_all_provider_keys()
        self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS channels_provider_key_idx
            ON channels(source_id, provider_key) WHERE provider_key <> ''
            """
        )

    def _migrate_profiles(self) -> None:
        """Rebuild legacy personal tables together, retaining folder and reminder ids."""
        self._db.execute(
            """INSERT INTO profiles(id,name,color,kids,protected,position)
            SELECT 1,'Ben','#E8B04B',0,0,0 WHERE NOT EXISTS(SELECT 1 FROM profiles)"""
        )
        definitions = {
            "favorites": """
                channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                PRIMARY KEY(profile_id, channel_id)
            """,
            "favorite_folders": """
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL
            """,
            "progress": """
                channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                position REAL NOT NULL, duration REAL NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0,
                history_hidden INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(profile_id, channel_id)
            """,
            "reminders": """
                id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL,
                start INTEGER NOT NULL, end INTEGER NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0, lead_minutes REAL NOT NULL DEFAULT 5,
                UNIQUE(profile_id, channel_id, start)
            """,
        }
        migrated = False
        for table, definition in definitions.items():
            columns = [row[1] for row in self._db.execute(f"PRAGMA table_info({table})")]
            if "profile_id" in columns:
                continue
            if not migrated:
                self._db.execute("DROP TRIGGER IF EXISTS favorite_folder_cleanup")
                migrated = True
            self._db.execute(
                f"""CREATE TABLE {table}_profiles (
                    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    {definition}
                )"""
            )
            names = ",".join(columns)
            self._db.execute(
                f"INSERT INTO {table}_profiles(profile_id,{names}) SELECT 1,{names} FROM {table}"
            )
            self._db.execute(f"DROP TABLE {table}")
            self._db.execute(f"ALTER TABLE {table}_profiles RENAME TO {table}")
        if migrated:
            self._db.execute(
                """CREATE TRIGGER favorite_folder_cleanup AFTER DELETE ON favorites BEGIN
                    DELETE FROM favorite_folder_items WHERE channel_id=OLD.channel_id
                    AND folder_id IN (
                        SELECT id FROM favorite_folders WHERE profile_id=OLD.profile_id
                    );
                END"""
            )

    def profiles(self) -> list[dict]:
        columns = ("id", "name", "color", "kids", "protected", "position", "avatar")
        return [
            dict(zip(columns, (*row[:3], bool(row[3]), bool(row[4]), *row[5:]), strict=True))
            for row in self._db.execute(
                "SELECT id,name,color,kids,protected,position,avatar FROM profiles ORDER BY position,id"
            )
        ]

    def profile(self, profile_id: int) -> dict | None:
        return next((item for item in self.profiles() if item["id"] == profile_id), None)

    def _profile_name(self, name: str, profile_id: int | None = None) -> str:
        if (
            not name.strip()
            or len(name.strip()) > 40
            or any(unicodedata.category(c) == "Cc" for c in name)
        ):
            raise ValueError("Profil adı 1–40 karakter olmalı ve kontrol karakteri içermemeli.")
        name = name.strip()
        if any(
            item["id"] != profile_id and item["name"].casefold() == name.casefold()
            for item in self.profiles()
        ):
            raise ValueError("Bu adda bir profil zaten var.")
        return name

    @staticmethod
    def _profile_color(color: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("Profil rengi #RRGGBB biçiminde olmalı.")
        return color

    def create_profile(
        self, name: str, color: str, *, kids: bool = False, protected: bool = False, avatar=None
    ) -> int:
        with self._db:
            name, color = self._profile_name(name), self._profile_color(color)
            return self._db.execute(
                """INSERT INTO profiles(name,color,kids,protected,avatar,position)
                SELECT ?,?,?,?,?,COALESCE(MAX(position), -1) + 1 FROM profiles""",
                (name, color, int(kids), int(protected), avatar),
            ).lastrowid

    def update_profile(
        self,
        profile_id: int,
        *,
        name=None,
        color=None,
        kids=None,
        protected=None,
        avatar=_UNCHANGED,
    ) -> None:
        with self._db:
            current = self.profile(profile_id)
            if current is None:
                raise ValueError("Profil bulunamadı.")
            self._db.execute(
                "UPDATE profiles SET name=?,color=?,kids=?,protected=?,avatar=? WHERE id=?",
                (
                    current["name"] if name is None else self._profile_name(name, profile_id),
                    current["color"] if color is None else self._profile_color(color),
                    int(current["kids"] if kids is None else kids),
                    int(current["protected"] if protected is None else protected),
                    current["avatar"] if avatar is _UNCHANGED else avatar,
                    profile_id,
                ),
            )

    def delete_profile(self, profile_id: int) -> None:
        remaining = [item for item in self.profiles() if item["id"] != profile_id]
        if self.profile(profile_id) is None:
            raise ValueError("Profil bulunamadı.")
        if not remaining:
            raise ValueError("Son profil silinemez.")
        active = remaining[0]["id"] if self.profile_id == profile_id else self.profile_id
        with self._db:
            self._db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
            if self.profile_id == profile_id:
                self._db.execute(
                    """INSERT INTO app_settings(key,value) VALUES('active_profile',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (json.dumps(active),),
                )
        self.profile_id = active

    def use_profile(self, profile_id: int) -> None:
        if self.profile(profile_id) is None:
            raise ValueError("Profil bulunamadı.")
        self.set_setting("active_profile", profile_id)
        self.profile_id = profile_id

    def pin_hash(self) -> str | None:
        row = self._db.execute("SELECT value FROM secrets WHERE key='pin_hash'").fetchone()
        return row[0] if row is not None else None

    def online_secret(self, key: str) -> str:
        from .settings import ONLINE_SECRETS

        if key not in ONLINE_SECRETS:
            raise ValueError("Bilinmeyen çevrimiçi ayar.")
        row = self._db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
        return row[0] if row is not None else ""

    def set_online_secret(self, key: str, value: str) -> None:
        from .settings import ONLINE_SECRETS

        if key not in ONLINE_SECRETS:
            raise ValueError("Bilinmeyen çevrimiçi ayar.")
        with self._db:
            if value:
                self._db.execute(
                    """INSERT INTO secrets(key,value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (key, value),
                )
            else:
                self._db.execute("DELETE FROM secrets WHERE key=?", (key,))

    def set_pin_hash(self, value: str | None) -> None:
        with self._db:
            if value is None:
                self._db.execute("DELETE FROM secrets WHERE key='pin_hash'")
            else:
                self._db.execute(
                    """INSERT INTO secrets(key,value) VALUES('pin_hash',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (value,),
                )

    def locked_groups(self) -> set[tuple[str, str]]:
        return set(self._db.execute("SELECT source_id,group_name FROM group_locks WHERE locked=1"))

    def set_group_locked(self, source_id: str, group_name: str, locked: bool) -> None:
        with self._db:
            self._db.execute(
                """INSERT INTO group_locks(source_id,group_name,locked) VALUES(?,?,?)
                ON CONFLICT(source_id,group_name) DO UPDATE SET locked=excluded.locked""",
                (source_id, group_name, int(locked)),
            )

    def locked_channels(self) -> set[str]:
        return {row[0] for row in self._db.execute("SELECT channel_id FROM channel_locks")}

    def set_channel_locked(self, channel_id: str, locked: bool) -> None:
        with self._db:
            if locked:
                self._db.execute(
                    "INSERT OR IGNORE INTO channel_locks(channel_id) VALUES(?)", (channel_id,)
                )
            else:
                self._db.execute("DELETE FROM channel_locks WHERE channel_id=?", (channel_id,))

    def lock_adult_groups(self) -> int:
        from .parental import is_adult_group

        with self._db:
            groups = self._db.execute(
                """SELECT DISTINCT source_id,group_name FROM channels AS c
                WHERE NOT EXISTS(SELECT 1 FROM group_locks AS g
                    WHERE g.source_id=c.source_id AND g.group_name=c.group_name)"""
            ).fetchall()
            return self._db.executemany(
                "INSERT OR IGNORE INTO group_locks(source_id,group_name,locked) VALUES(?,?,1)",
                [(source, group) for source, group in groups if is_adult_group(group)],
            ).rowcount

    def channel_groups(self) -> list[tuple[str, str, int]]:
        rows = self._db.execute(
            """SELECT source_id,group_name,COUNT(*) FROM channels
            WHERE group_name<>'' GROUP BY source_id,group_name"""
        ).fetchall()
        return sorted(rows, key=lambda row: (row[0], row[1].casefold(), row[1]))

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
                    "INSERT OR IGNORE INTO favorites(profile_id,channel_id) VALUES(?,?)",
                    (self.profile_id, channel_id),
                )
            else:
                self._db.execute(
                    "DELETE FROM favorites WHERE profile_id=? AND channel_id=?",
                    (self.profile_id, channel_id),
                )

    def favorites(self) -> set[str]:
        return {
            row[0]
            for row in self._db.execute(
                "SELECT channel_id FROM favorites WHERE profile_id=?", (self.profile_id,)
            )
        }

    def category_prefs(self, source_id: str | None = None) -> dict[tuple[str, str, str], dict]:
        sql = (
            "SELECT source_id,kind,group_name,hidden,position FROM category_prefs "
            "WHERE profile_id=?"
        )
        parameters = [self.profile_id]
        if source_id is not None:
            sql += " AND source_id=?"
            parameters.append(source_id)
        return {
            (source, kind, group): {"hidden": bool(hidden), "position": position}
            for source, kind, group, hidden, position in self._db.execute(sql, parameters)
        }

    def save_category_prefs(self, source_id: str, kind: str, rows: list[tuple[str, bool]]) -> None:
        """Replace one profile's source and section preferences atomically."""
        if kind not in ("live", "movie", "series"):
            raise ValueError("Geçersiz yayın türü.")
        with self._db:
            self._db.execute(
                "DELETE FROM category_prefs WHERE profile_id=? AND source_id=? AND kind=?",
                (self.profile_id, source_id, kind),
            )
            self._db.executemany(
                "INSERT INTO category_prefs VALUES(?,?,?,?,?,?)",
                [
                    (self.profile_id, source_id, kind, group, int(hidden), position)
                    for position, (group, hidden) in enumerate(rows)
                ],
            )

    def reset_category_prefs(self, source_id: str, kind: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM category_prefs WHERE profile_id=? AND source_id=? AND kind=?",
                (self.profile_id, source_id, kind),
            )

    def folders(self) -> list[tuple[int, str]]:
        return self._db.execute(
            "SELECT id,name FROM favorite_folders WHERE profile_id=? ORDER BY position,id",
            (self.profile_id,),
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
                """INSERT INTO favorite_folders(profile_id,name,position)
                SELECT ?,?,COALESCE(MAX(position), -1) + 1 FROM favorite_folders
                WHERE profile_id=?""",
                (self.profile_id, name, self.profile_id),
            ).lastrowid

    def rename_folder(self, folder_id: int, name: str) -> bool:
        with self._db:
            if not self._owns_folder(folder_id):
                return False
            name = self._folder_name(name, folder_id)
            return (
                self._db.execute(
                    "UPDATE favorite_folders SET name=? WHERE id=? AND profile_id=?",
                    (name, folder_id, self.profile_id),
                ).rowcount
                > 0
            )

    def delete_folder(self, folder_id: int) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM favorite_folders WHERE id=? AND profile_id=?",
                (folder_id, self.profile_id),
            )

    def _owns_folder(self, folder_id: int) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM favorite_folders WHERE id=? AND profile_id=?",
                (folder_id, self.profile_id),
            ).fetchone()
            is not None
        )

    def folder_items(self, folder_id: int) -> set[str]:
        return {
            row[0]
            for row in self._db.execute(
                """SELECT channel_id FROM favorite_folder_items
                WHERE folder_id=? AND folder_id IN (
                    SELECT id FROM favorite_folders WHERE profile_id=?
                )""",
                (folder_id, self.profile_id),
            )
        }

    def folders_of(self, channel_id: str) -> set[int]:
        return {
            row[0]
            for row in self._db.execute(
                """SELECT folder_id FROM favorite_folder_items
                WHERE channel_id=? AND folder_id IN (
                    SELECT id FROM favorite_folders WHERE profile_id=?
                )""",
                (channel_id, self.profile_id),
            )
        }

    def set_in_folder(self, folder_id: int, channel_id: str, member: bool) -> None:
        with self._db:
            if not self._owns_folder(folder_id):
                # Preserve the existing error for missing ids; foreign profiles are a no-op.
                if (
                    member
                    and self._db.execute(
                        "SELECT 1 FROM favorite_folders WHERE id=?", (folder_id,)
                    ).fetchone()
                    is None
                ):
                    raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
                return
            if member:
                self._db.execute(
                    "INSERT OR IGNORE INTO favorites(profile_id,channel_id) VALUES(?,?)",
                    (self.profile_id, channel_id),
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
                "SELECT COALESCE(MAX(updated_at), 0) + 1 FROM progress WHERE profile_id=?",
                (self.profile_id,),
            ).fetchone()[0]
            self._db.execute(
                """
                INSERT INTO progress(
                    profile_id,channel_id,position,duration,updated_at,history_hidden
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(profile_id,channel_id) DO UPDATE SET position=excluded.position,
                  duration=excluded.duration, updated_at=excluded.updated_at,
                  history_hidden=excluded.history_hidden
                """,
                (
                    self.profile_id,
                    channel_id,
                    float(position),
                    float(duration),
                    updated_at,
                    int(not mark_recent),
                ),
            )

    def progress(self, channel_id: str) -> tuple[float, float]:
        row = self._db.execute(
            "SELECT position,duration FROM progress WHERE profile_id=? AND channel_id=?",
            (self.profile_id, channel_id),
        ).fetchone()
        return (0.0, 0.0) if row is None else (float(row[0]), float(row[1]))

    def progress_map(self) -> dict[str, tuple[float, float]]:
        """Every saved position with a known duration, for painting without a query per card."""
        return {
            row[0]: (float(row[1]), float(row[2]))
            for row in self._db.execute(
                "SELECT channel_id,position,duration FROM progress "
                "WHERE profile_id=? AND duration>0",
                (self.profile_id,),
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
                WHERE profile_id=? AND position >= 0 AND history_hidden = 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (self.profile_id, int(limit)),
            )
        ]

    def clear_history(self, source_id: str | None = None, *, reset_progress: bool = False) -> None:
        assignments = "history_hidden = 1"
        if reset_progress:
            assignments += ", position = 0, duration = 0"
        sql = f"UPDATE progress SET {assignments} WHERE profile_id=?"
        args = (self.profile_id,)
        if source_id is not None:
            sql += " AND channel_id IN (SELECT id FROM channels WHERE source_id = ?)"
            args += (source_id,)
        with self._db:
            self._db.execute(sql, args)

    def add_watch_time(self, channel_id, day, seconds, *, profile_id=None):
        """Increment actual viewing time; progress.updated_at is an ordering counter."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return
        if not math.isfinite(seconds) or seconds <= 0:
            return
        day = date.fromisoformat(day).isoformat()
        profile_id = self.profile_id if profile_id is None else profile_id
        with self._db:
            self._db.execute(
                """INSERT INTO watch_log(profile_id,day,channel_id,seconds)
                SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM channels WHERE id=?)
                AND EXISTS(SELECT 1 FROM profiles WHERE id=?)
                ON CONFLICT(profile_id,day,channel_id)
                DO UPDATE SET seconds=watch_log.seconds+excluded.seconds""",
                (profile_id, day, channel_id, seconds, channel_id, profile_id),
            )

    def watch_statistics(self, today=None):
        today = today or date.today()
        monday = today - timedelta(days=today.weekday())
        start = today - timedelta(days=6)
        rows = self._db.execute(
            """SELECT w.day,c.name,c.kind,w.seconds FROM watch_log w
            JOIN channels c ON c.id=w.channel_id
            WHERE w.profile_id=? AND w.day BETWEEN ? AND ?""",
            (self.profile_id, start.isoformat(), today.isoformat()),
        ).fetchall()
        days = {(start + timedelta(days=i)).isoformat(): 0.0 for i in range(7)}
        # Aggregate by channel id, not title: duplicate names are distinct channels.
        top = self._db.execute(
            """SELECT c.name,SUM(w.seconds) total FROM watch_log w
            JOIN channels c ON c.id=w.channel_id
            WHERE w.profile_id=? AND w.day BETWEEN ? AND ?
            GROUP BY w.channel_id ORDER BY total DESC,c.name,w.channel_id LIMIT 5""",
            (self.profile_id, monday.isoformat(), today.isoformat()),
        ).fetchall()
        live = vod = 0.0
        for day, _name, kind, seconds in rows:
            days[day] += seconds
            if day >= monday.isoformat():
                if kind == "live":
                    live += seconds
                else:
                    vod += seconds
        return {
            "week_seconds": live + vod,
            "days": list(days.items()),
            "top": top,
            "live_seconds": live,
            "vod_seconds": vod,
        }

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

    def _backup_profile_records(self, profile_id: int, include_history: bool) -> dict:
        def rows(query, columns):
            return [
                dict(zip(columns, row, strict=True))
                for row in self._db.execute(query, (profile_id,))
            ]

        history = (
            rows(
                "SELECT channel_id,position,duration,updated_at,history_hidden FROM progress "
                "WHERE profile_id=? ORDER BY channel_id",
                ("channel_id", "position", "duration", "updated_at", "history_hidden"),
            )
            if include_history
            else None
        )
        for item in history or []:
            item["history_hidden"] = bool(item["history_hidden"])
        favorites = [
            row[0]
            for row in self._db.execute(
                "SELECT channel_id FROM favorites WHERE profile_id=? ORDER BY channel_id",
                (profile_id,),
            )
        ]
        folders = rows(
            "SELECT id,name,position FROM favorite_folders WHERE profile_id=? ORDER BY position,id",
            ("id", "name", "position"),
        )
        for folder in folders:
            folder["members"] = [
                row[0]
                for row in self._db.execute(
                    "SELECT channel_id FROM favorite_folder_items WHERE folder_id=? ORDER BY channel_id",
                    (folder["id"],),
                )
            ]
        reminders = rows(
            "SELECT r.channel_id,r.title,r.start,r.end,r.notified,r.lead_minutes "
            "FROM reminders r JOIN channels c ON c.id=r.channel_id "
            "WHERE r.profile_id=? ORDER BY r.start,r.id",
            ("channel_id", "title", "start", "end", "notified", "lead_minutes"),
        )
        for item in reminders:
            item["notified"] = bool(item["notified"])
        categories = rows(
            "SELECT source_id,kind,group_name,hidden,position FROM category_prefs "
            "WHERE profile_id=? ORDER BY source_id,kind,group_name",
            ("source_id", "kind", "group_name", "hidden", "position"),
        )
        for item in categories:
            item["hidden"] = bool(item["hidden"])
        return dict(
            favorites=favorites,
            favorite_folders=folders,
            history=history,
            reminders=reminders,
            category_prefs=categories,
        )

    def backup_records(self, *, include_history: bool = True) -> dict:
        """Read every profile's personal rows; never include PINs or the secrets table."""
        columns = ["id", "name", "color", "kids", "protected", "position"]
        if "avatar" in {r[1] for r in self._db.execute("PRAGMA table_info(profiles)")}:
            columns.append("avatar")
        profiles = [
            dict(zip(columns, row, strict=True))
            for row in self._db.execute(
                f"SELECT {','.join(columns)} FROM profiles ORDER BY position,id"
            )
        ]
        for profile in profiles:
            profile["kids"], profile["protected"] = (
                bool(profile["kids"]),
                bool(profile["protected"]),
            )
        personal = {
            str(p["id"]): self._backup_profile_records(p["id"], include_history) for p in profiles
        }
        locks = sorted(self.locked_channels())
        referenced = set(locks)
        for records in personal.values():
            referenced.update(records["favorites"])
            referenced.update(item["channel_id"] for item in records["history"] or [])
            referenced.update(item["channel_id"] for item in records["reminders"])
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
            "profiles": profiles,
            "profile_data": personal,
            "backup_profile_id": self.profile_id,
            # Legacy readers of the preview fields still see the selected profile.
            **{
                key: personal[str(self.profile_id)][key]
                for key in ("favorites", "favorite_folders", "history")
            },
            "group_locks": [
                dict(source_id=s, group_name=g, locked=bool(v))
                for s, g, v in self._db.execute(
                    "SELECT source_id,group_name,locked FROM group_locks "
                    "ORDER BY source_id,group_name"
                )
            ],
            "channel_locks": locks,
            "playback_preferences": {
                identity: self.playback_preferences(identity)
                for (identity,) in self._db.execute("SELECT source_id FROM playback_preferences")
            },
            "app_settings": {
                key: self.setting(key)
                for (key,) in self._db.execute(
                    "SELECT key FROM app_settings WHERE key<>'active_profile'"
                )
            },
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

            if data["version"] == 1:
                profile_records = [(self.profile_id, data)]
            else:
                profile_records = self._merge_backup_profiles(data)
            for profile_id, records in profile_records:
                self._merge_backup_personal(profile_id, records, channel_ids)
            if data["version"] == 2:
                self._db.executemany(
                    """INSERT INTO group_locks(source_id,group_name,locked) VALUES(?,?,?)
                    ON CONFLICT(source_id,group_name) DO UPDATE SET locked=excluded.locked""",
                    [
                        (r["source_id"], r["group_name"], int(r["locked"]))
                        for r in data["group_locks"]
                    ],
                )
                self._db.executemany(
                    "INSERT OR IGNORE INTO channel_locks(channel_id) VALUES(?)",
                    [(channel_ids[i],) for i in data["channel_locks"]],
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
                    if key != "active_profile"
                ],
            )

    def _merge_backup_profiles(self, data: dict) -> list[tuple[int, dict]]:
        names = {p["name"].strip().casefold(): p["id"] for p in self.profiles()}
        has_avatar = "avatar" in {r[1] for r in self._db.execute("PRAGMA table_info(profiles)")}
        result = []
        for profile in sorted(data["profiles"], key=lambda p: (p["position"], p["id"])):
            name = profile["name"].strip()
            identity = names.get(name.casefold())
            fields = ["color", "kids", "protected", "position"]
            if has_avatar and "avatar" in profile:
                fields.append("avatar")
            values = [profile[field] for field in fields]
            if identity is None:
                identity = self._db.execute(
                    f"INSERT INTO profiles(name,{','.join(fields)}) "
                    f"VALUES({','.join('?' for _ in range(len(fields) + 1))})",
                    [name, *values],
                ).lastrowid
                names[name.casefold()] = identity
            else:
                self._db.execute(
                    f"UPDATE profiles SET {','.join(f'{field}=?' for field in fields)} WHERE id=?",
                    [*values, identity],
                )
            result.append((identity, data["profile_data"][str(profile["id"])]))
        return result

    def _merge_backup_personal(self, profile_id: int, records: dict, channel_ids: dict) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO favorites(profile_id,channel_id) VALUES(?,?)",
            [(profile_id, channel_ids[identity]) for identity in records["favorites"]],
        )
        # Folder ids are local autoincrement values, so folders merge by name:
        # a backup's folder 1 may be a different folder here. New ones go last.
        folder_names = {
            name.strip().casefold(): identity
            for identity, name in self._db.execute(
                "SELECT id,name FROM favorite_folders WHERE profile_id=?", (profile_id,)
            )
        }
        for folder in sorted(records["favorite_folders"], key=lambda f: (f["position"], f["id"])):
            name = folder["name"].strip()
            identity = folder_names.get(name.casefold())
            if identity is None:
                identity = self._db.execute(
                    """INSERT INTO favorite_folders(profile_id,name,position)
                    SELECT ?,?,COALESCE(MAX(position), -1) + 1 FROM favorite_folders
                    WHERE profile_id=?""",
                    (profile_id, name, profile_id),
                ).lastrowid
                folder_names[name.casefold()] = identity
            self._db.executemany(
                "INSERT OR IGNORE INTO favorite_folder_items(folder_id,channel_id) VALUES(?,?)",
                [(identity, channel_ids[item]) for item in folder["members"]],
            )
        self._db.executemany(
            """INSERT INTO progress(
                profile_id,channel_id,position,duration,updated_at,history_hidden
            ) VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id,channel_id) DO UPDATE SET
              position=excluded.position,duration=excluded.duration,
              updated_at=excluded.updated_at,history_hidden=excluded.history_hidden""",
            [
                (
                    profile_id,
                    channel_ids[item["channel_id"]],
                    item["position"],
                    item["duration"],
                    item["updated_at"],
                    int(item["history_hidden"]),
                )
                for item in records["history"] or []
            ],
        )
        for item in records.get("reminders", []):
            self._db.execute(
                """INSERT INTO reminders(profile_id,channel_id,title,start,end,notified,lead_minutes)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile_id,channel_id,start) DO UPDATE SET
                  title=excluded.title,end=excluded.end,notified=excluded.notified,
                  lead_minutes=excluded.lead_minutes""",
                (
                    profile_id,
                    channel_ids[item["channel_id"]],
                    item["title"],
                    item["start"],
                    item["end"],
                    int(item["notified"]),
                    item["lead_minutes"],
                ),
            )
        for item in records.get("category_prefs", []):
            self._db.execute(
                """INSERT INTO category_prefs(profile_id,source_id,kind,group_name,hidden,position)
                VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id,source_id,kind,group_name) DO UPDATE SET
                  hidden=excluded.hidden,position=excluded.position""",
                (
                    profile_id,
                    item["source_id"],
                    item["kind"],
                    item["group_name"],
                    int(item["hidden"]),
                    item["position"],
                ),
            )

    def close(self) -> None:
        self._db.close()

    def add_reminder(
        self, channel_id: str, title: str, start: int, end: int, lead_minutes: float = 5
    ) -> int:
        """Keep duplicate programmes idempotent, including their notification state."""
        with self._db:
            self._db.execute(
                """INSERT INTO reminders(profile_id,channel_id,title,start,end,lead_minutes)
                VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id,channel_id,start) DO NOTHING""",
                (self.profile_id, channel_id, title, start, end, float(lead_minutes)),
            )
            return self._db.execute(
                "SELECT id FROM reminders WHERE profile_id=? AND channel_id=? AND start=?",
                (self.profile_id, channel_id, start),
            ).fetchone()[0]

    def remove_reminder(self, reminder_id: int, *, profile_id: int | None = None) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM reminders WHERE id=? AND profile_id=?",
                (reminder_id, self.profile_id if profile_id is None else profile_id),
            )

    def reminders(self) -> list[dict[str, Any]]:
        columns = ("id", "channel_id", "title", "start", "end", "notified", "lead_minutes")
        rows = self._db.execute(
            "SELECT id,channel_id,title,start,end,notified,lead_minutes FROM reminders "
            "WHERE profile_id=? ORDER BY start,id",
            (self.profile_id,),
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def all_reminders(self) -> list[dict[str, Any]]:
        """Read every profile's reminders for the shared desktop scheduler."""
        columns = (
            "id",
            "profile_id",
            "channel_id",
            "title",
            "start",
            "end",
            "notified",
            "lead_minutes",
        )
        rows = self._db.execute(
            "SELECT id,profile_id,channel_id,title,start,end,notified,lead_minutes "
            "FROM reminders ORDER BY start,id"
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def mark_reminder_notified(self, reminder_id: int, *, profile_id: int | None = None) -> None:
        with self._db:
            self._db.execute(
                "UPDATE reminders SET notified=1 WHERE id=? AND profile_id=?",
                (reminder_id, self.profile_id if profile_id is None else profile_id),
            )

    def drop_expired_reminders(self, now: float) -> int:
        with self._db:
            return self._db.execute("DELETE FROM reminders WHERE end<=?", (now,)).rowcount
