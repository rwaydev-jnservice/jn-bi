"""JN Service BI - Streamlit entry point.

    streamlit run app.py

Login is required for every page. Pages live in views/ (not pages/, so
Streamlit never auto-exposes them) and are registered with st.navigation only
after authentication. Each page also re-checks the role itself.
"""

from __future__ import annotations

import logging

import streamlit as st

from config import get_settings
from database.connection import session_scope
from jobber import oauth
from services.auth import AuthError, authenticate, ensure_bootstrap_admin
from services.freshness import source_freshness

st.set_page_config(page_title="JN Service BI", page_icon=":material/monitoring:", layout="wide")
cfg = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@st.cache_resource
def _bootstrap() -> bool:
    with session_scope() as s:
        ensure_bootstrap_admin(s, cfg.bootstrap_admin_email, cfg.bootstrap_admin_password, cfg.bootstrap_admin_name)
    return True


_bootstrap()


def _handle_jobber_callback() -> None:
    """Jobber redirects back to APP_BASE_URL?code=...&state=... (a fresh session)."""
    params = st.query_params
    if params:
        log.info("Request with query params: %s", sorted(params.keys()))
    if "error" in params:
        log.warning("Jobber returned an error: %s", params.get("error"))
        st.error(f"Jobber returned an error: {params.get('error')} {params.get('error_description', '')}")
    if "code" not in params:
        return
    admin = oauth.verify_state(params.get("state", ""))
    if not admin:
        log.warning("Jobber callback with invalid/expired/missing state")
        st.error("Jobber connection link expired or invalid. Start again from the Connections page.")
    else:
        try:
            with session_scope() as s:
                oauth.exchange_code(s, params["code"], connected_by=admin)
            log.info("Jobber connected by %s", admin)
            st.success(f"Jobber connected (started by {admin}). The next sync will pull data.")
        except Exception as exc:
            log.exception("Jobber code exchange failed")
            st.error(f"Jobber connection failed: {exc}")
    st.query_params.clear()


def _login() -> None:
    st.title("JN Service BI")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        try:
            with session_scope() as s:
                u = authenticate(s, email, password)
                st.session_state.user = {"id": u.id, "email": u.email, "name": u.name, "role": u.role}
            st.rerun()
        except AuthError as exc:
            st.error(str(exc))


_handle_jobber_callback()

user = st.session_state.get("user")
if not user:
    _login()
    st.stop()

with st.sidebar:
    st.caption(f"Signed in as **{user['name']}** ({user['role']})")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    with session_scope() as s:
        for source in ("JOBBER", "PIPEDRIVE"):
            f = source_freshness(s, source)
            when = f.last_success.astimezone(cfg.tz).strftime("%b %d %I:%M %p") if f.last_success else "never"
            st.caption(f"Last {source.title()} sync: {when}")
            if (w := f.warning()) and source == "JOBBER":
                st.warning(w)

dashboards = [
    st.Page("views/executive.py", title="Executive", icon=":material/home:", default=True),
    st.Page("views/estimates.py", title="Estimates", icon=":material/request_quote:"),
    st.Page("views/service.py", title="Service", icon=":material/build:"),
    st.Page("views/quote_origin.py", title="Quote Origin Review", icon=":material/account_tree:"),
    st.Page("views/pipeline_audit.py", title="Pipeline Audit", icon=":material/rule:"),
    st.Page("views/technician_schedule.py", title="Technician Schedule", icon=":material/calendar_month:"),
    st.Page("views/reconciliation.py", title="Reconciliation", icon=":material/balance:"),
    st.Page("views/opportunity_detail.py", title="Opportunity Detail", icon=":material/search:"),
]
sections = {"Dashboards": dashboards}
if user["role"] == "ADMIN":
    sections["Admin"] = [
        st.Page("views/admin_connections.py", title="Connections", icon=":material/link:"),
        st.Page("views/admin_sync.py", title="Sync Status", icon=":material/sync:"),
        st.Page("views/admin_users.py", title="Users", icon=":material/group:"),
    ]
st.navigation(sections).run()
