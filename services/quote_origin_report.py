"""Builds the quote-origin validation table (used by the script and the review page)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database import models as M

FLOW_ORDER = ["ESTIMATE/DIRECT", "ESTIMATE/INFERRED", "SERVICE/INFERRED", "REVIEW_REQUIRED/REVIEW_REQUIRED", "OTHER/UNLINKED"]
FLOW_LABEL = {
    "ESTIMATE/DIRECT": "Estimate — DIRECT (Jobber link)",
    "ESTIMATE/INFERRED": "Estimate — INFERRED",
    "SERVICE/INFERRED": "Service — INFERRED",
    "REVIEW_REQUIRED/REVIEW_REQUIRED": "Review required",
    "OTHER/UNLINKED": "Other / unlinked",
}


def origin_table(session: Session, since: datetime | None = None) -> pd.DataFrame:
    cfg = get_settings()
    tz = cfg.tz
    since = since or datetime.fromisoformat(cfg.jobber_history_start).replace(tzinfo=timezone.utc)
    clients = dict(session.execute(select(M.JobberClient.client_id, M.JobberClient.name)).all())
    props = {p.property_id: ", ".join(x for x in (p.street1, p.city) if x) for p in session.scalars(select(M.JobberProperty))}
    reqs = {r.request_id: r for r in session.scalars(select(M.JobberRequest))}
    assess = {a.assessment_id: a for a in session.scalars(select(M.JobberAssessment))}
    jobs = {j.job_id: j for j in session.scalars(select(M.JobberJob))}
    visits = {v.visit_id: v for v in session.scalars(select(M.JobberVisit))}
    fmt = lambda d: d.astimezone(tz).strftime("%Y-%m-%d %H:%M") if d else None  # noqa: E731

    rows = []
    for q in session.scalars(select(M.JobberQuote).where(M.JobberQuote.created_at >= since).order_by(M.JobberQuote.created_at)):
        r = reqs.get(q.origin_request_id)
        a = assess.get(q.origin_assessment_id)
        j = jobs.get(q.origin_service_job_id)
        v = visits.get(q.origin_visit_id)
        conv = [jobs[x] for x in (q.converted_job_ids or []) if x in jobs]
        cand_jobs = [f"#{jobs[x].job_number}" if x in jobs else x for x in (q.candidate_service_job_ids or [])]
        cand_reqs = [fmt(reqs[x].created_at)[:10] + " request" if x in reqs else x for x in (q.candidate_request_ids or [])]
        rows.append({
            "Quote Number": f"#{q.quote_number}", "Quote ID": q.quote_id, "Client": clients.get(q.client_id),
            "Property": props.get(q.property_id), "Created At": fmt(q.created_at), "Quote Status": q.status,
            "Total": float(q.total or 0), "Flow Type": q.origin_flow_type, "Origin Method": q.origin_method,
            "key": f"{q.origin_flow_type}/{q.origin_method}", "Legacy Flow": q.flow_type,
            "Jobber request on quote": bool(q.request_id),
            "Origin Request": (f"{fmt(r.created_at)[:10]} — {r.title or 'request'}" if r else None),
            "Origin Assessment": (f"{'completed' if a.completed_at else 'scheduled'} {fmt(a.completed_at or a.start_at)}" if a else None),
            "Origin Service Job": (f"#{j.job_number}" if j else None),
            "Origin Visit": (f"{'completed' if v.completed_at else 'on'} {fmt(v.completed_at or v.start_at)}" if v else None),
            "Converted Job(s)": ", ".join(f"#{c.job_number}" for c in conv) or None,
            "Confidence": q.origin_confidence, "Reason": q.origin_reason,
            "Candidate Service Jobs": ", ".join(cand_jobs) or None, "Candidate Requests": ", ".join(cand_reqs) or None,
            "Open quote": q.web_uri, "Open origin job": j.web_uri if j else None, "Open origin request": r.web_uri if r else None,
        })
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    rows = [{"Classification": "Total quotes", "Quotes": total, "Value": df.Total.sum() if total else 0}]
    for k in FLOW_ORDER:
        part = df[df.key == k] if total else df
        rows.append({"Classification": FLOW_LABEL[k], "Quotes": len(part), "Value": part.Total.sum() if len(part) else 0})
    return pd.DataFrame(rows)


def unlinked_resolution(df: pd.DataFrame) -> pd.DataFrame:
    """Section 13: quotes with NO Jobber request — where did they resolve?"""
    u = df[~df["Jobber request on quote"]]
    rows = []
    for label, flow in (("Resolved to SERVICE", "SERVICE"), ("Resolved to ESTIMATE", "ESTIMATE"),
                        ("Require review", "REVIEW_REQUIRED"), ("Remain unlinked", "OTHER")):
        part = u[u["Flow Type"] == flow]
        rows.append({"Quotes without a Jobber request": label, "Quotes": len(part), "Value": part.Total.sum() if len(part) else 0})
    return pd.DataFrame(rows)
