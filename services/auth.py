"""App login: email + bcrypt password, three roles, simple lockout.

Roles
- ADMIN       : everything, incl. Connections (Jobber/Pipedrive) and Users
- MANAGEMENT  : all dashboards
- VIEW_ONLY   : all dashboards (no admin pages); kept separate for future limits
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import AppUser

ROLES = ("ADMIN", "MANAGEMENT", "VIEW_ONLY")
MAX_FAILED = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LEN = 10


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(session: Session, email: str, name: str, password: str, role: str = "VIEW_ONLY") -> AppUser:
    role = role.upper()
    if role not in ROLES:
        raise AuthError(f"Unknown role {role}. Use one of {ROLES}.")
    email = _norm_email(email)
    if not email or "@" not in email:
        raise AuthError("A valid email is required.")
    if session.scalar(select(AppUser).where(AppUser.email == email)):
        raise AuthError(f"User {email} already exists.")
    user = AppUser(email=email, name=name.strip() or email, password_hash=hash_password(password), role=role)
    session.add(user)
    session.flush()
    return user


def set_password(session: Session, email: str, password: str) -> None:
    user = session.scalar(select(AppUser).where(AppUser.email == _norm_email(email)))
    if not user:
        raise AuthError("User not found.")
    user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None


def authenticate(session: Session, email: str, password: str, now: datetime | None = None) -> AppUser:
    """Returns the user or raises AuthError with a user-safe message."""
    now = now or datetime.now(timezone.utc)
    generic = AuthError("Invalid email or password.")
    user = session.scalar(select(AppUser).where(AppUser.email == _norm_email(email)))
    if not user or not user.is_active:
        raise generic
    if user.locked_until and user.locked_until > now:
        raise AuthError("Too many failed attempts. Try again in a few minutes.")
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        session.flush()
        raise generic
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    session.flush()
    return user


def ensure_bootstrap_admin(session: Session, email: str, password: str, name: str = "Admin") -> AppUser | None:
    """Create the first ADMIN from env vars, only when the users table is empty."""
    if not email or not password:
        return None
    if session.scalar(select(func.count()).select_from(AppUser)):
        return None
    return create_user(session, email, name, password, "ADMIN")
