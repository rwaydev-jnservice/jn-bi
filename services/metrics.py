"""Metrics. One fact table per object, one set of reusable calculations.

Definitions (shown in the UI too):
- Quotes Created / Sent / Converted: counted by the date of that event
  (created_at / sent_at / converted_at) falling in the period.
- Salesperson Conversion Rate: of the quotes PRESENTED in the period (sent, or
  converted on the spot without a send), the share converted as of today
  (cohort). Value Conversion Rate: same, by $. "Presented" figures use this cohort.
- Average Converted Ticket: converted value / converted count (converted in period).
- Unsent: quotes created in the period that have no sent date and are not
  archived/converted.
Salesperson = Jobber Quote.salesperson. Technician = users assigned to the
Assessment (estimates) or to the service job's visits (service). Kept separate.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database import models as M
from services.periods import Period


FLOW_LABELS = {"ESTIMATE": "Estimate", "SERVICE": "Service", "REVIEW_REQUIRED": "Review required (unreviewed)", "OTHER": "Other / unlinked"}


def _ts(series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def in_period(series: pd.Series, p: Period) -> pd.Series:
    s = _ts(series)
    return (s >= pd.Timestamp(p.start_utc)) & (s < pd.Timestamp(p.end_utc))


# ---------------------------------------------------------------------------
# Fact tables
# ---------------------------------------------------------------------------
def _people(session):
    return dict(session.execute(select(M.JobberPerson.user_id, M.JobberPerson.name)).all())


def quote_facts(session: Session) -> pd.DataFrame:
    cfg = get_settings()
    people = _people(session)
    clients = dict(session.execute(select(M.JobberClient.client_id, M.JobberClient.name)).all())
    cls = {c.quote_id: c for c in session.scalars(select(M.QuoteClassification))}
    assess = {a.request_id: a.assessment_id for a in session.scalars(select(M.JobberAssessment)) if a.request_id}
    a_users: dict[str, list] = {}
    for aid, uid in session.execute(select(M.RequestAssignment.assessment_id, M.RequestAssignment.user_id)):
        a_users.setdefault(aid, []).append(uid)
    v_users: dict[str, set] = {}
    for jid, uid in session.execute(select(M.JobberVisit.job_id, M.VisitAssignment.user_id).join(
            M.VisitAssignment, M.VisitAssignment.visit_id == M.JobberVisit.visit_id)):
        v_users.setdefault(jid, set()).add(uid)
    jobs = {j.job_id: j for j in session.scalars(select(M.JobberJob))}
    service_rel = {}
    for r in session.scalars(select(M.Relationship).where(M.Relationship.rel_type == "SERVICE_JOB_REPAIR_QUOTE", M.Relationship.is_active.is_(True))):
        service_rel.setdefault(r.to_id, []).append(r)
    deals = {d.deal_id: d for d in session.scalars(select(M.PipedriveDeal))}
    stages = dict(session.execute(select(M.PipedriveStage.stage_id, M.PipedriveStage.name)).all())
    match = {}
    for m in session.scalars(select(M.DealMatch).where(M.DealMatch.is_active.is_(True), M.DealMatch.match_method != "REVIEW_REQUIRED")):
        match.setdefault(m.quote_id, m)

    rows = []
    for q in session.scalars(select(M.JobberQuote)):
        techs: set = set()
        service_job = None
        rel_method = None
        origin_req = q.origin_request_id if cfg.use_quote_origin_engine else q.request_id
        if origin_req and origin_req in assess:
            techs |= set(a_users.get(assess[origin_req], []))
        rels = service_rel.get(q.quote_id, [])
        if len(rels) == 1 or (rels and all(r.method == "INFERRED" for r in rels)):
            service_job = jobs.get(rels[0].from_id)
            rel_method = rels[0].method
            techs |= v_users.get(rels[0].from_id, set())
        elif rels:
            rel_method = "REVIEW_REQUIRED"
        c = cls.get(q.quote_id)
        m = match.get(q.quote_id)
        d = deals.get(m.deal_id) if m else None
        conv_jobs = [jobs[j] for j in (q.converted_job_ids or []) if j in jobs]
        rows.append({
            "quote_id": q.quote_id, "Quote": f"#{q.quote_number}", "title": q.title, "status": q.status,
            "created_at": q.created_at, "sent_at": q.sent_at,
            "converted_at": (q.converted_at or q.transitioned_at) if q.is_converted else None,
            "is_converted": bool(q.is_converted), "total": float(q.total or 0),
            "salesperson_id": q.salesperson_id, "Salesperson": people.get(q.salesperson_id) or "(none)",
            "Technician": ", ".join(sorted(people.get(t, t) for t in techs)) or "—",
            "technician_ids": sorted(techs),
            "Flow": FLOW_LABELS.get(q.flow_type, "Other / unlinked"),
            "origin_method": q.origin_method, "origin_confidence": q.origin_confidence, "origin_reason": q.origin_reason,
            "segment": q.segment or "NO_DEAL_MATCH",
            "Department": c.department if c else "OTHER", "Category": c.category if c else "OTHER", "System": c.system if c else "OTHER",
            "Client": clients.get(q.client_id), "request_id": q.request_id,
            "Service Job": f"#{service_job.job_number}" if service_job else None,
            "service_job_url": service_job.web_uri if service_job else None,
            "service_job_status": service_job.status if service_job else None,
            "service_job_total": float(service_job.total or 0) if service_job else None,
            "relationship": rel_method,
            "Converted Job": ", ".join(f"#{j.job_number}" for j in conv_jobs) or None,
            "converted_job_url": conv_jobs[0].web_uri if conv_jobs else None,
            "Deal": d.title if d else None, "Deal Owner": d.owner_name if d else None,
            "Deal Stage": f"{(stages.get(d.stage_id) or '').strip()} / {d.status}" if d else None,
            "deal_url": f"https://{cfg.pipedrive_company_domain}.pipedrive.com/deal/{d.deal_id}" if d else None,
            "Open in Jobber": q.web_uri,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ("created_at", "sent_at", "converted_at"):
            df[col] = _ts(df[col])
    return df


def request_facts(session: Session) -> pd.DataFrame:
    people = _people(session)
    assess = {a.request_id: a for a in session.scalars(select(M.JobberAssessment)) if a.request_id}
    a_users: dict[str, list] = {}
    for aid, uid in session.execute(select(M.RequestAssignment.assessment_id, M.RequestAssignment.user_id)):
        a_users.setdefault(aid, []).append(uid)
    inferred = set()
    if get_settings().use_quote_origin_engine:
        inferred = {rid for (rid,) in session.execute(select(M.JobberQuote.origin_request_id).where(
            M.JobberQuote.origin_method == "INFERRED", M.JobberQuote.origin_request_id.isnot(None)))}
    rows = []
    for r in session.scalars(select(M.JobberRequest)):
        a = assess.get(r.request_id)
        techs = a_users.get(a.assessment_id, []) if a else []
        rows.append({
            "request_id": r.request_id, "created_at": r.created_at, "status": r.status,
            "has_quote": bool(r.has_quote) or r.request_id in inferred, "quote_link": (
                "DIRECT" if r.has_quote else "INFERRED" if r.request_id in inferred else None),
            "assessment_start": a.start_at if a else None, "assessment_completed": a.completed_at if a else None,
            "Technician": ", ".join(sorted(people.get(t, t) for t in techs)) or "(unassigned)", "technician_ids": techs,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ("created_at", "assessment_start", "assessment_completed"):
            df[col] = _ts(df[col])
    return df


def service_job_facts(session: Session) -> pd.DataFrame:
    people = _people(session)
    v_users: dict[str, set] = {}
    for jid, uid in session.execute(select(M.JobberVisit.job_id, M.VisitAssignment.user_id).join(
            M.VisitAssignment, M.VisitAssignment.visit_id == M.JobberVisit.visit_id)):
        v_users.setdefault(jid, set()).add(uid)
    with_quote = {r.from_id for r in session.scalars(select(M.Relationship).where(
        M.Relationship.rel_type == "SERVICE_JOB_REPAIR_QUOTE", M.Relationship.is_active.is_(True)))}
    rows = []
    for j in session.scalars(select(M.JobberJob).where(M.JobberJob.is_direct_job.is_(True))):
        techs = v_users.get(j.job_id, set())
        rows.append({
            "job_id": j.job_id, "Job": f"#{j.job_number}", "title": j.title, "status": j.status, "created_at": j.created_at,
            "completed_at": j.completed_at, "total": float(j.total or 0), "has_repair_quote": j.job_id in with_quote,
            "Technician": ", ".join(sorted(people.get(t, t) for t in techs)) or "(unassigned)", "technician_ids": sorted(techs),
            "Open in Jobber": j.web_uri,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ("created_at", "completed_at"):
            df[col] = _ts(df[col])
    return df


# ---------------------------------------------------------------------------
# Calculations (pure)
# ---------------------------------------------------------------------------
UNSENT_EXCLUDED = {"archived", "converted"}


def quote_summary(df: pd.DataFrame, p: Period) -> dict:
    if df.empty:
        return {k: 0 for k in ("created", "created_value", "sent", "sent_value", "unsent", "unsent_value", "converted",
                               "converted_value", "avg_ticket", "cohort_converted", "cohort_converted_value")} | {"rate": None, "value_rate": None}
    created = df[in_period(df.created_at, p)]
    sent = df[in_period(df.sent_at, p)]
    conv = df[in_period(df.converted_at, p)]
    unsent = created[created.sent_at.isna() & ~created.status.isin(UNSENT_EXCLUDED)]
    # Conversion cohort = quotes PRESENTED in the period: sent, or converted without ever
    # being sent (approved on the spot). Otherwise on-the-spot sales vanish from both sides.
    presented = df[in_period(df.sent_at.fillna(df.converted_at), p)]
    cohort_conv = presented[presented.is_converted]
    sv = presented.total.sum()
    return {
        "created": len(created), "created_value": created.total.sum(),
        "sent": len(presented), "sent_value": sv, "sent_only": len(sent),
        "unsent": len(unsent), "unsent_value": unsent.total.sum(),
        "converted": len(conv), "converted_value": conv.total.sum(),
        "avg_ticket": conv.total.sum() / len(conv) if len(conv) else None,
        "cohort_converted": len(cohort_conv), "cohort_converted_value": cohort_conv.total.sum(),
        "rate": len(cohort_conv) / len(presented) if len(presented) else None,
        "value_rate": cohort_conv.total.sum() / sv if sv else None,
    }


def by_salesperson(df: pd.DataFrame, p: Period) -> pd.DataFrame:
    rows = []
    if df.empty:
        return pd.DataFrame()
    for name, g in df.groupby("Salesperson"):
        s = quote_summary(g, p)
        if not (s["created"] or s["sent"] or s["converted"]):
            continue
        rows.append({"Salesperson": name, "Created": s["created"], "Presented": s["sent"], "Presented value": s["sent_value"],
                     "Converted (of presented)": s["cohort_converted"], "Conversion": s["rate"],
                     "Converted value (of presented)": s["cohort_converted_value"], "Value conversion": s["value_rate"],
                     "Converted in period": s["converted"], "Avg converted ticket": s["avg_ticket"],
                     "Unsent": s["unsent"], "Unsent value": s["unsent_value"]})
    return pd.DataFrame(rows).sort_values("Presented value", ascending=False) if rows else pd.DataFrame()


def unsent_quotes(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    u = df[df.sent_at.isna() & ~df.status.isin(UNSENT_EXCLUDED)].copy()
    u["Age (days)"] = (now - u.created_at).dt.days
    return u.sort_values("Age (days)", ascending=False)


def explode_technicians(df: pd.DataFrame, col: str = "Technician") -> pd.DataFrame:
    """One row per technician for 'by technician' counts (a request with 2 techs counts for both)."""
    out = df.copy()
    out[col] = out[col].str.split(", ")
    return out.explode(col)
