"""Sanitized Xtream account profile state."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

SQLITE_INTEGER_MAX = 2**63 - 1
# One UTC day below datetime.max stays renderable after every civil-time offset.
MAX_UNIX_SECONDS = 253_402_214_399


@dataclass(frozen=True)
class AccountProfile:
    status: str
    created_at: int | None
    expires_at: int | None
    active_connections: int | None
    max_connections: int | None
    checked_at: int | None


def normalize_profile(response: object, *, checked_at: int | None = None) -> AccountProfile:
    user_info = response.get("user_info") if isinstance(response, dict) else None
    if not isinstance(user_info, dict):
        user_info = {}

    provider_status = str(user_info.get("status", "")).strip().casefold()
    if provider_status in {"expired", "disabled", "banned"}:
        status = provider_status
    elif provider_status == "active" and user_info.get("auth") in (1, "1", True):
        status = "active"
    else:
        status = "unknown"

    return AccountProfile(
        status=status,
        created_at=bounded_timestamp(user_info.get("created_at")),
        expires_at=bounded_timestamp(user_info.get("exp_date")),
        active_connections=bounded_count(user_info.get("active_cons")),
        max_connections=bounded_count(user_info.get("max_connections")),
        checked_at=bounded_timestamp(int(time.time()) if checked_at is None else checked_at),
    )


def serialize_profile(profile: AccountProfile) -> str:
    return json.dumps(
        {
            "active_connections": profile.active_connections,
            "checked_at": profile.checked_at,
            "created_at": profile.created_at,
            "expires_at": profile.expires_at,
            "max_connections": profile.max_connections,
            "status": profile.status,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def bounded_timestamp(value: object) -> int | None:
    return _bounded_integer(value, minimum=1, maximum=MAX_UNIX_SECONDS)


def bounded_count(value: object) -> int | None:
    return _bounded_integer(value, minimum=0, maximum=SQLITE_INTEGER_MAX)


def sanitize_profile(profile: AccountProfile) -> AccountProfile:
    status = (
        profile.status
        if profile.status in {"active", "expired", "disabled", "banned"}
        else "unknown"
    )
    return AccountProfile(
        status=status,
        created_at=bounded_timestamp(profile.created_at),
        expires_at=bounded_timestamp(profile.expires_at),
        active_connections=bounded_count(profile.active_connections),
        max_connections=bounded_count(profile.max_connections),
        checked_at=bounded_timestamp(profile.checked_at),
    )


def _bounded_integer(value: object, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not number.is_finite() or number < minimum or number > maximum:
        return None
    if number != number.to_integral_value():
        return None
    return int(number)
