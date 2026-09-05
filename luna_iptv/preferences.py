"""Remember semantic track choices; mpv track IDs belong to one file only."""

from __future__ import annotations

import unicodedata

_ALIASES = {
    "tur": "tr",
    "eng": "en",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "ara": "ar",
    "rus": "ru",
    "jpn": "ja",
    "ita": "it",
    "por": "pt",
}
_MODES = {"audio": "aid", "sub": "sid"}


def _text(value, limit=256):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if unicodedata.category(c) != "Cc").strip()[:limit]


def _language(value):
    value = _text(value, 32).casefold().replace("_", "-").split("-")[0]
    if value in ("und", "unknown"):
        return ""
    return _ALIASES.get(value, value)


def track_preference(track):
    language, title = _language(track.get("lang")), _text(track.get("title"))
    if not language and not title:
        return None
    return {
        "mode": "track",
        "lang": language,
        "title": title,
        "forced": track.get("forced") is True,
        "hearing_impaired": track.get("hearing-impaired", track.get("hearing_impaired")) is True,
    }


def normalize_preferences(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("remember"), bool):
        result["remember"] = value["remember"]
    for mode in _MODES:
        choice = value.get(mode)
        if not isinstance(choice, dict):
            continue
        if choice.get("mode") == "off":
            result[mode] = {"mode": "off"}
        elif choice.get("mode") == "track":
            normalized = track_preference(choice)
            if normalized:
                result[mode] = normalized
    return result


def match_track(tracks, mode, choice):
    if choice.get("mode") == "off":
        return "no"
    matches = []
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") != mode:
            continue
        identity = track.get("id")
        if isinstance(identity, bool) or not isinstance(identity, int) or identity < 1:
            continue
        language = _language(track.get("lang"))
        title = _text(track.get("title")).casefold()
        preferred_title = choice.get("title", "").casefold()
        if choice.get("lang"):
            if language != choice["lang"]:
                continue
        elif not preferred_title or title != preferred_title:
            continue
        score = (
            bool(preferred_title) and title == preferred_title,
            (track.get("forced") is True) == choice.get("forced", False),
            (track.get("hearing-impaired") is True) == choice.get("hearing_impaired", False),
            bool(track.get("default")),
        )
        matches.append((score, identity))
    return max(matches, key=lambda pair: pair[0])[1] if matches else None


class TrackPreferences:
    def __init__(self, store, player):
        self.store = store
        self.player = player
        self.source_id = None
        self.generation = 0
        self._preferences = {}
        self._tracks = []
        self._loaded = False
        self._applied = {}
        self._manual_modes = set()

    @property
    def remember(self):
        return self._preferences.get("remember", True)

    def begin(self, source_id):
        self.generation += 1
        self.source_id = source_id
        self._preferences = self.store.playback_preferences(source_id) if source_id else {}
        self._tracks = []
        self._loaded = False
        self._applied = {}
        self._manual_modes = set()
        for mode, prop in _MODES.items():
            choice = self._preferences.get(mode, {}) if self.remember else {}
            value = "no" if choice.get("mode") == "off" else "auto"
            self.player.set_property(prop, value)
            if value == "no":
                self._applied[mode] = value

    def update_tracks(self, tracks):
        self._tracks = (
            [track for track in tracks if isinstance(track, dict)]
            if isinstance(tracks, list)
            else []
        )
        self._apply()

    def loaded(self):
        self._loaded = True
        self._apply()

    def finish(self):
        self.generation += 1
        self._loaded = False
        self._tracks = []
        self.source_id = None
        self._preferences = {}
        self._applied = {}
        self._manual_modes = set()

    def _apply(self):
        if not self._loaded or not self.remember:
            return
        for mode, prop in _MODES.items():
            choice = self._preferences.get(mode)
            if not choice or mode in self._manual_modes:
                continue
            selected = match_track(self._tracks, mode, choice)
            if selected is not None and self._applied.get(mode) != selected:
                self._applied[mode] = selected
                self.player.set_property(prop, selected)

    def select(self, mode, track, *, generation=None):
        if mode not in _MODES or (generation is not None and generation != self.generation):
            return False
        if track is None:
            value, choice = "no", {"mode": "off"}
        else:
            value = track.get("id")
            if (
                track.get("type") != mode
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                return False
            choice = track_preference(track)
        self._manual_modes.add(mode)
        self._applied[mode] = value
        self.player.set_property(_MODES[mode], value)
        if self.source_id and self.remember and choice:
            self._preferences[mode] = choice
            self._save()
        return True

    def set_remember(self, enabled, *, generation=None):
        if generation is not None and generation != self.generation:
            return False
        self._preferences["remember"] = bool(enabled)
        self._save()
        if enabled:
            self._applied.clear()
            self._manual_modes.clear()
            self._apply()

    def reset(self, *, generation=None):
        if generation is not None and generation != self.generation:
            return False
        self._preferences = {"remember": self.remember}
        self._save()
        self._applied.clear()
        self._manual_modes.clear()
        for prop in _MODES.values():
            self.player.set_property(prop, "auto")

    def _save(self):
        if self.source_id:
            self.store.save_playback_preferences(self.source_id, self._preferences)
