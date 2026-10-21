import pandas as pd
import streamlit as st
from sqlalchemy import select

from config import get_settings
from database.connection import session_scope
from database.models import JobberClient, JobberQuote, PipedriveDeal, Reconciliation
from services import segments as SEG
from services.ui import require_role

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
cfg = get_settings()
st.title("Reconciliation")
st.caption("Jobber quote vs its linked Pipedrive deal (firm links only: Quote # field, confirmed, or a single contact match). "
           "A deal with several quotes is OK if its value equals any of them.")

STATUS_LABEL = {
    "OK": "OK", "JOBBER_CONVERTED_PIPE_NOT_WON": "Converted in Jobber, deal not Won",
    "PIPE_WON_JOBBER_NOT_CONVERTED": "Deal Won, quote not converted", "STATUS_MISMATCH": "Won/Lost mismatch",
}

with session_scope() as s:
    q = (select(Reconciliation, JobberQuote.quote_number, JobberQuote.title, JobberQuote.web_uri, JobberQuote.segment,
                PipedriveDeal.title.label("deal_title"), PipedriveDeal.owner_name, JobberClient.name.label("client"))
         .join(JobberQuote, JobberQuote.quote_id == Reconciliation.quote_id)
         .join(PipedriveDeal, PipedriveDeal.deal_id == Reconciliation.deal_id)
         .outerjoin(JobberClient, JobberClient.client_id == JobberQuote.client_id))
    data = [{
        "Quote": f"#{r.quote_number}", "Client": r.client, "Deal": r.deal_title, "Deal Owner": r.owner_name,
        "Quote value": float(r.Reconciliation.quote_value or 0), "Deal value": float(r.Reconciliation.deal_value or 0),
        "Difference": float(r.Reconciliation.difference or 0), "Value": r.Reconciliation.value_status,
        "Jobber status": r.Reconciliation.quote_status, "Pipedrive": f"{r.Reconciliation.deal_stage} / {r.Reconciliation.deal_status}",
        "Status check": STATUS_LABEL.get(r.Reconciliation.status_check, r.Reconciliation.status_check),
        "Quotes on deal": r.Reconciliation.quotes_on_deal, "Link": r.Reconciliation.match_method,
        "Segment": SEG.LABELS.get(r.segment, "—"), "Open in Jobber": r.web_uri,
        "Open in Pipedrive": f"https://{cfg.pipedrive_company_domain}.pipedrive.com/deal/{r.Reconciliation.deal_id}",
    } for r in s.execute(q)]
df = pd.DataFrame(data)
if df.empty:
    st.info("Nothing to reconcile yet.")
    st.stop()

c = st.columns(4)
c[0].metric("Linked quotes", len(df))
c[1].metric("Value mismatch", int((df.Value == "VALUE_MISMATCH").sum()))
c[2].metric("Status mismatch", int((df["Status check"] != "OK").sum()))
c[3].metric("Converted, deal not Won", int((df["Status check"] == STATUS_LABEL["JOBBER_CONVERTED_PIPE_NOT_WON"]).sum()))

f = st.columns(3)
show = f[0].radio("Show", ["Problems only", "Everything"], horizontal=True)
owners = f[1].multiselect("Deal Owner", sorted(df["Deal Owner"].dropna().unique()))
segs = f[2].multiselect("Segment", sorted(df["Segment"].unique()))
if show == "Problems only":
    df = df[(df.Value != "OK") | (df["Status check"] != "OK")]
if owners:
    df = df[df["Deal Owner"].isin(owners)]
if segs:
    df = df[df.Segment.isin(segs)]
df = df.sort_values(["Status check", "Difference"], key=lambda col: col.abs() if col.name == "Difference" else (col == "OK"), ascending=[True, False])

money = st.column_config.NumberColumn(format="$%.0f")
st.dataframe(df, hide_index=True, width="stretch", column_config={
    "Quote value": money, "Deal value": money, "Difference": money,
    "Open in Jobber": st.column_config.LinkColumn(display_text="Jobber"),
    "Open in Pipedrive": st.column_config.LinkColumn(display_text="Pipedrive"),
})
st.caption("Difference = deal value − quote value. 'Quotes on deal' > 1 usually means options or re-sent quotes on the same deal.")
