"""Shared value normalization (dates, money, contact info)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def parse_ts(value: str | None) -> datetime | None:
    """ISO-8601 -> aware UTC datetime. Accepts 'Z', offsets, and Pipedrive's
    'YYYY-MM-DD HH:MM:SS' (which Pipedrive documents as UTC)."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if " " in v and "T" not in v:
        v = v.replace(" ", "T")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def money(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def norm_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e if "@" in e else None


def norm_phone(phone: str | None) -> str | None:
    """Digits only, US numbers reduced to their last 10 digits."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


def uniq(values) -> list:
    out = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out
