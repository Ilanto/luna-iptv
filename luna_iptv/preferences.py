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
DEFAULT_TRACK_OPTIONS = {
    "aid": "auto",
    "sid": "auto",
    "alang": "",
    "slang": "",
    "subs-fallback": "default",
    "subs-fallback-forced": "yes",
    "subs-with-matching-audio": "yes",
}


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
        if choice.get("mode") in ("auto", "off"):
            result[mode] = {"mode": choice["mode"]}
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
        self._choices = {}
        self._tracks = []
        self._tracks_known = False
        self._loaded = False
        self._applied = {}
        self._manual_modes = set()
        self._notice = ""
        self._notice_checked = False

    @property
    def remember(self):
        return self._preferences.get("remember", True)

    def preview(self, source_id):
        from .settings import playback_defaults

        saved = normalize_preferences(
            self.store.playback_preferences(source_id) if source_id else {}
        )
        remember = saved.get("remember", True)
        defaults = playback_defaults(self.store)
        return {
            mode: dict(saved.get(mode, defaults[mode]) if remember else defaults[mode])
            for mode in _MODES
        } | {"remember": remember}

    def begin(self, source_id, preferences=None, *, persist=True):
        self.generation += 1
        self.source_id = source_id
        self._preferences = normalize_preferences(
            self.store.playback_preferences(source_id) if source_id else {}
        )
        if preferences is None:
            self._choices = self.preview(source_id)
        else:
            supplied = normalize_preferences(preferences)
            self._choices = {
                mode: dict(supplied.get(mode, {"mode": "auto"})) for mode in _MODES
            } | {"remember": supplied.get("remember", True)}
            self._preferences["remember"] = self._choices["remember"]
            if self.remember and persist:
                for mode in _MODES:
                    choice = self._choices[mode]
                    if choice["mode"] == "auto":
                        self._preferences.pop(mode, None)
                    else:
                        self._preferences[mode] = dict(choice)
            if persist:
                self._save()
        self._tracks = []
        self._tracks_known = False
        self._loaded = False
        self._applied = {}
        self._manual_modes = set()
        self._notice = ""
        self._notice_checked = False
        options = dict(DEFAULT_TRACK_OPTIONS)
        for mode, prop in _MODES.items():
            choice = self._choices[mode]
            if choice["mode"] == "off":
                options[prop] = "no"
            elif choice["mode"] == "track":
                language = choice.get("lang", "")
                options["alang" if mode == "audio" else "slang"] = language
                if mode == "sub":
                    options["subs-fallback"] = "no"
                    options["subs-fallback-forced"] = "no"
                    options["subs-with-matching-audio"] = "yes"
                    if not language:
                        # A title-only identity cannot be chosen until tracks are known.
                        options["sid"] = "no"
        return options

    def current_choices(self):
        return {mode: dict(self._choices.get(mode, {"mode": "auto"})) for mode in _MODES} | {
            "remember": self._choices.get("remember", self.remember)
        }

    def take_notice(self):
        notice, self._notice = self._notice, ""
        return notice

    def update_tracks(self, tracks):
        self._tracks_known = isinstance(tracks, list)
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
        self._tracks_known = False
        self.source_id = None
        self._preferences = {}
        self._notice = ""
        self._notice_checked = False
        self._applied = {}
        self._manual_modes = set()

    def _apply(self, *, manual=False):
        if not self._loaded or not self._tracks_known:
            return
        missing = []
        for mode, prop in _MODES.items():
            choice = self._choices.get(mode)
            if not choice or choice["mode"] == "auto" or mode in self._manual_modes:
                continue
            selected = match_track(self._tracks, mode, choice)
            active = next(
                (
                    track
                    for track in self._tracks
                    if track.get("type") == mode and track.get("selected")
                ),
                None,
            )
            if selected is None:
                if choice.get("lang"):
                    label = "Ses" if mode == "audio" else "Altyazı"
                    fallback = (
                        "varsayılan ses kullanılıyor" if mode == "audio" else "altyazı kapalı"
                    )
                    missing.append(f"{label} dili ({choice['lang']}) bulunamadı; {fallback}.")
                if mode == "sub" and active is not None:
                    selected = "no"
            elif choice.get("lang") and active is not None and not manual:
                # Native loadfile selection owns language selection. Only refine a
                # semantic variant within that language after loading.
                if _language(active.get("lang")) != choice["lang"]:
                    continue
            if active is not None and active.get("id") == selected:
                self._applied[mode] = selected
            elif selected is not None and self._applied.get(mode) != selected:
                self._applied[mode] = selected
                self.player.set_property(prop, selected)
        if not self._notice_checked:
            self._notice = " ".join(missing)[:384]
            self._notice_checked = True

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
        self._choices[mode] = choice or {"mode": "auto"}
        self._choices["remember"] = self.remember
        if self.source_id and self.remember and choice:
            self._preferences[mode] = choice
            self._save()
        return True

    def set_remember(self, enabled, *, generation=None):
        if generation is not None and generation != self.generation:
            return False
        self._preferences["remember"] = bool(enabled)
        self._choices["remember"] = bool(enabled)
        self._save()
        if enabled:
            self._choices = self.preview(self.source_id)
            self._applied.clear()
            self._manual_modes.clear()
            self._apply(manual=True)

    def reset(self, *, generation=None):
        if generation is not None and generation != self.generation:
            return False
        self._preferences = {"remember": self.remember}
        self._choices = {mode: {"mode": "auto"} for mode in _MODES} | {"remember": self.remember}
        self._notice = ""
        self._notice_checked = True
        self._save()
        self._applied.clear()
        self._manual_modes.clear()
        for name, value in DEFAULT_TRACK_OPTIONS.items():
            if name not in _MODES.values():
                self.player.set_property(name, value)
        for prop in _MODES.values():
            self.player.set_property(prop, "auto")

    def _save(self):
        if self.source_id:
            self.store.save_playback_preferences(self.source_id, self._preferences)
