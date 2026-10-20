from __future__ import annotations

from typing import Any


class SchedulesMixin:
    """Store reminders and shared recording jobs."""

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

    def recordings(self):
        cursor = self._db.execute("SELECT * FROM recordings ORDER BY start DESC, id DESC")
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]

    def add_recording(self, channel, title, start, end, padding, *, authorized=False, kids=False):
        with self._db:
            self._db.execute(
                """INSERT OR IGNORE INTO recordings(
                    channel_id,channel_name,title,start,end,padding,authorized,kids
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (channel.id, channel.name, title, start, end, padding, int(authorized), int(kids)),
            )
        return self._db.execute(
            "SELECT id FROM recordings WHERE channel_id=? AND start=?", (channel.id, start)
        ).fetchone()[0]

    def update_recording(self, identity, status, *, path=None, message=""):
        with self._db:
            self._db.execute(
                "UPDATE recordings SET status=?,path=COALESCE(?,path),message=? WHERE id=?",
                (status, path, message, identity),
            )
