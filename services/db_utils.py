"""Small DB helpers shared by the sync services."""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session


def _pk_values(model, row: dict):
    cols = [c.key for c in inspect(model).primary_key]
    vals = tuple(row[c] for c in cols)
    return vals[0] if len(vals) == 1 else vals


def upsert(session: Session, model, row: dict) -> tuple[str, object]:
    """Insert or update by primary key. Returns ('created'|'updated'|'unchanged', obj)."""
    obj = session.get(model, _pk_values(model, row))
    if obj is None:
        obj = model(**row)
        session.add(obj)
        return "created", obj
    changed = False
    for key, value in row.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return ("updated" if changed else "unchanged"), obj


class Stats:
    def __init__(self):
        self.read = 0
        self.created = 0
        self.updated = 0

    def count(self, outcome: str) -> None:
        if outcome == "created":
            self.created += 1
        elif outcome == "updated":
            self.updated += 1
