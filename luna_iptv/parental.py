"""PIN hashing and adult category detection without a GUI dependency."""

import hashlib
import hmac
import re
import secrets
import unicodedata

from .i18n import _

_ITERATIONS = 200_000
_ADULT_GROUP = re.compile(
    r"\b(?:xxx|adults?|yetiskin|erotik|erotic|porno?)\b|(?<!\w)(?:18\+|\+18)(?!\w)"
)


def valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and re.fullmatch(r"[0-9]{4,8}", pin) is not None


def hash_pin(pin: str) -> str:
    if not valid_pin(pin):
        raise ValueError(_("PIN 4–8 rakamdan oluşmalı."))
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    if not valid_pin(pin) or not isinstance(stored, str):
        return False
    match = re.fullmatch(
        r"pbkdf2_sha256\$([0-9]{1,7})\$([0-9a-fA-F]{32})\$([0-9a-fA-F]{64})", stored
    )
    if match is None:
        return False
    iterations, salt, expected = match.groups()
    # Bound work even if the stored record is damaged or deliberately oversized.
    rounds = int(iterations)
    if not 1 <= rounds <= 1_000_000:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), bytes.fromhex(salt), rounds)
    return hmac.compare_digest(digest, bytes.fromhex(expected))


def is_adult_group(name: str) -> bool:
    # Match library.search_key's folding without importing its Qt widgets.
    key = "".join(
        c
        for c in unicodedata.normalize("NFKD", name.casefold().replace("ı", "i"))
        if not unicodedata.combining(c)
    )
    return _ADULT_GROUP.search(key) is not None
