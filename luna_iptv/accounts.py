"""Sanitized Xtream account profile state."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountProfile:
    status: str
    created_at: int | None
    expires_at: int | None
    active_connections: int | None
    max_connections: int | None
    checked_at: int


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
        created_at=_number(user_info.get("created_at"), positive=True),
        expires_at=_number(user_info.get("exp_date"), positive=True),
        active_connections=_number(user_info.get("active_cons"), positive=False),
        max_connections=_number(user_info.get("max_connections"), positive=False),
        checked_at=int(time.time()) if checked_at is None else int(checked_at),
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


def _number(value: object, *, positive: bool) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    result = int(number)
    if result < 0 or (positive and result == 0):
        return None
    return result
