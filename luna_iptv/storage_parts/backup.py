from __future__ import annotations

from .. import storage as _storage
from ..i18n import _


class BackupMixin:
    """Export and merge personal backup records atomically."""

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
        from ..backup import source_incomplete

        if self._db.in_transaction:
            raise ValueError(_("Başka bir kayıt işlemi sürerken yedek geri yüklenemez."))
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing_sources = {item["id"]: item for item in self.sources()}
            for source in data["sources"]:
                existing = existing_sources.get(source["id"])
                if existing is not None and existing["type"] != source["type"]:
                    raise ValueError(
                        _("Yedekteki bir kaynak kimliği farklı türde bir kaynakla çakışıyor.")
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
                    tuple(values[field] for field in _storage._SOURCE_FIELDS),
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
                    raise ValueError(_("Yedekteki bir kanal kimliği mevcut kanalla çakışıyor."))
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
                    (source_id, _storage.json.dumps(merged, ensure_ascii=False)),
                )
            self._db.executemany(
                """INSERT INTO app_settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [
                    (key, _storage.json.dumps(value, ensure_ascii=False))
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
