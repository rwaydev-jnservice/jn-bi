"""Data freshness: last successful sync per source, and visible warnings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import SyncRun


@dataclass
class SourceFreshness:
    source: str
    last_success: datetime | None
    last_failure: datetime | None
    failing_entities: list[str]

    def warning(self, now: datetime | None = None) -> str | None:
        now = now or datetime.now(timezone.utc)
        stale = timedelta(minutes=get_settings().stale_after_minutes)
        if self.last_success is None:
            return f"{self.source}: no successful sync yet — numbers below do not include {self.source} data."
        if self.failing_entities:
            return f"{self.source}: last sync FAILED for {', '.join(self.failing_entities)}. Data may be outdated."
        if now - self.last_success > stale:
            return f"{self.source}: data is stale (last successful sync {int((now - self.last_success).total_seconds() // 60)} min ago)."
        return None


def source_freshness(session: Session, source: str) -> SourceFreshness:
    last_success = session.scalar(select(func.max(SyncRun.completed_at)).where(SyncRun.source == source, SyncRun.status == "SUCCESS"))
    last_failure = session.scalar(select(func.max(SyncRun.completed_at)).where(SyncRun.source == source, SyncRun.status == "FAILED"))
    # An entity is failing if its most recent finished run failed.
    latest = (
        select(SyncRun.entity, func.max(SyncRun.id).label("id"))
        .where(SyncRun.source == source, SyncRun.status != "RUNNING")
        .group_by(SyncRun.entity)
        .subquery()
    )
    failing = [e for e, st in session.execute(select(SyncRun.entity, SyncRun.status).join(latest, SyncRun.id == latest.c.id)) if st == "FAILED"]
    return SourceFreshness(source, last_success, last_failure, sorted(failing))
