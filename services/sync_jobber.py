"""Jobber -> Postgres sync (READ ONLY on the Jobber side).

Per-entity strategy, chosen from what the 2026-05-12 schema supports:
- users        : full pull (small)
- clients      : filter updatedAt.after watermark
- requests     : filter updatedAt.after watermark (includes each request's assessment)
- quotes       : filter updatedAt.after watermark (includes line items)
- jobs         : no updatedAt filter exists -> sort UPDATED_AT desc, stop at watermark
- visits       : rolling startAt window + completedAt.after watermark
- assessments  : scheduledItems(ASSESSMENT) over a rolling window (no root query exists)
- invoices     : filter updatedAt.after watermark

Watermarks only advance after an entity finishes successfully, and each run
re-reads an overlap (SYNC_OVERLAP_MINUTES) so nothing is missed.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import (
    JobberAssessment,
    JobberClient,
    JobberInvoice,
    JobberJob,
    JobberPerson,
    JobberProperty,
    JobberQuote,
    JobberQuoteLineItem,
    JobberRequest,
    JobberVisit,
    QuoteStatusHistory,
    RequestAssignment,
    SyncRun,
    SyncState,
    VisitAssignment,
    utcnow,
)
from jobber import normalize as N
from jobber import queries as Q
from jobber.client import JobberClient as JobberAPI  # NOT the jobber_clients table model
from services.db_utils import Stats, upsert
from services.normalize import parse_ts

log = logging.getLogger(__name__)
SOURCE = "JOBBER"
ENTITY_ORDER = ["users", "clients", "requests", "assessments", "quotes", "jobs", "visits", "invoices"]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobberSync:
    def __init__(self, session: Session, client: JobberAPI, settings: Settings | None = None, now: datetime | None = None):
        self.s = session
        self.client = client
        self.cfg = settings or get_settings()
        self.now = now or datetime.now(timezone.utc)

    # --- helpers --------------------------------------------------------
    def _history_start(self) -> datetime:
        return datetime.fromisoformat(self.cfg.jobber_history_start).replace(tzinfo=timezone.utc)

    def _since(self, watermark: datetime | None) -> datetime:
        if watermark is None:
            return self._history_start()
        return watermark - timedelta(minutes=self.cfg.sync_overlap_minutes)

    def _window(self, watermark: datetime | None) -> tuple[datetime, datetime]:
        start = self._history_start() if watermark is None else self.now - timedelta(days=self.cfg.schedule_lookback_days)
        return start, self.now + timedelta(days=self.cfg.schedule_lookahead_days)

    def _property(self, node, stats: Stats | None = None) -> None:
        row = N.property_(node)
        if row:
            upsert(self.s, JobberProperty, row)

    def _assessment(self, node, request_id=None) -> str | None:
        parsed = N.assessment(node, request_id)
        if not parsed:
            return None
        row, user_ids = parsed
        outcome, _ = upsert(self.s, JobberAssessment, row)
        self.s.execute(delete(RequestAssignment).where(RequestAssignment.assessment_id == row["assessment_id"]))
        for uid in user_ids:
            self.s.add(RequestAssignment(assessment_id=row["assessment_id"], user_id=uid, request_id=row["request_id"]))
        return outcome

    # --- entities -------------------------------------------------------
    def sync_users(self, stats: Stats, watermark):
        for node in self.client.paginate(Q.USERS, "users"):
            stats.read += 1
            stats.count(upsert(self.s, JobberPerson, N.person(node))[0])
        return self.now

    def _by_updated(self, document, root, model, to_row, stats, watermark, extra: Callable | None = None):
        since = self._since(watermark)
        newest = watermark
        filt = {"updatedAt": {"after": iso(since)}}
        for node in self.client.paginate(document, root, {"filter": filt}):
            stats.read += 1
            if extra:
                extra(node)
            stats.count(upsert(self.s, model, to_row(node))[0])
            ts = parse_ts(node.get("updatedAt"))
            if ts and (newest is None or ts > newest):
                newest = ts
        return newest or self.now

    def sync_clients(self, stats, watermark):
        return self._by_updated(Q.CLIENTS, "clients", JobberClient, N.client, stats, watermark)

    def sync_requests(self, stats, watermark):
        def extra(node):
            self._property(node.get("property"))
            self._assessment(node.get("assessment"), node["id"])

        return self._by_updated(Q.REQUESTS, "requests", JobberRequest, N.request, stats, watermark, extra)

    def sync_quotes(self, stats, watermark):
        since = self._since(watermark)
        newest = watermark
        for node in self.client.paginate(Q.QUOTES, "quotes", {"filter": {"updatedAt": {"after": iso(since)}}}, page_size=min(10, self.cfg.jobber_page_size)):  # ~500 cost points per quote (line items)
            stats.read += 1
            self._property(node.get("property"))
            row, line_rows = N.quote(node)
            existing = self.s.get(JobberQuote, row["quote_id"])
            old_status, old_total = (existing.status, existing.total) if existing else (None, None)
            outcome, _ = upsert(self.s, JobberQuote, row)
            stats.count(outcome)
            if existing is None or old_status != row["status"] or old_total != row["total"]:
                self.s.add(QuoteStatusHistory(quote_id=row["quote_id"], old_status=old_status, new_status=row["status"], old_total=old_total, new_total=row["total"]))
            self.s.execute(delete(JobberQuoteLineItem).where(JobberQuoteLineItem.quote_id == row["quote_id"]))
            self.s.flush()
            for li in line_rows:
                self.s.add(JobberQuoteLineItem(**li))
            ts = parse_ts(node.get("updatedAt"))
            if ts and (newest is None or ts > newest):
                newest = ts
        return newest or self.now

    def sync_jobs(self, stats, watermark):
        since = self._since(watermark)
        newest = watermark

        def stop(node):
            ts = parse_ts(node.get("updatedAt"))
            return ts is not None and ts < since

        for node in self.client.paginate(Q.JOBS, "jobs", {"filter": None}, stop_when=stop):
            stats.read += 1
            self._property(node.get("property"))
            stats.count(upsert(self.s, JobberJob, N.job(node))[0])
            ts = parse_ts(node.get("updatedAt"))
            if ts and (newest is None or ts > newest):
                newest = ts
        return newest or self.now

    def _visits(self, filt, stats, seen: set):
        for node in self.client.paginate(Q.VISITS, "visits", {"filter": filt}):
            if node["id"] in seen:
                continue
            seen.add(node["id"])
            stats.read += 1
            row, user_ids = N.visit(node)
            stats.count(upsert(self.s, JobberVisit, row)[0])
            self.s.execute(delete(VisitAssignment).where(VisitAssignment.visit_id == row["visit_id"]))
            for uid in user_ids:
                self.s.add(VisitAssignment(visit_id=row["visit_id"], user_id=uid))

    def sync_visits(self, stats, watermark):
        start, end = self._window(watermark)
        seen: set = set()
        self._visits({"startAt": {"after": iso(start), "before": iso(end)}}, stats, seen)
        if watermark is not None:
            self._visits({"completedAt": {"after": iso(self._since(watermark))}}, stats, seen)
        return self.now

    def sync_assessments(self, stats, watermark):
        start, end = self._window(watermark)
        chunk = timedelta(days=31)
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + chunk, end)
            filt = {"scheduleItemType": "ASSESSMENT", "occursWithin": {"startAt": iso(cursor), "endAt": iso(chunk_end)}}
            for node in self.client.paginate(Q.ASSESSMENTS, "scheduledItems", {"filter": filt}):
                if node.get("__typename") != "Assessment":
                    continue
                stats.read += 1
                stats.count(self._assessment(node) or "unchanged")
            cursor = chunk_end
        return self.now

    def sync_invoices(self, stats, watermark):
        return self._by_updated(Q.INVOICES, "invoices", JobberInvoice, N.invoice, stats, watermark)

    # --- orchestration ----------------------------------------------------
    def run(self, entities: list[str] | None = None) -> dict:
        results = {}
        for entity in entities or ENTITY_ORDER:
            results[entity] = self.run_entity(entity)
        return results

    def run_entity(self, entity: str) -> str:
        fn = getattr(self, f"sync_{entity}")
        run = SyncRun(source=SOURCE, entity=entity)
        self.s.add(run)
        self.s.commit()
        state = self.s.get(SyncState, (SOURCE, entity))
        stats = Stats()
        try:
            new_wm = fn(stats, state.watermark if state else None)
            if state is None:
                state = SyncState(source=SOURCE, entity=entity)
                self.s.add(state)
            state.watermark = new_wm
            run.status = "SUCCESS"
        except Exception as exc:  # record and continue with the next entity
            self.s.rollback()
            run = self.s.get(SyncRun, run.id)
            run.status = "FAILED"
            run.errors = f"{exc}\n{traceback.format_exc()[-2000:]}"
            log.exception("Jobber sync of %s failed", entity)
        run.records_read, run.records_created, run.records_updated = stats.read, stats.created, stats.updated
        run.completed_at = utcnow()
        self.s.commit()
        return run.status
