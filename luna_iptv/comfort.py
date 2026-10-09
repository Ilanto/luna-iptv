"""Source-scoped picture and sound controls; own only Luna's audio filter."""

import math

from .i18n import _

DEFAULTS = {
    "aspect": "auto",
    "zoom": 0,
    "brightness": 0,
    "contrast": 0,
    "boost": 100,
    "normalize": False,
}
CHOICES = {
    "aspect": ("auto", "16:9", "4:3", "21:9", "fill"),
    "zoom": (0, 10, 20),
    "brightness": (-20, -10, 0, 10, 20),
    "contrast": (-20, -10, 0, 10, 20),
    "boost": (100, 130, 160),
    "normalize": (False, True),
}


def normalize_comfort(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in DEFAULTS and type(item) is type(DEFAULTS[key]) and item in CHOICES[key]
    }


class ComfortPreferences:
    defaults = DEFAULTS

    def __init__(self, store, player):
        self.store, self.player = store, player
        self.source_id = None
        self.values = dict(DEFAULTS)
        self._filter_added = False
        self._boost_explicit = False

    def begin(self, source_id, *, preserve=False):
        previous = self.values if preserve and source_id == self.source_id else None
        self.source_id = source_id
        saved = self.store.playback_preferences(source_id) if source_id else {}
        if previous is None:
            self._boost_explicit = saved.get("remember", True) and "boost" in saved.get(
                "comfort", {}
            )
        self.values = dict(DEFAULTS) | (
            previous
            if previous is not None
            else saved.get("comfort", {})
            if saved.get("remember", True)
            else {}
        )

    def loaded(self, *, volume=None):
        for key in self.values:
            # Preserve the ordinary volume slider unless a boost was chosen.
            if key == "boost" and not self._boost_explicit and volume is not None:
                self.player.set_property("volume-max", 100)
                self.player.set_property("volume", min(volume, 100))
            else:
                self._apply(key)

    def select(self, key, value):
        if key not in normalize_comfort({key: value}):
            return False
        if key == "boost":
            self._boost_explicit = True
        self.values[key] = value
        self._apply(key)
        if self.source_id:
            saved = self.store.playback_preferences(self.source_id)
            if saved.get("remember", True):
                saved["comfort"] = saved.get("comfort", {}) | {key: value}
                self.store.save_playback_preferences(self.source_id, saved)
        return True

    def _apply(self, key):
        value = self.values[key]
        if key == "aspect":
            self.player.set_property(
                "video-aspect-override", "-1" if value in ("auto", "fill") else value
            )
            self.player.set_property("panscan", int(value == "fill"))
        elif key == "zoom":
            self.player.set_property("video-zoom", math.log2(1 + value / 100))
        elif key == "boost":
            self.player.set_property("volume-max", value)
            self.player.set_property("volume", value)
        elif key == "normalize":
            if value:
                self.player.command(["af", "add", "@luna-norm:dynaudnorm"])
                self._filter_added = True
            elif self._filter_added:
                self.player.command(["af", "remove", "@luna-norm"])
                self._filter_added = False
        else:
            self.player.set_property(key, value)

    def reset(self):
        self._boost_explicit = False
        self.values = dict(DEFAULTS)
        if self.source_id:
            saved = self.store.playback_preferences(self.source_id)
            saved.pop("comfort", None)
            self.store.save_playback_preferences(self.source_id, saved)
        self.loaded()

    def add_menu(self, menu, guard, notify, *, enabled):
        from PySide6.QtGui import QActionGroup

        sections = (
            ("aspect", _("Görüntü oranı"), (_("Otomatik"), "16:9", "4:3", "21:9", _("Doldur"))),
            ("zoom", _("Yakınlaştır"), ("0", "+10%", "+20%")),
            ("brightness", _("Parlaklık"), ("−20", "−10", "0", "+10", "+20")),
            ("contrast", _("Kontrast"), ("−20", "−10", "0", "+10", "+20")),
            ("boost", _("Ses güçlendirme"), ("100%", "130%", "160%")),
        )
        picture = None
        for key, title, labels in sections:
            if key == "brightness":
                picture = menu.addMenu(_("Parlaklık/Kontrast"))
                picture.setEnabled(enabled)
            sub = (picture if key in ("brightness", "contrast") else menu).addMenu(title)
            sub.setEnabled(enabled)
            group = QActionGroup(sub)
            group.setExclusive(True)
            for label, value in zip(labels, CHOICES[key], strict=True):
                action = sub.addAction(label)
                group.addAction(action)
                action.setCheckable(True)
                action.setChecked(self.values[key] == value)

                def choose(checked=False, k=key, v=value, text=f"{title}: {label}"):
                    def apply():
                        self.select(k, v)
                        notify(text)

                    guard(apply)

                action.triggered.connect(choose)
        action = menu.addAction(_("Sesi dengele"))
        action.setEnabled(enabled)
        action.setCheckable(True)
        action.setChecked(self.values["normalize"])

        def normalize(checked):
            def apply():
                self.select("normalize", checked)
                notify(_("Sesi dengele: ") + (_("Açık") if checked else _("Kapalı")))

            guard(apply)

        action.triggered.connect(normalize)
        reset = menu.addAction(_("Varsayılana dön"))
        reset.setEnabled(enabled)

        def restore():
            self.reset()
            notify(_("Görüntü ve ses ayarları varsayılana döndü."))

        reset.triggered.connect(lambda: guard(restore))
