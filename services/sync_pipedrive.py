"""Pipedrive -> Postgres sync (READ ONLY on the Pipedrive side).

- users, pipelines, stages: full pull (small)
- deals: every OPEN deal, plus every deal updated since the watermark
  (sorted update_time DESC with early stop; first run = JOBBER_HISTORY_START).
  Each deal embeds its person's email/phone, which is what matching needs.
- deal_stage_history: a row whenever stage/status/value changes between syncs.

Activities/notes ("last contact" detail) come in a later phase; deals already
carry last/next activity dates.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import (
    DealStageHistory, PipedriveDeal, PipedrivePerson, PipedrivePipeline, PipedriveStage, PipedriveUser,
    SyncRun, SyncState, utcnow,
)
from pipedrive import normalize as PN
from pipedrive.client import PipedriveClient
from services.db_utils import Stats, upsert
from services.normalize import parse_ts

log = logging.getLogger(__name__)
SOURCE = "PIPEDRIVE"
ENTITY_ORDER = ["users", "pipelines", "deals"]
JOBBER_QUOTE_FIELD_NAMES = ("jobber quote id", "jobber quote #", "jobber quote number", "quote #", "quote number")


class PipedriveSync:
    def __init__(self, session: Session, client: PipedriveClient, settings: Settings | None = None, now: datetime | None = None):
        self.s = session
        self.c = client
        self.cfg = settings or get_settings()
        self.now = now or datetime.now(timezone.utc)

    def _fields(self):
        fields = list(self.c.get_all("/dealFields"))
        by_name = {(f.get("name") or "").strip().lower(): f for f in fields}
        cat = by_name.get("category")
        category = {"key": cat["key"], "labels": {o["id"]: o["label"] for o in cat.get("options") or []}} if cat else None
        jq = next((by_name[n]["key"] for n in JOBBER_QUOTE_FIELD_NAMES if n in by_name), None)
        return category, jq

    def sync_users(self, stats, watermark):
        for u in self.c.get_all("/users"):
            stats.read += 1
            stats.count(upsert(self.s, PipedriveUser, {"user_id": u["id"], "name": u.get("name"), "email": u.get("email"),
                                                       "is_active": u.get("active_flag"), "raw": u})[0])
        return self.now

    def sync_pipelines(self, stats, watermark):
        for p in self.c.get_all("/pipelines"):
            stats.read += 1
            stats.count(upsert(self.s, PipedrivePipeline, {"pipeline_id": p["id"], "name": p.get("name"), "active": p.get("active"), "raw": p})[0])
        for st in self.c.get_all("/stages"):
            stats.read += 1
            upsert(self.s, PipedriveStage, {"stage_id": st["id"], "pipeline_id": st.get("pipeline_id"),
                                            "name": (st.get("name") or "").strip(), "order_nr": st.get("order_nr"), "raw": st})
        return self.now

    def _save_deal(self, d, category, jq_key, stats, seen):
        if d["id"] in seen:
            return
        seen.add(d["id"])
        stats.read += 1
        row = PN.deal(d, category, jq_key)
        old = self.s.get(PipedriveDeal, row["deal_id"])
        before = (old.stage_id, old.status, old.value) if old else None
        outcome, _ = upsert(self.s, PipedriveDeal, row)
        stats.count(outcome)
        after = (row["stage_id"], row["status"], row["value"])
        if before != after:
            self.s.add(DealStageHistory(deal_id=row["deal_id"], old_stage_id=before[0] if before else None, new_stage_id=row["stage_id"],
                                        old_status=before[1] if before else None, new_status=row["status"],
                                        old_value=before[2] if before else None, new_value=row["value"]))
        person = PN.person_from_embedded(d.get("person_id"))
        if person:
            upsert(self.s, PipedrivePerson, {**person, "raw": d.get("person_id")})

    def sync_deals(self, stats, watermark):
        category, jq_key = self._fields()
        if category is None:
            log.warning("Pipedrive deal field 'Category' not found; segments will be UNCATEGORIZED")
        seen: set = set()
        newest = watermark
        for d in self.c.get_all("/deals", {"status": "open"}):
            self._save_deal(d, category, jq_key, stats, seen)
        if watermark is None:
            since = datetime.fromisoformat(self.cfg.jobber_history_start).replace(tzinfo=timezone.utc)
        else:
            since = watermark - timedelta(minutes=self.cfg.sync_overlap_minutes)

        def stop(d):
            ts = parse_ts(d.get("update_time"))
            return ts is not None and ts < since

        for d in self.c.get_all("/deals", {"status": "all_not_deleted", "sort": "update_time DESC"}, stop_before=stop):
            self._save_deal(d, category, jq_key, stats, seen)
            ts = parse_ts(d.get("update_time"))
            if ts and (newest is None or ts > newest):
                newest = ts
        return newest or self.now

    def run(self, entities=None) -> dict:
        return {e: self.run_entity(e) for e in entities or ENTITY_ORDER}

    def run_entity(self, entity: str) -> str:
        run = SyncRun(source=SOURCE, entity=entity)
        self.s.add(run)
        self.s.commit()
        state = self.s.get(SyncState, (SOURCE, entity))
        stats = Stats()
        try:
            new_wm = getattr(self, f"sync_{entity}")(stats, state.watermark if state else None)
            if state is None:
                state = SyncState(source=SOURCE, entity=entity)
                self.s.add(state)
            state.watermark = new_wm
            run.status = "SUCCESS"
        except Exception as exc:
            self.s.rollback()
            run = self.s.get(SyncRun, run.id)
            run.status = "FAILED"
            run.errors = f"{exc}\n{traceback.format_exc()[-2000:]}"
            log.exception("Pipedrive sync of %s failed", entity)
        run.records_read, run.records_created, run.records_updated = stats.read, stats.created, stats.updated
        run.completed_at = utcnow()
        self.s.commit()
        return run.status
