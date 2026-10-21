"""Relationship (genealogy) engine.

DIRECT          : explicitly exposed by the Jobber API
                  (Request->Quote, Request->Job, Request->Assessment,
                   Quote->converted Job via Job.quote, Job->Visit, Job->Invoice)
INFERRED        : Service Job -> Repair Quote, exactly one strong candidate
REVIEW_REQUIRED : Service Job -> Repair Quote, more than one strong candidate

Service Job -> Repair Quote is NEVER marked DIRECT: the API does not expose it.

A candidate is "strong" only when ALL of these hold:
  - quote.request_id is null
  - the job was not itself created from a quote (job.origin_quote_id is null)
  - the quote did not convert into this same job
  - same client_id AND same property_id (IDs, never names/addresses)
  - quote created between the job's start of lifecycle and
    INFERENCE_WINDOW_DAYS after its last visit / completion
Name, address or date proximity alone never create a relationship.

The core is pure (plain dicts in, edges out) so it is easy to test; persist()
writes the result to the `relationships` table and sets quote.flow_type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from config import get_settings
from database.models import (
    JobberAssessment,
    JobberInvoice,
    JobberJob,
    JobberQuote,
    JobberRequest,
    JobberVisit,
    Relationship,
    VisitAssignment,
    utcnow,
)

DIRECT, INFERRED, REVIEW = "DIRECT", "INFERRED", "REVIEW_REQUIRED"
START_TOLERANCE = timedelta(hours=12)


@dataclass
class Edge:
    from_type: str
    from_id: str
    to_type: str
    to_id: str
    rel_type: str
    method: str
    confidence: float = 1.0
    reason: str = ""
    signals: dict = field(default_factory=dict)

    @property
    def key(self):
        return (self.from_type, self.from_id, self.to_type, self.to_id, self.rel_type)


def _max(*vals):
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def _min(*vals):
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def direct_edges(requests, quotes, jobs, visits, invoices, assessments) -> list[Edge]:
    edges: dict[tuple, Edge] = {}

    def add(e: Edge):
        edges.setdefault(e.key, e)

    for r in requests:
        for qid in r.get("quote_ids") or []:
            add(Edge("REQUEST", r["request_id"], "QUOTE", qid, "REQUEST_QUOTE", DIRECT, reason="Request.quotes"))
        for jid in r.get("job_ids") or []:
            add(Edge("REQUEST", r["request_id"], "JOB", jid, "REQUEST_JOB", DIRECT, reason="Request.jobs"))
        if r.get("assessment_id"):
            add(Edge("REQUEST", r["request_id"], "ASSESSMENT", r["assessment_id"], "REQUEST_ASSESSMENT", DIRECT, reason="Request.assessment"))
    for q in quotes:
        if q.get("request_id"):
            add(Edge("REQUEST", q["request_id"], "QUOTE", q["quote_id"], "REQUEST_QUOTE", DIRECT, reason="Quote.request"))
        for jid in q.get("converted_job_ids") or []:
            add(Edge("QUOTE", q["quote_id"], "JOB", jid, "QUOTE_CONVERTED_JOB", DIRECT, reason="Quote.jobs"))
    for j in jobs:
        if j.get("origin_quote_id"):
            add(Edge("QUOTE", j["origin_quote_id"], "JOB", j["job_id"], "QUOTE_CONVERTED_JOB", DIRECT, reason="Job.quote"))
        if j.get("request_id"):
            add(Edge("REQUEST", j["request_id"], "JOB", j["job_id"], "REQUEST_JOB", DIRECT, reason="Job.request"))
        for iid in j.get("invoice_ids") or []:
            add(Edge("JOB", j["job_id"], "INVOICE", iid, "JOB_INVOICE", DIRECT, reason="Job.invoices"))
    for v in visits:
        if v.get("job_id"):
            add(Edge("JOB", v["job_id"], "VISIT", v["visit_id"], "JOB_VISIT", DIRECT, reason="Visit.job"))
    for inv in invoices:
        for jid in inv.get("job_ids") or []:
            add(Edge("JOB", jid, "INVOICE", inv["invoice_id"], "JOB_INVOICE", DIRECT, reason="Invoice.jobs"))
    for a in assessments:
        if a.get("request_id"):
            add(Edge("REQUEST", a["request_id"], "ASSESSMENT", a["assessment_id"], "REQUEST_ASSESSMENT", DIRECT, reason="Assessment.request"))
    return list(edges.values())


def infer_service_repair_quotes(quotes, jobs, visits, visit_users: dict[str, list[str]] | None = None, window_days: int | None = None) -> list[Edge]:
    """Service Job -> Repair Quote edges (INFERRED / REVIEW_REQUIRED only)."""
    window = timedelta(days=window_days if window_days is not None else get_settings().inference_window_days)
    visit_users = visit_users or {}
    visits_by_job: dict[str, list[dict]] = {}
    for v in visits:
        if v.get("job_id"):
            visits_by_job.setdefault(v["job_id"], []).append(v)

    # Lifecycle window per eligible service job
    service_jobs = []
    for j in jobs:
        if j.get("origin_quote_id"):
            continue  # quote-converted jobs are not "original service jobs"
        if not j.get("client_id") or not j.get("property_id") or not j.get("created_at"):
            continue
        jv = visits_by_job.get(j["job_id"], [])
        start = _min(j["created_at"], *[v.get("start_at") for v in jv])
        last = _max(j.get("completed_at"), j["created_at"], *[_max(v.get("completed_at"), v.get("end_at"), v.get("start_at")) for v in jv])
        service_jobs.append((j, jv, start - START_TOLERANCE, last + window))

    edges: list[Edge] = []
    for q in quotes:
        if q.get("request_id") or not q.get("created_at") or not q.get("client_id") or not q.get("property_id"):
            continue
        converted = set(q.get("converted_job_ids") or [])
        candidates = []
        for j, jv, w_start, w_end in service_jobs:
            if j["job_id"] in converted:
                continue
            if j["client_id"] != q["client_id"] or j["property_id"] != q["property_id"]:
                continue
            if not (w_start <= q["created_at"] <= w_end):
                continue
            signals = {
                "same_client_id": True,
                "same_property_id": True,
                "quote_request_null": True,
                "job_request_null": j.get("request_id") is None,
                "job_source": j.get("source"),
                "quote_created_at": q["created_at"].isoformat(),
                "job_created_at": j["created_at"].isoformat(),
                "window_end": w_end.isoformat(),
            }
            # Closeness to an actual visit (strengthens, never required)
            visit_times = [t for v in jv for t in (v.get("completed_at"), v.get("start_at")) if t]
            if visit_times:
                gap_h = min(abs((q["created_at"] - t).total_seconds()) / 3600 for t in visit_times)
                signals["hours_from_nearest_visit"] = round(gap_h, 1)
            techs = sorted({u for v in jv for u in visit_users.get(v["visit_id"], [])})
            signals["visit_technician_ids"] = techs
            signals["salesperson_attended_visit"] = bool(q.get("salesperson_id") and q["salesperson_id"] in techs)
            candidates.append((j, signals))

        if not candidates:
            continue
        if len(candidates) == 1:
            j, sig = candidates[0]
            conf = 0.7
            if sig.get("hours_from_nearest_visit") is not None and sig["hours_from_nearest_visit"] <= 48:
                conf += 0.15
            if sig["job_request_null"]:
                conf += 0.05
            if sig["salesperson_attended_visit"]:
                conf += 0.05
            edges.append(Edge(
                "JOB", j["job_id"], "QUOTE", q["quote_id"], "SERVICE_JOB_REPAIR_QUOTE", INFERRED, round(min(conf, 0.95), 2),
                "Only service job for the same client+property whose lifecycle contains the quote creation; quote has no request.",
                sig,
            ))
        else:
            ids = [c[0]["job_id"] for c in candidates]
            for j, sig in candidates:
                sig = {**sig, "competing_job_ids": [i for i in ids if i != j["job_id"]]}
                edges.append(Edge(
                    "JOB", j["job_id"], "QUOTE", q["quote_id"], "SERVICE_JOB_REPAIR_QUOTE", REVIEW, round(0.5 / len(candidates), 2),
                    f"{len(candidates)} service jobs for the same client+property could have produced this quote; needs human review.",
                    sig,
                ))
    return edges


def quote_flow_types(quotes, edges: list[Edge]) -> dict[str, str]:
    service = {e.to_id for e in edges if e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"}
    out = {}
    for q in quotes:
        if q.get("request_id"):
            out[q["quote_id"]] = "ESTIMATE"
        elif q["quote_id"] in service:
            out[q["quote_id"]] = "SERVICE"
        else:
            out[q["quote_id"]] = "UNLINKED"
    return out


def build(requests, quotes, jobs, visits, invoices, assessments, visit_users=None, window_days=None):
    edges = direct_edges(requests, quotes, jobs, visits, invoices, assessments)
    edges += infer_service_repair_quotes(quotes, jobs, visits, visit_users, window_days)
    return edges, quote_flow_types(quotes, edges)


def origin_edges(origins) -> list[Edge]:
    """Inferred/review edges from the quote origin engine (used when USE_QUOTE_ORIGIN_ENGINE is on)."""
    edges = []
    for o in origins:
        if o.method == INFERRED and o.service_job_id:
            edges.append(Edge("JOB", o.service_job_id, "QUOTE", o.quote_id, "SERVICE_JOB_REPAIR_QUOTE", INFERRED, o.confidence or 0, o.reason,
                              {"origin_visit_id": o.visit_id}))
        elif o.method == INFERRED and o.request_id:
            edges.append(Edge("REQUEST", o.request_id, "QUOTE", o.quote_id, "REQUEST_QUOTE", INFERRED, o.confidence or 0, o.reason,
                              {"origin_assessment_id": o.assessment_id}))
        elif o.method == REVIEW:
            for jid in o.candidate_service_job_ids:
                edges.append(Edge("JOB", jid, "QUOTE", o.quote_id, "SERVICE_JOB_REPAIR_QUOTE", REVIEW, o.confidence or 0, o.reason))
            for rid in o.candidate_request_ids:
                edges.append(Edge("REQUEST", rid, "QUOTE", o.quote_id, "REQUEST_QUOTE", REVIEW, o.confidence or 0, o.reason))
    return edges


# --- persistence ------------------------------------------------------------
def _rows(session: Session, model, cols: list[str]) -> list[dict]:
    return [dict(zip(cols, r)) for r in session.execute(select(*[getattr(model, c) for c in cols]))]


def rebuild(session: Session, window_days: int | None = None) -> dict:
    requests = _rows(session, JobberRequest, ["request_id", "quote_ids", "job_ids", "assessment_id"])
    quotes = _rows(session, JobberQuote, ["quote_id", "request_id", "client_id", "property_id", "created_at", "converted_job_ids", "salesperson_id"])
    jobs = _rows(session, JobberJob, ["job_id", "request_id", "origin_quote_id", "client_id", "property_id", "created_at", "completed_at", "source", "invoice_ids"])
    visits = _rows(session, JobberVisit, ["visit_id", "job_id", "start_at", "end_at", "completed_at"])
    invoices = _rows(session, JobberInvoice, ["invoice_id", "job_ids"])
    assessments = _rows(session, JobberAssessment, ["assessment_id", "request_id"])
    visit_users: dict[str, list[str]] = {}
    for vid, uid in session.execute(select(VisitAssignment.visit_id, VisitAssignment.user_id)):
        visit_users.setdefault(vid, []).append(uid)

    if get_settings().use_quote_origin_engine:
        from services import quote_origin

        origins = quote_origin.load_and_classify(session)
        quote_origin.persist(session, origins)  # origin_* fields always match the edges below
        edges = direct_edges(requests, quotes, jobs, visits, invoices, assessments) + origin_edges(origins)
        flows = {o.quote_id: o.flow_type for o in origins}
    else:
        edges, flows = build(requests, quotes, jobs, visits, invoices, assessments, visit_users, window_days)

    existing = {(r.from_type, r.from_id, r.to_type, r.to_id, r.rel_type): r for r in session.scalars(select(Relationship))}
    produced = set()
    counts = {DIRECT: 0, INFERRED: 0, REVIEW: 0}
    for e in edges:
        produced.add(e.key)
        counts[e.method] += 1
        row = existing.get(e.key)
        if row is None:
            session.add(Relationship(from_type=e.from_type, from_id=e.from_id, to_type=e.to_type, to_id=e.to_id, rel_type=e.rel_type,
                                     method=e.method, confidence=e.confidence, reason=e.reason, signals=e.signals))
        elif row.confirmed_at is None:  # never overwrite a human decision
            row.method, row.confidence, row.reason, row.signals, row.is_active = e.method, e.confidence, e.reason, e.signals, True
    for key, row in existing.items():
        if key not in produced and row.confirmed_at is None and row.is_active:
            row.is_active = False
            row.updated_at = utcnow()
    for qid, flow in flows.items():
        session.execute(update(JobberQuote).where(JobberQuote.quote_id == qid).values(flow_type=flow))
    session.flush()
    return counts
