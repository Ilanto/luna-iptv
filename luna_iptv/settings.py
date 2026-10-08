"""Persisted application defaults, separate from each source's remembered choices."""

MOTION_CHOICES = (("Tam", "full"), ("Az", "reduced"), ("Kapalı", "off"))
STARTUP_CHOICES = (
    ("Son kanalı seç", "select"),
    ("Son kanalı oynat", "play"),
    ("Hiçbir şey yapma", "none"),
)
AUTOPLAY_CHOICES = (("Açık", "on"), ("Kapalı", "off"))


def selected_setting(store, key, choices):
    """Use the first choice when an older or damaged setting is unsupported."""
    value = store.setting(key, choices[0][1])
    return next((item for _, item in choices if item == value), choices[0][1])


def playback_defaults(store):
    from .media_dialog import _LANGUAGE_PREFERENCES
    from .preferences import normalize_preferences

    # Only languages the settings window offers: a stale value plays as Otomatik,
    # exactly as the window shows it.
    supported = {code for _, code in _LANGUAGE_PREFERENCES}
    result = {}
    for mode, key in (("audio", "audio_language"), ("sub", "subtitle_language")):
        value = store.setting(key, "auto")
        if value == "off" and mode == "sub":
            choice = {"mode": "off"}
        elif isinstance(value, str) and value in supported:
            choice = {"mode": "track", "lang": value}
        else:
            choice = {"mode": "auto"}
        result[mode] = normalize_preferences({mode: choice}).get(mode, {"mode": "auto"})
    return result
