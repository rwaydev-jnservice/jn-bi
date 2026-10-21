import streamlit as st

from config import get_settings
from database.connection import session_scope
from database.models import OAuthToken
from jobber import oauth
from services.ui import require_role

user = require_role("ADMIN")
cfg = get_settings()
st.title("Connections")
st.caption("Both integrations are READ ONLY. This app never changes anything in Jobber or Pipedrive.")

st.subheader("Jobber")
if not cfg.jobber_configured:
    st.error("JOBBER_CLIENT_ID / JOBBER_CLIENT_SECRET are not set on the server.")
else:
    with session_scope() as s:
        row = s.get(OAuthToken, oauth.PROVIDER)
        connected = oauth.is_connected(s)
        info = (row.connected_by, row.updated_at) if row else (None, None)
    if connected:
        st.success(f"Connected{f' by {info[0]}' if info[0] else ''}{f' — token updated {info[1]:%b %d %H:%M} UTC' if info[1] else ''}.")
    else:
        st.warning("Not connected yet.")
    st.write("Connecting opens Jobber; a Jobber **admin** approves read access and is sent back here.")
    st.caption(f"Callback URL that must be registered in the Jobber app: `{cfg.app_base_url}`")
    st.link_button("Connect / reconnect Jobber", oauth.authorize_url(oauth.make_state(user["email"])))

st.subheader("Pipedrive")
if cfg.pipedrive_configured:
    st.success(f"API token configured for `{cfg.pipedrive_company_domain}.pipedrive.com` (sales pipeline id {cfg.pipedrive_sales_pipeline_id}).")
else:
    st.warning("PIPEDRIVE_API_TOKEN / PIPEDRIVE_COMPANY_DOMAIN are not set on the server.")
