"""Pipedrive API v1 objects -> row dicts. Pure functions."""

from __future__ import annotations

from services.normalize import money, norm_email, norm_phone, parse_ts, uniq
from services.segments import segment_for_category


def _ref_id(v):
    """v1 returns related objects either as an int or as {"value": id, ...}."""
    if isinstance(v, dict):
        return v.get("value") or v.get("id")
    return v


def _contacts(items) -> list:
    if isinstance(items, list):
        return [i.get("value") if isinstance(i, dict) else i for i in items]
    return [items] if items else []


def person_from_embedded(p) -> dict | None:
    if not isinstance(p, dict) or not p.get("value"):
        return None
    return {
        "person_id": p["value"],
        "name": p.get("name"),
        "owner_id": p.get("owner_id"),
        "emails": uniq(norm_email(e) for e in _contacts(p.get("email"))),
        "phones": uniq(norm_phone(x) for x in _contacts(p.get("phone"))),
    }


def deal(d: dict, category_field: dict | None = None, jobber_quote_field_key: str | None = None) -> dict:
    """category_field: {"key": <hash>, "labels": {option_id: label}}"""
    category = None
    if category_field:
        raw = d.get(category_field["key"])
        if raw not in (None, ""):
            category = category_field["labels"].get(int(raw) if str(raw).isdigit() else raw)
    person = person_from_embedded(d.get("person_id"))
    user = d.get("user_id")
    return {
        "deal_id": d["id"],
        "title": d.get("title"),
        "person_id": _ref_id(d.get("person_id")),
        "org_id": _ref_id(d.get("org_id")),
        "owner_id": _ref_id(user),
        "owner_name": (user.get("name") if isinstance(user, dict) else None) or d.get("owner_name"),
        "pipeline_id": d.get("pipeline_id"),
        "stage_id": d.get("stage_id"),
        "status": d.get("status"),
        "value": money(d.get("value")),
        "currency": d.get("currency"),
        "add_time": parse_ts(d.get("add_time")),
        "update_time": parse_ts(d.get("update_time")),
        "stage_change_time": parse_ts(d.get("stage_change_time")),
        "won_time": parse_ts(d.get("won_time")),
        "lost_time": parse_ts(d.get("lost_time")),
        "last_activity_date": d.get("last_activity_date"),
        "next_activity_date": d.get("next_activity_date"),
        "next_activity_time": d.get("next_activity_time"),
        "next_activity_id": d.get("next_activity_id"),
        "last_activity_id": d.get("last_activity_id"),
        "jobber_quote_id": (d.get(jobber_quote_field_key) or None) if jobber_quote_field_key else None,
        "category": category,
        "segment": segment_for_category(category),
        "person_emails": person["emails"] if person else [],
        "person_phones": person["phones"] if person else [],
        "raw": d,
    }
