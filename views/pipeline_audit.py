from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.connection import session_scope
from database.models import ProcessAudit
from services import segments as SEG
from services.ui import require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Pipeline Audit")
st.caption("Expected Pipedrive stage (from what happened in Jobber) vs the deal's actual stage. "
           "Read only: the **Deal Owner** fixes gaps in Pipedrive; the next sync clears them.")

VIEWS = {
    "Should be NEGOTIATE": ["PIPE_STAGE_NEGOTIATE_REQUIRED"],
    "Should be WON": ["PIPE_WON_REQUIRED"],
    "Should be QUOTE": ["PIPE_STAGE_QUOTE_REQUIRED"],
    "Should be VISIT": ["PIPE_STAGE_VISIT_REQUIRED"],
    "Won/Lost mismatch": ["STATUS_MISMATCH"],
    "Deal not found": ["DEAL_NOT_FOUND"],
    "Needs link confirmation": ["CONTACT_MATCH_AMBIGUOUS", "JOBBER_QUOTE_ID_MISSING"],
    "Deal ahead of Jobber": ["DEAL_STAGE_AHEAD", "STAGE_UNMAPPED"],
}

with session_scope() as s:
    rows = s.scalars(select(ProcessAudit).where(ProcessAudit.resolved_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    data = []
    for r in rows:
        d = r.details or {}
        data.append({
            "gap_type": r.gap_type, "severity": r.severity, "type": r.opportunity_type, "deal_id": r.deal_id,
            "Jobber": d.get("label"), "Client": d.get("client"), "Deal": d.get("deal_title"),
            "Deal Owner": r.owner_name or "—", "Technician": ", ".join(d.get("technicians") or []) or "—",
            "Salesperson": d.get("salesperson") or "—",
            "Actual": f"{r.actual_stage or '—'} / {r.actual_status or '—'}" if r.deal_id else "no deal",
            "Expected": (r.expected_status if r.expected_status == "WON" else (r.expected_stage or "—").title()),
            "Reason": r.reason, "Gap days": (now - r.last_jobber_event_at).days if r.last_jobber_event_at else None,
            "Value": d.get("total"), "Segment": SEG.LABELS.get(r.segment, "—") if r.segment else "—",
            "Last Pipedrive update": r.last_pipedrive_update_at, "Open in Jobber": d.get("jobber_url"), "Open in Pipedrive": d.get("deal_url"),
        })
df = pd.DataFrame(data)
if df.empty:
    st.success("No open gaps. (Or no sync has run yet — check the sidebar.)")
    st.stop()

action = df[df.severity == "ACTION"]
c = st.columns(5)
c[0].metric("Action needed", len(action))
c[1].metric("Should be NEGOTIATE", int((df.gap_type == "PIPE_STAGE_NEGOTIATE_REQUIRED").sum()))
c[2].metric("Should be WON", int((df.gap_type == "PIPE_WON_REQUIRED").sum()))
c[3].metric("Deal not found", int(((df.gap_type == "DEAL_NOT_FOUND") & (df.severity == "ACTION")).sum()))
c[4].metric("Links to confirm", int((df.gap_type == "CONTACT_MATCH_AMBIGUOUS").sum()))

by_owner = action[action.deal_id.notna()].groupby("Deal Owner").size().sort_values(ascending=False)
if not by_owner.empty:
    st.caption("Action needed by Deal Owner: " + " · ".join(f"**{o}** {n}" for o, n in by_owner.items()))

f = st.columns(3)
owners = sorted(df["Deal Owner"].unique())
owner = f[0].multiselect("Deal Owner", owners)
segs = f[1].multiselect("Segment", sorted(df["Segment"].unique()))
kinds = f[2].multiselect("Opportunity type", sorted(df["type"].unique()), help="QUOTE, REQUEST (assessment, no quote yet), SERVICE_JOB")
if owner:
    df = df[df["Deal Owner"].isin(owner)]
if segs:
    df = df[df["Segment"].isin(segs)]
if kinds:
    df = df[df["type"].isin(kinds)]

cols = ["Jobber", "Client", "Deal", "Deal Owner", "Actual", "Expected", "Gap days", "Reason", "Technician", "Salesperson",
        "Value", "Segment", "Last Pipedrive update", "Open in Jobber", "Open in Pipedrive"]
config = {
    "Open in Jobber": st.column_config.LinkColumn(display_text="Jobber"),
    "Open in Pipedrive": st.column_config.LinkColumn(display_text="Pipedrive"),
    "Value": st.column_config.NumberColumn(format="$%.0f"),
    "Last Pipedrive update": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
}


def grouped(frame: pd.DataFrame) -> pd.DataFrame:
    """One line per deal when several quotes point at the same deal."""
    with_deal = frame[frame.deal_id.notna()]
    no_deal = frame[frame.deal_id.isna()]
    if with_deal.empty:
        return frame
    agg = with_deal.groupby(["deal_id", "gap_type"], as_index=False).agg({
        **{k: "first" for k in cols if k not in ("Jobber", "Value", "Gap days")},
        "Jobber": lambda x: ", ".join(sorted(set(x))), "Value": "sum", "Gap days": "max"})
    return pd.concat([agg, no_deal], ignore_index=True)


tabs = st.tabs([f"{name} ({int(df.gap_type.isin(types).sum())})" for name, types in VIEWS.items()])
for tab, (name, types) in zip(tabs, VIEWS.items()):
    with tab:
        view = df[df.gap_type.isin(types)]
        if view.empty:
            st.caption("Nothing here.")
            continue
        view = grouped(view).sort_values("Gap days", ascending=False, na_position="last")
        if name == "Deal not found":
            st.caption("Quotes/requests whose client email/phone matches no Pipedrive deal around that date. "
                       "Service jobs without a deal are listed too but marked informational.")
        if name == "Needs link confirmation":
            st.caption("Several deals fit this client, or a GC deal was linked by contact only. "
                       "Filling **Quote #** on the right deal in Pipedrive makes the link exact.")
        st.dataframe(view[cols], hide_index=True, width="stretch", column_config=config)
