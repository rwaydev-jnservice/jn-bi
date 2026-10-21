import pandas as pd
import streamlit as st
from sqlalchemy import select

from config import get_settings
from database.connection import session_scope
from database.models import SyncRun, SyncState
from services.ui import require_role

require_role("ADMIN")
cfg = get_settings()
st.title("Sync Status")
st.caption(f"Background worker runs every {cfg.sync_interval_minutes} min (SYNC_INTERVAL_MINUTES).")

with session_scope() as s:
    runs = s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(100)).all()
    states = s.scalars(select(SyncState)).all()
    tz = cfg.tz
    run_rows = [{
        "Source": r.source, "Entity": r.entity, "Status": r.status,
        "Started": r.started_at.astimezone(tz).strftime("%m/%d %I:%M %p") if r.started_at else "",
        "Seconds": round((r.completed_at - r.started_at).total_seconds()) if r.completed_at else None,
        "Read": r.records_read, "Created": r.records_created, "Updated": r.records_updated,
        "Error": (r.errors or "").splitlines()[0][:200] if r.errors else "",
    } for r in runs]
    state_rows = [{"Source": x.source, "Entity": x.entity,
                   "Synced up to": x.watermark.astimezone(tz).strftime("%m/%d/%Y %I:%M %p") if x.watermark else ""} for x in states]

st.subheader("Watermarks")
st.dataframe(pd.DataFrame(state_rows), hide_index=True, width="stretch")
st.subheader("Recent runs")
st.dataframe(pd.DataFrame(run_rows), hide_index=True, width="stretch")
