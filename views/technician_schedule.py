import streamlit as st

from services.ui import require_role, coming_soon

require_role("ADMIN", "MANAGEMENT", "VIEW_ONLY")
st.title("Technician Schedule")
coming_soon("Phase 8", "Upcoming assessments and visits per technician (picked from Jobber users), with quote and deal context.")
