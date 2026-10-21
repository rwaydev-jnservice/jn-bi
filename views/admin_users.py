import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.connection import session_scope
from database.models import AppUser
from services.auth import ROLES, AuthError, create_user, set_password
from services.ui import require_role

me = require_role("ADMIN")
st.title("Users")

with session_scope() as s:
    users = s.scalars(select(AppUser).order_by(AppUser.email)).all()
    rows = [{"Email": u.email, "Name": u.name, "Role": u.role, "Active": u.is_active,
             "Last login": u.last_login_at.strftime("%m/%d/%Y %H:%M UTC") if u.last_login_at else ""} for u in users]
    emails = [u.email for u in users]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

st.subheader("Add user")
with st.form("add_user", clear_on_submit=True):
    c = st.columns(3)
    email = c[0].text_input("Email")
    name = c[1].text_input("Name")
    role = c[2].selectbox("Role", ROLES, index=1)
    pw = st.text_input("Temporary password (10+ characters)", type="password")
    if st.form_submit_button("Create"):
        try:
            with session_scope() as s:
                create_user(s, email, name, pw, role)
            st.success(f"Created {email}. Share the password with them privately.")
            st.rerun()
        except AuthError as exc:
            st.error(str(exc))

st.subheader("Change role / access / password")
if emails:
    with st.form("edit_user"):
        target = st.selectbox("User", emails)
        c = st.columns(3)
        new_role = c[0].selectbox("Role", ("(keep)",) + ROLES)
        active = c[1].selectbox("Access", ("(keep)", "Active", "Disabled"))
        new_pw = c[2].text_input("New password (optional)", type="password")
        if st.form_submit_button("Save"):
            try:
                with session_scope() as s:
                    u = s.scalar(select(AppUser).where(AppUser.email == target))
                    if u.email == me["email"] and (active == "Disabled" or (new_role not in ("(keep)", "ADMIN"))):
                        raise AuthError("You can't remove your own admin access.")
                    if new_role != "(keep)":
                        u.role = new_role
                    if active != "(keep)":
                        u.is_active = active == "Active"
                    if new_pw:
                        set_password(s, target, new_pw)
                st.success("Saved.")
                st.rerun()
            except AuthError as exc:
                st.error(str(exc))
