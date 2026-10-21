"""Process audit: expected Pipedrive state (from Jobber facts) vs actual deal.

READ ONLY. The app never moves, wins or loses a deal; it records the gap and
the Deal Owner is responsible for fixing it in Pipedrive.

Expected state (spec section 34), highest rule wins:
  quote converted                      -> status WON
  quote sent                           -> stage NEGOTIATE
  assessment or visit completed        -> stage QUOTE
  assessment / visit / job scheduled   -> stage VISIT
  otherwise                            -> no expectation

Opportunities audited (created on/after JOBBER_HISTORY_START, not archived):
  QUOTE        every quote (Estimate, Service or unlinked)
  REQUEST      requests that have an assessment but no quote yet
  SERVICE_JOB  direct service jobs with no repair quote found yet
Pipeline 5 (Subcontractors) is not a sales pipeline and is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database import models as M
from services import deal_matching as DM
from services import segments as SEG

STAGE_RANK = {"LEAD": 0, "OPPORTUNITY": 1, "VISIT": 2, "QUOTE": 3, "NEGOTIATE": 4}
NON_SALES_PIPELINES = {5}
REQUIRED_CODE = {"VISIT": "PIPE_STAGE_VISIT_REQUIRED", "QUOTE": "PIPE_STAGE_QUOTE_REQUIRED", "NEGOTIATE": "PIPE_STAGE_NEGOTIATE_REQUIRED"}


def stage_key(name: str | None) -> str | None:
    n = (name or "").strip().lower()
    if n.startswith("negotiat"):
        return "NEGOTIATE"
    return {"lead": "LEAD", "opportunity": "OPPORTUNITY", "visit": "VISIT", "quote": "QUOTE"}.get(n)


@dataclass
class Expected:
    stage: str | None
    status: str | None
    reason: str
    event_at: datetime | None


def expected_state(converted: bool, converted_at=None, sent_at=None, completed_at=None, scheduled_at=None, what="Assessment/visit") -> Expected | None:
    if converted:
        return Expected(None, "WON", "Quote converted in Jobber", converted_at)
    if sent_at:
        return Expected("NEGOTIATE", "open", "Quote sent in Jobber", sent_at)
    if completed_at:
        return Expected("QUOTE", "open", f"{what} completed in Jobber", completed_at)
    if scheduled_at:
        return Expected("VISIT", "open", f"{what} scheduled in Jobber", scheduled_at)
    return None


@dataclass
class Gap:
    gap_type: str
    severity: str  # ACTION | INFO
    reason: str


def evaluate(exp: Expected, deal_status: str | None, deal_stage_name: str | None, opportunity_type: str = "QUOTE") -> list[Gap]:
    actual = stage_key(deal_stage_name)
    if exp.status == "WON":
        if deal_status == "won":
            return []
        if deal_status == "lost":
            return [Gap("STATUS_MISMATCH", "ACTION", "Quote converted in Jobber but the deal is LOST in Pipedrive")]
        return [Gap("PIPE_WON_REQUIRED", "ACTION", f"Quote converted in Jobber; deal still open in {deal_stage_name or '?'}")]
    if deal_status == "won":
        if opportunity_type == "QUOTE":
            return [Gap("STATUS_MISMATCH", "ACTION", "Deal WON in Pipedrive but the quote is not converted in Jobber")]
        return []
    if deal_status == "lost":
        return [Gap("STATUS_MISMATCH", "INFO", f"Deal LOST in Pipedrive while Jobber shows: {exp.reason.lower()}")]
    if actual is None:
        return [Gap("STAGE_UNMAPPED", "INFO", f"Deal stage '{deal_stage_name}' is not part of Visit/Quote/Negotiate")]
    if STAGE_RANK[actual] < STAGE_RANK[exp.stage]:
        return [Gap(REQUIRED_CODE[exp.stage], "ACTION", f"{exp.reason}; deal is in {deal_stage_name}, should be {exp.stage.title()}")]
    if STAGE_RANK[actual] > STAGE_RANK[exp.stage]:
        return [Gap("DEAL_STAGE_AHEAD", "INFO", f"Deal is in {deal_stage_name}, ahead of Jobber ({exp.reason.lower()})")]
    return []


# ---------------------------------------------------------------------------
@dataclass
class Opp:
    key: str
    type: str
    client_id: str | None
    created_at: datetime | None
    quote_id: str | None = None
    exp: Expected | None = None
    segment: str | None = None
    flow_type: str | None = None
    details: dict = field(default_factory=dict)


def _maxdt(*v):
    v = [x for x in v if x]
    return max(v) if v else None


def _mindt(*v):
    v = [x for x in v if x]
    return min(v) if v else None


def rebuild(session: Session, now: datetime | None = None) -> dict:
    cfg = get_settings()
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(cfg.jobber_history_start).replace(tzinfo=timezone.utc)

    people = dict(session.execute(select(M.JobberPerson.user_id, M.JobberPerson.name)).all())
    client_names = dict(session.execute(select(M.JobberClient.client_id, M.JobberClient.name)).all())
    assess = {a.request_id: a for a in session.scalars(select(M.JobberAssessment)) if a.request_id}
    assess_users: dict[str, list[str]] = {}
    for aid, uid in session.execute(select(M.RequestAssignment.assessment_id, M.RequestAssignment.user_id)):
        assess_users.setdefault(aid, []).append(uid)
    visits_by_job: dict[str, list] = {}
    for v in session.scalars(select(M.JobberVisit)):
        if v.job_id:
            visits_by_job.setdefault(v.job_id, []).append(v)
    visit_users: dict[str, list[str]] = {}
    for vid, uid in session.execute(select(M.VisitAssignment.visit_id, M.VisitAssignment.user_id)):
        visit_users.setdefault(vid, []).append(uid)
    service_job_of_quote = {r.to_id: r.from_id for r in session.scalars(select(M.Relationship).where(
        M.Relationship.rel_type == "SERVICE_JOB_REPAIR_QUOTE", M.Relationship.method == "INFERRED", M.Relationship.is_active.is_(True)))}
    jobs_with_repair_quote = {r.from_id for r in session.scalars(select(M.Relationship).where(
        M.Relationship.rel_type == "SERVICE_JOB_REPAIR_QUOTE", M.Relationship.is_active.is_(True)))}

    def techs(user_ids):
        return sorted({people.get(u, u) for u in user_ids})

    def visit_facts(job_id):
        vs = visits_by_job.get(job_id, [])
        return (_maxdt(*[v.completed_at for v in vs]), _mindt(*[v.start_at for v in vs]),
                techs([u for v in vs for u in visit_users.get(v.visit_id, [])]))

    opps: list[Opp] = []
    # --- quotes
    for q in session.scalars(select(M.JobberQuote).where(M.JobberQuote.created_at >= start, M.JobberQuote.status != "archived")):
        completed = scheduled = None
        technicians: list[str] = []
        what = "Assessment/visit"
        origin_req = q.origin_request_id if cfg.use_quote_origin_engine else q.request_id
        origin_job = (q.origin_service_job_id if cfg.use_quote_origin_engine else service_job_of_quote.get(q.quote_id))
        if origin_req and origin_req in assess:
            a = assess[origin_req]
            completed, scheduled, technicians, what = a.completed_at, a.start_at, techs(assess_users.get(a.assessment_id, [])), "Assessment"
        elif origin_job:
            completed, scheduled, technicians = visit_facts(origin_job)
            what = "Service visit"
        exp = expected_state(q.is_converted, q.converted_at or q.transitioned_at, q.sent_at, completed, scheduled, what)
        opps.append(Opp(f"quote:{q.quote_id}", "QUOTE", q.client_id, q.created_at, q.quote_id, exp, q.segment, q.flow_type, {
            "label": f"Quote #{q.quote_number}", "title": q.title, "total": float(q.total or 0), "jobber_url": q.web_uri,
            "technicians": technicians, "salesperson": people.get(q.salesperson_id), "quote_status": q.status}))
    # --- requests with an assessment but no quote (direct, or inferred when the origin engine is on)
    inferred_origin_requests = set()
    if cfg.use_quote_origin_engine:
        inferred_origin_requests = {r for (r,) in session.execute(select(M.JobberQuote.origin_request_id).where(
            M.JobberQuote.origin_request_id.isnot(None)))}
    for r in session.scalars(select(M.JobberRequest).where(M.JobberRequest.created_at >= start, M.JobberRequest.has_quote.is_(False),
                                                           M.JobberRequest.status != "archived")):
        a = assess.get(r.request_id)
        if not a or r.request_id in inferred_origin_requests:
            continue
        exp = expected_state(False, None, None, a.completed_at, a.start_at, "Assessment")
        opps.append(Opp(f"request:{r.request_id}", "REQUEST", r.client_id, r.created_at, None, exp, None, "ESTIMATE", {
            "label": "Request", "title": r.title, "jobber_url": r.web_uri,
            "technicians": techs(assess_users.get(a.assessment_id, [])), "salesperson": people.get(r.salesperson_id)}))
    # --- direct service jobs without a repair quote
    for j in session.scalars(select(M.JobberJob).where(M.JobberJob.created_at >= start, M.JobberJob.is_direct_job.is_(True),
                                                       M.JobberJob.status != "archived")):
        if j.job_id in jobs_with_repair_quote:
            continue
        completed, scheduled, technicians = visit_facts(j.job_id)
        exp = expected_state(False, None, None, completed or j.completed_at, scheduled or j.start_at, "Service visit")
        opps.append(Opp(f"job:{j.job_id}", "SERVICE_JOB", j.client_id, j.created_at, None, exp, None, "SERVICE", {
            "label": f"Job #{j.job_number}", "title": j.title, "total": float(j.total or 0), "jobber_url": j.web_uri,
            "technicians": technicians, "salesperson": people.get(j.salesperson_id)}))

    # --- deals and matches
    stage_names = dict(session.execute(select(M.PipedriveStage.stage_id, M.PipedriveStage.name)).all())
    deals = {d.deal_id: d for d in session.scalars(select(M.PipedriveDeal))}
    quote_matches: dict[str, list] = {}
    for m in session.scalars(select(M.DealMatch).where(M.DealMatch.is_active.is_(True))):
        quote_matches.setdefault(m.quote_id, []).append(m)
    # contact matching for request/job opportunities (not persisted as deal_matches)
    clients = {c.client_id: {"emails": c.emails, "phones": c.phones} for c in session.scalars(select(M.JobberClient))}
    deal_rows = [{"deal_id": d.deal_id, "jobber_quote_id": None, "person_emails": d.person_emails, "person_phones": d.person_phones,
                  "add_time": d.add_time, "update_time": d.update_time, "won_time": d.won_time, "lost_time": d.lost_time,
                  "status": d.status, "value": d.value} for d in deals.values() if d.pipeline_id not in NON_SALES_PIPELINES]
    pseudo = [{"quote_id": o.key, "quote_number": None, "client_id": o.client_id, "created_at": o.created_at, "total": None}
              for o in opps if o.type != "QUOTE"]
    other_matches: dict[str, list] = {}
    for m in DM.match_quotes(pseudo, clients, deal_rows):
        other_matches.setdefault(m.quote_id, []).append(m)

    found: dict[tuple, dict] = {}

    def add(o: Opp, gap: Gap, deal=None, method=None):
        d = dict(o.details)
        d["match_method"] = method
        if deal is not None:
            d.update(deal_title=deal.title, deal_url=f"https://{cfg.pipedrive_company_domain}.pipedrive.com/deal/{deal.deal_id}",
                     pipeline_id=deal.pipeline_id, deal_value=float(deal.value or 0))
        found[(o.key, gap.gap_type)] = {
            "opportunity_key": o.key, "opportunity_type": o.type, "quote_id": o.quote_id, "client_id": o.client_id,
            "deal_id": deal.deal_id if deal else None, "owner_id": deal.owner_id if deal else None,
            "owner_name": deal.owner_name if deal else None,
            "segment": o.segment or (deal.segment if deal else None), "flow_type": o.flow_type,
            "expected_stage": o.exp.stage if o.exp else None, "expected_status": o.exp.status if o.exp else None,
            "actual_stage": stage_names.get(deal.stage_id, "").strip() if deal else None, "actual_status": deal.status if deal else None,
            "gap_type": gap.gap_type, "severity": gap.severity, "reason": gap.reason,
            "last_jobber_event_at": o.exp.event_at if o.exp else o.created_at,
            "last_pipedrive_update_at": deal.update_time if deal else None,
            "details": {**d, "client": client_names.get(o.client_id)},
        }

    for o in opps:
        ms = quote_matches.get(o.quote_id, []) if o.type == "QUOTE" else other_matches.get(o.key, [])
        ms = [m for m in ms if getattr(m, "deal_id", None) in deals and deals[m.deal_id].pipeline_id not in NON_SALES_PIPELINES]
        method_of = lambda m: getattr(m, "match_method", None) or getattr(m, "method", None)  # noqa: E731
        sure = [m for m in ms if method_of(m) != "REVIEW_REQUIRED"]
        if not ms:
            if o.exp or o.type == "QUOTE":
                sev = "INFO" if o.type == "SERVICE_JOB" else "ACTION"
                add(o, Gap("DEAL_NOT_FOUND", sev, "No Pipedrive deal found for this client's email/phone around this date"))
            continue
        if not sure:
            g = Gap("CONTACT_MATCH_AMBIGUOUS", "INFO", f"{len(ms)} possible deals for this client; confirm which one (or fill Quote # in Pipedrive)")
            add(o, g)
            found[(o.key, g.gap_type)]["details"]["candidate_deal_ids"] = [m.deal_id for m in ms]
            continue
        m = sure[0]
        deal = deals[m.deal_id]
        if o.type == "QUOTE" and method_of(m) == "CONTACT_MATCH" and (o.segment == SEG.GC_PROJECT):
            add(o, Gap("JOBBER_QUOTE_ID_MISSING", "INFO", "GC deal linked by contact only; fill Quote # in Pipedrive for an exact link"), deal, method_of(m))
        if o.exp is None:
            continue
        for gap in evaluate(o.exp, deal.status, stage_names.get(deal.stage_id), o.type):
            add(o, gap, deal, method_of(m))

    # --- persist: keep first_detected_at for gaps that persist; resolve the rest
    open_rows = {(r.opportunity_key, r.gap_type): r for r in session.scalars(select(M.ProcessAudit).where(M.ProcessAudit.resolved_at.is_(None)))}
    for key, row in found.items():
        existing = open_rows.get(key)
        if existing is None:
            session.add(M.ProcessAudit(**row, first_detected_at=now, last_seen_at=now))
        else:
            for k, v in row.items():
                setattr(existing, k, v)
            existing.last_seen_at = now
    for key, row in open_rows.items():
        if key not in found:
            row.resolved_at = now
    session.flush()
    counts: dict[str, int] = {}
    for row in found.values():
        counts[row["gap_type"]] = counts.get(row["gap_type"], 0) + 1
    counts["_opportunities"] = len(opps)
    return counts
