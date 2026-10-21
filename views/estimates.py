import streamlit as st

from database.connection import session_scope
from services import metrics as MX
from services.ui import fmt_money, fmt_pct, period_picker, quote_filters, require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Estimates")
st.caption("Request → Assessment → Quote → Converted Job. Salesperson = Jobber quote salesperson; "
           "Technician = who was assigned to the assessment.")
p = period_picker("est")

with session_scope() as s:
    quotes = MX.quote_facts(s)
    reqs = MX.request_facts(s)

# --- Requests & assessments --------------------------------------------------
r = reqs[MX.in_period(reqs.created_at, p)] if not reqs.empty else reqs
assess_sched = reqs[MX.in_period(reqs.assessment_start, p)] if not reqs.empty else reqs
assess_done = reqs[MX.in_period(reqs.assessment_completed, p)] if not reqs.empty else reqs
with_quote = int(r.has_quote.sum()) if not r.empty else 0
c = st.columns(4)
c[0].metric("Requests", len(r))
c[1].metric("Assessments scheduled", len(assess_sched), f"{len(assess_done)} completed", delta_color="off")
c[2].metric("Requests with a quote", with_quote)
c[3].metric("Request → Quote rate", fmt_pct(with_quote / len(r) if len(r) else None))
direct_q = int((r.quote_link == "DIRECT").sum()) if not r.empty else 0
st.caption(f"'With a quote' = quote attached in Jobber ({direct_q}) or inferred by the BI ({with_quote - direct_q}: same client + "
           "property, quote after the assessment). See Quote Origin Review for each link and its reason.")

# --- Quotes (Estimate flow) ----------------------------------------------------
q = quotes[quotes.Flow == "Estimate"] if not quotes.empty else quotes
q = quote_filters(q, "est", show_flow=False)
m = MX.quote_summary(q, p)
st.subheader("Estimate quotes")
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
    st.caption("No estimate quotes in this period.")
else:
    st.dataframe(sp, hide_index=True, width="stretch", column_config={
        "Presented value": money, "Converted value (of presented)": money, "Avg converted ticket": money, "Unsent value": money,
        "Conversion": pct, "Value conversion": pct})

st.subheader("Requests by technician")
if not r.empty:
    t = MX.explode_technicians(r).groupby("Technician").agg(Requests=("request_id", "count"), **{"With quote": ("has_quote", "sum")}).reset_index()
    st.dataframe(t.sort_values("Requests", ascending=False), hide_index=True, width="stretch")

st.subheader("Unsent quotes (all estimate quotes, any date)")
u = MX.unsent_quotes(q)
if u.empty:
    st.success("No unsent estimate quotes.")
else:
    st.dataframe(u[["Client", "Quote", "created_at", "Age (days)", "Technician", "Salesperson", "Department", "Category", "System",
                    "total", "Deal", "Deal Owner", "Deal Stage", "Open in Jobber"]],
                 hide_index=True, width="stretch", column_config={
                     "total": money, "created_at": st.column_config.DatetimeColumn("Created", format="MMM D, YYYY"),
                     "Open in Jobber": st.column_config.LinkColumn(display_text="Open")})
