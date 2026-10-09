from __future__ import annotations


class ParentalMixin:
    """Persist PIN and channel/category locks."""

    def pin_hash(self) -> str | None:
        row = self._db.execute("SELECT value FROM secrets WHERE key='pin_hash'").fetchone()
        return row[0] if row is not None else None

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
        from ..parental import is_adult_group

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
