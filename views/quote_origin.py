import pandas as pd
import streamlit as st

from config import get_settings
from database.connection import session_scope
from services import quote_origin_report as R
from services.ui import require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
cfg = get_settings()
st.title("Quote Origin Review")
st.caption("Where each quote came from: an Estimate (Request/Assessment) or a Service job visit. "
           "DIRECT = Jobber links them. INFERRED = the BI deduced it (same client + property + timing). "
           "Nothing here is written back to Jobber.")
if cfg.use_quote_origin_engine:
    st.success("Dashboards use this classification (USE_QUOTE_ORIGIN_ENGINE is ON).")
else:
    st.warning("Validation mode: dashboards still use the previous classification until this is approved "
               "(USE_QUOTE_ORIGIN_ENGINE is OFF).")

with session_scope() as s:
    df = R.origin_table(s)
if df.empty or df["Flow Type"].isna().all():
    st.info("No origin classification yet. It runs with every sync.")
    st.stop()

money = st.column_config.NumberColumn(format="$%.0f")
c = st.columns(2)
with c[0]:
    st.subheader("Summary")
    st.dataframe(R.summary(df), hide_index=True, width="stretch", column_config={"Value": money})
with c[1]:
    st.subheader("Quotes without a Jobber request")
    st.dataframe(R.unlinked_resolution(df), hide_index=True, width="stretch", column_config={"Value": money})
st.caption(f"Quotes created since {cfg.jobber_history_start}. Review-required and Other/unlinked quotes are kept out of "
           "Estimate and Service conversion metrics.")

st.subheader("Quote detail")
views = {"Inferred + review": ["INFERRED", "REVIEW_REQUIRED"], "Review required": ["REVIEW_REQUIRED"], "Inferred": ["INFERRED"],
         "Direct": ["DIRECT"], "Unlinked": ["UNLINKED"], "All": ["DIRECT", "INFERRED", "REVIEW_REQUIRED", "UNLINKED"]}
pick = st.radio("Show", list(views), horizontal=True)
flows = st.multiselect("Flow type", sorted(df["Flow Type"].dropna().unique()))
view = df[df["Origin Method"].isin(views[pick])]
if flows:
    view = view[view["Flow Type"].isin(flows)]
cols = ["Quote Number", "Client", "Property", "Created At", "Flow Type", "Origin Method", "Confidence", "Origin Request",
        "Origin Assessment", "Origin Service Job", "Origin Visit", "Converted Job(s)", "Candidate Service Jobs", "Candidate Requests",
        "Reason", "Total", "Open quote", "Open origin job", "Open origin request"]
st.dataframe(view[cols], hide_index=True, width="stretch", column_config={
    "Total": money, "Confidence": st.column_config.NumberColumn(format="%.2f"),
    "Open quote": st.column_config.LinkColumn(display_text="Quote"),
    "Open origin job": st.column_config.LinkColumn(display_text="Job"),
    "Open origin request": st.column_config.LinkColumn(display_text="Request"),
    "Reason": st.column_config.TextColumn(width="large")})
st.caption("Converted Job(s) are DIRECT descendants from Jobber and are never the origin. "
           "Origin Service Job → Quote is always INFERRED (Jobber does not expose it).")

with st.expander("Previous classification → new classification"):
    st.dataframe(pd.crosstab(df["Legacy Flow"].fillna("—"), df["Flow Type"].fillna("—")), width="stretch")
