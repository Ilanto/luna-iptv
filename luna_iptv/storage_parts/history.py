from __future__ import annotations

from .. import storage as _storage


class HistoryMixin:
    """Store playback progress, watch statistics and unseen episodes."""

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
        if not _storage.math.isfinite(seconds) or seconds <= 0:
            return
        day = _storage.date.fromisoformat(day).isoformat()
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
        today = today or _storage.date.today()
        monday = today - _storage.timedelta(days=today.weekday())
        start = today - _storage.timedelta(days=6)
        rows = self._db.execute(
            """SELECT w.day,c.name,c.kind,w.seconds FROM watch_log w
            JOIN channels c ON c.id=w.channel_id
            WHERE w.profile_id=? AND w.day BETWEEN ? AND ?""",
            (self.profile_id, start.isoformat(), today.isoformat()),
        ).fetchall()
        days = {(start + _storage.timedelta(days=i)).isoformat(): 0.0 for i in range(7)}
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

    def watch_seconds(self, day, *, profile_id=None):
        profile_id = self.profile_id if profile_id is None else profile_id
        return self._db.execute(
            "SELECT COALESCE(SUM(seconds),0) FROM watch_log WHERE profile_id=? AND day=?",
            (profile_id, day),
        ).fetchone()[0]

    def favorite_series(self):
        """All profiles' series favorites, without changing the active profile."""
        return self._db.execute(
            """SELECT f.profile_id,c.id,c.source_id FROM favorites f
            JOIN channels c ON c.id=f.channel_id JOIN sources s ON s.id=c.source_id
            WHERE c.kind='series' AND s.type='xtream' ORDER BY c.rowid,f.profile_id"""
        ).fetchall()

    def episode_check_day(self, series_id):
        row = self._db.execute(
            "SELECT day FROM episode_checks WHERE series_id=?", (series_id,)
        ).fetchone()
        return row[0] if row else None

    def mark_episode_check(self, series_id, day):
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO episode_checks(series_id,day) VALUES(?,?)",
                (series_id, day),
            )

    def mark_new_episodes(self, series_id, episodes, profiles):
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO unseen_episodes VALUES(?,?,?)",
                [(pid, series_id, episode.id) for pid in profiles for episode in episodes],
            )

    def unseen_series(self):
        return {
            row[0]
            for row in self._db.execute(
                "SELECT DISTINCT series_id FROM unseen_episodes WHERE profile_id=?",
                (self.profile_id,),
            )
        }

    def see_series(self, series_id):
        with self._db:
            self._db.execute(
                "DELETE FROM unseen_episodes WHERE profile_id=? AND series_id=?",
                (self.profile_id, series_id),
            )
