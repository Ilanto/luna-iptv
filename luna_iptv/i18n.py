"""Turkish source strings with a small, restart-selected English catalog."""

import json
from datetime import date
from functools import lru_cache
from importlib.resources import files

_language = "tr"

_WEEKDAYS = {
    "tr": ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
_WEEKDAYS_FULL = {
    "tr": ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
_MONTHS = {
    "tr": (
        "Ocak",
        "Şubat",
        "Mart",
        "Nisan",
        "Mayıs",
        "Haziran",
        "Temmuz",
        "Ağustos",
        "Eylül",
        "Ekim",
        "Kasım",
        "Aralık",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}
_MONTHS_SHORT = {
    "tr": ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


@lru_cache(maxsize=1)
def _english():
    return json.loads(files("luna_iptv").joinpath("locale/en.json").read_text(encoding="utf-8"))


def set_language(code):
    """Select a supported UI language; unknown settings fall back to Turkish."""
    global _language
    _language = "en" if code == "en" else "tr"


def get_language():
    return _language


def _(text):
    """Translate a source string without touching interpolated user data."""
    return _english().get(text, text) if _language == "en" else text


def N_(text):
    """Mark a deferred label; translate it where it is displayed."""
    return text


def _n(singular, plural, n):
    """Turkish counts keep the singular noun; English distinguishes one."""
    return _(singular if _language == "tr" or n == 1 else plural)


def weekday_name(day, short=True):
    return (_WEEKDAYS if short else _WEEKDAYS_FULL)[_language][day.weekday()]


def month_name(day, short=False):
    return (_MONTHS_SHORT if short else _MONTHS)[_language][day.month - 1]


def day_label(day, today=None):
    today = today or date.today()
    delta = (day - today).days
    if delta == 0:
        return _("Bugün")
    if delta == 1:
        return _("Yarın")
    return f"{weekday_name(day)} {day.day}"


def format_date(day, include_time=False):
    pattern = "%d.%m.%Y" if _language == "tr" else "%d/%m/%Y"
    return day.strftime(pattern + (" %H:%M" if include_time else ""))


def format_number(value, decimals=1, *, grouping=False):
    spec = f",.{decimals}f" if grouping else f".{decimals}f"
    text = format(value, spec)
    return text.translate(str.maketrans(".,", ",.")) if _language == "tr" else text
