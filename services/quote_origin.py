"""Quote ORIGIN classification engine (analytical only; never writes to Jobber).

Precedence (exact):
 1. quote.request_id set                      -> ESTIMATE / DIRECT
 2. strong prior Service Job + Visit           -> SERVICE  / INFERRED
 3. strong Request + Assessment                -> ESTIMATE / INFERRED
 4. more than one plausible candidate          -> REVIEW_REQUIRED / REVIEW_REQUIRED
    (incl. one Service AND one Estimate candidate: never auto-picked)
 5. nothing reliable                           -> OTHER / UNLINKED

Service is evaluated before Estimate: an Estimate is only inferred when NO
Service candidate exists.

Candidates require the same client_id AND property_id (stable IDs). Names,
address text or date proximity alone never create a link.

SERVICE candidate (a "direct" Service/Troubleshooting Job):
  - job.request_id is null and job.origin_quote_id is null (not quote-created)
  - job is not one of this quote's converted jobs
  - job created before the quote
  - a visit of that job STARTED before the quote was created (1h tolerance for
    quotes written on site), and the quote was created no later than
    INFERENCE_WINDOW_DAYS after that visit ended/completed
  - if the job has no visits in the database, the job's own start/completion is
    used instead (lower confidence; origin_visit_id stays empty)
ESTIMATE candidate:
  - request created before the quote, and the request has an Assessment
  - the assessment STARTED before the quote was created (1h tolerance) and the
    quote was created no later than ESTIMATE_INFERENCE_WINDOW_DAYS after it

Origin (where a quote came from) is separate from descendants (Quote ->
converted Job, DIRECT from Jobber): origin_service_job_id is never a converted job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database import models as M

ESTIMATE, SERVICE, REVIEW, OTHER = "ESTIMATE", "SERVICE", "REVIEW_REQUIRED", "OTHER"
DIRECT, INFERRED, UNLINKED = "DIRECT", "INFERRED", "UNLINKED"
START_TOLERANCE = timedelta(hours=1)


@dataclass
class Origin:
    quote_id: str
    flow_type: str
    method: str
    request_id: str | None = None
    assessment_id: str | None = None
    service_job_id: str | None = None
    visit_id: str | None = None
    confidence: float | None = None
    reason: str = ""
    candidate_request_ids: list = field(default_factory=list)
    candidate_service_job_ids: list = field(default_factory=list)
    candidate_visit_ids: list = field(default_factory=list)


@dataclass
class _Cand:
    kind: str  # SERVICE | ESTIMATE
    obj_id: str  # job_id or request_id
    sub_id: str | None  # visit_id or assessment_id
    anchor: datetime  # event the quote followed
    completed: bool
    label: str
    has_visit: bool = True


def _fmt(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%b %d %I:%M %p") if dt else "?"


def _hours(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600


def _service_candidate(q, j, visits, window) -> _Cand | None:
    if j.get("request_id") or j.get("origin_quote_id"):
        return None
    if j["job_id"] in (q.get("converted_job_ids") or []):
        return None
    if j.get("client_id") != q["client_id"] or j.get("property_id") != q["property_id"]:
        return None
    if not j.get("created_at") or j["created_at"] > q["created_at"]:
        return None
    qc = q["created_at"]
    best = None
    for v in visits:
        start = v.get("start_at")
        if not start or start > qc + START_TOLERANCE:
            continue
        anchor = v.get("completed_at") or v.get("end_at") or start
        if qc > anchor + window:
            continue
        if best is None or start > best.get("start_at"):
            best = v
    label = f"Job #{j.get('job_number')}"
    if best is not None:
        return _Cand(SERVICE, j["job_id"], best["visit_id"], best.get("completed_at") or best.get("end_at") or best["start_at"],
                     bool(best.get("completed_at")), label)
    if not visits:  # no visit data for this job: fall back on the job's own dates
        start = j.get("start_at") or j.get("created_at")
        anchor = j.get("completed_at") or start
        if start and start <= qc + START_TOLERANCE and qc <= anchor + window:
            return _Cand(SERVICE, j["job_id"], None, anchor, bool(j.get("completed_at")), label, has_visit=False)
    return None


def _estimate_candidate(q, r, a, window) -> _Cand | None:
    if not a or r.get("client_id") != q["client_id"] or r.get("property_id") != q["property_id"]:
        return None
    if not r.get("created_at") or r["created_at"] > q["created_at"]:
        return None
    start = a.get("start_at")
    qc = q["created_at"]
    if not start or start > qc + START_TOLERANCE:
        return None
    anchor = a.get("completed_at") or a.get("end_at") or start
    if qc > anchor + window:
        return None
    return _Cand(ESTIMATE, r["request_id"], a["assessment_id"], anchor, bool(a.get("completed_at")), "Request")


def classify(quotes, requests, assessments, jobs, visits, service_window_days=None, estimate_window_days=None, tz=None) -> list[Origin]:
    cfg = get_settings()
    tz = tz or cfg.tz
    sw = timedelta(days=service_window_days if service_window_days is not None else cfg.inference_window_days)
    ew = timedelta(days=estimate_window_days if estimate_window_days is not None else cfg.estimate_inference_window_days)
    assess_by_req = {a["request_id"]: a for a in assessments if a.get("request_id")}
    assess_by_id = {a["assessment_id"]: a for a in assessments}
    visits_by_job: dict[str, list] = {}
    for v in visits:
        if v.get("job_id"):
            visits_by_job.setdefault(v["job_id"], []).append(v)
    jobs_by_cp: dict[tuple, list] = {}
    for j in jobs:
        jobs_by_cp.setdefault((j.get("client_id"), j.get("property_id")), []).append(j)
    reqs_by_cp: dict[tuple, list] = {}
    for r in requests:
        reqs_by_cp.setdefault((r.get("client_id"), r.get("property_id")), []).append(r)

    out: list[Origin] = []
    for q in quotes:
        qid = q["quote_id"]
        # 1. DIRECT estimate
        if q.get("request_id"):
            a = assess_by_req.get(q["request_id"])
            out.append(Origin(qid, ESTIMATE, DIRECT, request_id=q["request_id"], assessment_id=a["assessment_id"] if a else None,
                              confidence=1.0, reason="Jobber links this quote to its Request (direct relationship)."))
            continue
        if not q.get("created_at") or not q.get("client_id") or not q.get("property_id"):
            out.append(Origin(qid, OTHER, UNLINKED, reason="Quote has no request and no client/property, so no origin can be inferred."))
            continue
        key = (q["client_id"], q["property_id"])
        svc = [c for j in jobs_by_cp.get(key, []) if (c := _service_candidate(q, j, visits_by_job.get(j["job_id"], []), sw))]
        est = [c for r in reqs_by_cp.get(key, []) if (c := _estimate_candidate(q, r, assess_by_req.get(r["request_id"]), ew))]
        qc = q["created_at"]
        cand_ids = dict(candidate_service_job_ids=[c.obj_id for c in svc], candidate_visit_ids=[c.sub_id for c in svc if c.sub_id],
                        candidate_request_ids=[c.obj_id for c in est])

        if len(svc) == 1 and not est:  # 2. SERVICE inferred
            c = svc[0]
            gap = _hours(qc, c.anchor)
            conf = 0.7 + (0.15 if c.completed and gap <= 48 else 0.05 if gap <= 72 else 0) - (0.15 if not c.has_visit else 0)
            when = f"visit {'completed' if c.completed else 'on'} {_fmt(c.anchor, tz)}" if c.has_visit else f"job dated {_fmt(c.anchor, tz)} (no visit data)"
            out.append(Origin(qid, SERVICE, INFERRED, service_job_id=c.obj_id, visit_id=c.sub_id, confidence=round(min(conf, 0.95), 2),
                              reason=(f"Same client and property. Quote has no request. {c.label} is a direct service job; its {when}, "
                                      f"quote created {_fmt(qc, tz)} ({gap:.0f}h later). Only service candidate; no estimate request candidate."),
                              **cand_ids))
            continue
        if not svc and len(est) == 1:  # 3. ESTIMATE inferred
            c = est[0]
            gap_days = (qc - c.anchor).total_seconds() / 86400
            conf = 0.65 + (0.15 if c.completed else 0) + (0.1 if gap_days <= 7 else 0)
            a = assess_by_id.get(c.sub_id) or {}
            when = _fmt(c.anchor if c.completed else a.get("start_at"), tz)
            out.append(Origin(qid, ESTIMATE, INFERRED, request_id=c.obj_id, assessment_id=c.sub_id, confidence=round(min(conf, 0.9), 2),
                              reason=(f"Same client and property. Quote has no request. A request was created before the quote and its "
                                      f"assessment {'was completed' if c.completed else 'started'} {when}; quote created {_fmt(qc, tz)} "
                                      f"({max(gap_days, 0):.1f} days later). No service job candidate; only estimate candidate."),
                              **cand_ids))
            continue
        if svc or est:  # 4. ambiguous
            parts = []
            if svc:
                parts.append(f"{len(svc)} service job candidate(s): {', '.join(c.label for c in svc)}")
            if est:
                parts.append(f"{len(est)} estimate request candidate(s)")
            out.append(Origin(qid, REVIEW, REVIEW, confidence=round(0.5 / (len(svc) + len(est)), 2),
                              reason="Several plausible origins (" + "; ".join(parts) + "). Not chosen automatically.", **cand_ids))
            continue
        out.append(Origin(qid, OTHER, UNLINKED, reason="No request on the quote and no service job or estimate request for the same "
                                                       "client and property fits the timing."))
    return out


# --- DB ---------------------------------------------------------------------
def _rows(session, model, cols):
    return [dict(zip(cols, r)) for r in session.execute(select(*[getattr(model, c) for c in cols]))]


def load_and_classify(session: Session) -> list[Origin]:
    quotes = _rows(session, M.JobberQuote, ["quote_id", "request_id", "client_id", "property_id", "created_at", "converted_job_ids"])
    requests = _rows(session, M.JobberRequest, ["request_id", "client_id", "property_id", "created_at"])
    assessments = _rows(session, M.JobberAssessment, ["assessment_id", "request_id", "start_at", "end_at", "completed_at"])
    jobs = _rows(session, M.JobberJob, ["job_id", "job_number", "client_id", "property_id", "request_id", "origin_quote_id",
                                        "created_at", "start_at", "completed_at"])
    visits = _rows(session, M.JobberVisit, ["visit_id", "job_id", "start_at", "end_at", "completed_at"])
    return classify(quotes, requests, assessments, jobs, visits)


def rebuild(session: Session) -> dict:
    """Writes origin_* fields only. The legacy flow_type is untouched unless
    USE_QUOTE_ORIGIN_ENGINE is on."""
    return persist(session, load_and_classify(session))


def persist(session: Session, origins: list[Origin]) -> dict:
    cfg = get_settings()
    now = M.utcnow()
    counts: dict[str, int] = {}
    quotes = {q.quote_id: q for q in session.scalars(select(M.JobberQuote))}
    for o in origins:
        q = quotes[o.quote_id]
        q.origin_flow_type, q.origin_method = o.flow_type, o.method
        q.origin_request_id, q.origin_assessment_id = o.request_id, o.assessment_id
        q.origin_service_job_id, q.origin_visit_id = o.service_job_id, o.visit_id
        q.origin_confidence, q.origin_reason = o.confidence, o.reason
        q.candidate_request_ids, q.candidate_service_job_ids, q.candidate_visit_ids = (
            o.candidate_request_ids, o.candidate_service_job_ids, o.candidate_visit_ids)
        q.origin_classified_at = now
        if cfg.use_quote_origin_engine:
            q.flow_type = o.flow_type
        k = f"{o.flow_type}/{o.method}"
        counts[k] = counts.get(k, 0) + 1
    session.flush()
    return counts
