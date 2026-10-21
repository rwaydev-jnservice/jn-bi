from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from database.connection import session_scope
from database.models import JobberAssessment, JobberVisit, ProcessAudit
from services import metrics as MX
from services import segments as SEG
from services.ui import fmt_money, fmt_pct, period_picker, quote_filters, require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Executive")
p = period_picker("exec")

with session_scope() as s:
    quotes = MX.quote_facts(s)
    reqs = MX.request_facts(s)
    jobs = MX.service_job_facts(s)
    now = datetime.now(timezone.utc)
    week = now + timedelta(days=7)
    future_visits = s.scalar(select(func.count()).select_from(JobberVisit).where(JobberVisit.start_at >= now, JobberVisit.start_at < week)) or 0
    future_assess = s.scalar(select(func.count()).select_from(JobberAssessment).where(JobberAssessment.start_at >= now, JobberAssessment.start_at < week)) or 0
    gaps = pd.DataFrame([{"owner": r.owner_name or "(no deal)", "type": r.gap_type} for r in s.scalars(
        select(ProcessAudit).where(ProcessAudit.resolved_at.is_(None), ProcessAudit.severity == "ACTION"))])

q = quote_filters(quotes, "exec")
m = MX.quote_summary(q, p)
n_req = int(MX.in_period(reqs.created_at, p).sum()) if not reqs.empty else 0
n_jobs = int(MX.in_period(jobs.created_at, p).sum()) if not jobs.empty else 0

c = st.columns(4)
c[0].metric("Requests", n_req)
c[1].metric("Direct service jobs", n_jobs)
c[2].metric("Quotes created", m["created"], fmt_money(m["created_value"]), delta_color="off")
c[3].metric("Quotes presented", m["sent"], fmt_money(m["sent_value"]), delta_color="off")
c = st.columns(4)
c[0].metric("Quotes converted", m["converted"], fmt_money(m["converted_value"]), delta_color="off")
c[1].metric("Salesperson conversion", fmt_pct(m["rate"]), f"{m['cohort_converted']} of {m['sent']} presented", delta_color="off")
c[2].metric("Value conversion", fmt_pct(m["value_rate"]))
c[3].metric("Avg converted ticket", fmt_money(m["avg_ticket"]))
c = st.columns(4)
c[0].metric("Unsent quotes", m["unsent"], fmt_money(m["unsent_value"]), delta_color="off")
c[1].metric("Open CRM gaps (action)", len(gaps))
c[2].metric("Visits next 7 days", future_visits)
c[3].metric("Assessments next 7 days", future_assess)
st.caption("Presented = sent, or converted on the spot. Conversion = of the quotes presented in the period, the share converted so far. "
           "'Quotes converted' counts conversions that happened in the period.")

left, right = st.columns(2)
with left:
    st.subheader("By segment")
    rows = []
    for seg in SEG.ORDER:
        sm = MX.quote_summary(quotes[quotes.segment == seg], p) if not quotes.empty else MX.quote_summary(quotes, p)
        rows.append({"Segment": SEG.LABELS[seg], "Presented": sm["sent"], "Presented value": sm["sent_value"],
                     "Conversion": sm["rate"], "Value conversion": sm["value_rate"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Presented value": st.column_config.NumberColumn(format="$%.0f"),
        "Conversion": st.column_config.NumberColumn(format="percent"), "Value conversion": st.column_config.NumberColumn(format="percent")})
    st.caption("All segments, ignoring the filters above.")
with right:
    st.subheader("CRM gaps by Deal Owner")
    if gaps.empty:
        st.success("No open action items.")
    else:
        t = gaps.groupby("owner").size().rename("Action items").reset_index().sort_values("Action items", ascending=False)
        st.dataframe(t.rename(columns={"owner": "Deal Owner"}), hide_index=True, width="stretch")
        st.caption("Details on the Pipeline Audit page.")
