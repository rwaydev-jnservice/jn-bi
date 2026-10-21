import streamlit as st

from services.ui import require_role, coming_soon

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Opportunity Detail")
coming_soon("Phase 9", "Full chain for one opportunity: Jobber records, Pipedrive deal, process gaps, relationship methods, timeline.")
