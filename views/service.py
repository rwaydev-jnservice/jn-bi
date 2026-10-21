import streamlit as st

from database.connection import session_scope
from services import metrics as MX
from services.ui import fmt_money, fmt_pct, period_picker, quote_filters, require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Service")
st.caption("Service Job → Repair Quote (INFERRED: same client + property + timing) → Converted Job (DIRECT). "
           "Technician = who was assigned to the service job's visits.")
p = period_picker("svc")

with session_scope() as s:
    quotes = MX.quote_facts(s)
    jobs = MX.service_job_facts(s)

j = jobs[MX.in_period(jobs.created_at, p)] if not jobs.empty else jobs
done = jobs[MX.in_period(jobs.completed_at, p)] if not jobs.empty else jobs
c = st.columns(4)
c[0].metric("Direct service jobs", len(j), fmt_money(j.total.sum() if not j.empty else 0), delta_color="off")
c[1].metric("Completed (in period)", len(done))
c[2].metric("Jobs that produced a repair quote", int(j.has_repair_quote.sum()) if not j.empty else 0)
c[3].metric("Repair quote rate", fmt_pct(j.has_repair_quote.mean() if len(j) else None))

q = quotes[quotes.Flow == "Service"] if not quotes.empty else quotes
q = quote_filters(q, "svc", show_flow=False)
m = MX.quote_summary(q, p)
st.subheader("Repair quotes")
c = st.columns(5)
c[0].metric("Created", m["created"], fmt_money(m["created_value"]), delta_color="off")
c[1].metric("Presented", m["sent"], fmt_money(m["sent_value"]), delta_color="off")
c[2].metric("Not sent", m["unsent"], fmt_money(m["unsent_value"]), delta_color="off")
c[3].metric("Converted (in period)", m["converted"], fmt_money(m["converted_value"]), delta_color="off")
c[4].metric("Avg converted ticket", fmt_money(m["avg_ticket"]))
c = st.columns(2)
c[0].metric("Salesperson conversion (of quotes presented in period)", fmt_pct(m["rate"]), f"{m['cohort_converted']} of {m['sent']}", delta_color="off")
c[1].metric("Value conversion", fmt_pct(m["value_rate"]), f"{fmt_money(m['cohort_converted_value'])} of {fmt_money(m['sent_value'])}", delta_color="off")

money = st.column_config.NumberColumn(format="$%.0f")
pct = st.column_config.NumberColumn(format="percent")
st.subheader("By salesperson")
sp = MX.by_salesperson(q, p)
if sp.empty:
    st.caption("No repair quotes in this period.")
else:
    st.dataframe(sp, hide_index=True, width="stretch", column_config={
        "Presented value": money, "Converted value (of presented)": money, "Avg converted ticket": money, "Unsent value": money,
        "Conversion": pct, "Value conversion": pct})

st.subheader("Service jobs by technician")
if not j.empty:
    t = (MX.explode_technicians(j).groupby("Technician")
         .agg(Jobs=("job_id", "count"), Value=("total", "sum"), **{"With repair quote": ("has_repair_quote", "sum")}).reset_index())
    st.dataframe(t.sort_values("Jobs", ascending=False), hide_index=True, width="stretch", column_config={"Value": money})

st.subheader("Service chain: original job → repair quote → converted job")
chain = q[MX.in_period(q.created_at, p)] if not q.empty else q
if chain.empty:
    st.caption("No repair quotes created in this period.")
else:
    chain = chain.assign(**{"Link": chain.relationship.map({"INFERRED": "Job→Quote inferred", "REVIEW_REQUIRED": "Needs review"}).fillna("")})
    st.dataframe(chain[["Client", "Service Job", "service_job_url", "Technician", "Quote", "Open in Jobber", "status", "total",
                        "Salesperson", "Department", "Category", "System", "Converted Job", "converted_job_url",
                        "Deal", "Deal Owner", "Deal Stage", "deal_url", "Link"]],
                 hide_index=True, width="stretch", column_config={
                     "service_job_url": st.column_config.LinkColumn("Job link", display_text="Open"),
                     "Open in Jobber": st.column_config.LinkColumn("Quote link", display_text="Open"),
                     "converted_job_url": st.column_config.LinkColumn("New job link", display_text="Open"),
                     "deal_url": st.column_config.LinkColumn("Deal link", display_text="Pipedrive"),
                     "total": money, "status": "Quote status"})
    st.caption("Job → Quote is INFERRED (Jobber does not expose it). Quote → Converted Job is DIRECT from Jobber.")
