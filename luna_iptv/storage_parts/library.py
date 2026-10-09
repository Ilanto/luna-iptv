from __future__ import annotations

from .. import storage as _storage


class LibraryMixin:
    """Manage profile favorites, category preferences and folders."""

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
        if not name.strip() or any(_storage.unicodedata.category(c) == "Cc" for c in name):
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
                    raise _storage.sqlite3.IntegrityError("FOREIGN KEY constraint failed")
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
