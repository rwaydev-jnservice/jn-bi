"""Jobber OAuth 2.0 (authorization code + rotating refresh tokens).

Tokens are stored encrypted in the `oauth_tokens` table. Jobber may rotate the
refresh token on every refresh, so the newest one is always saved back.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import OAuthToken
from services.crypto import decrypt, encrypt

PROVIDER = "jobber"
STATE_TTL_SECONDS = 15 * 60


# --- signed state (survives the redirect even though Streamlit starts a new session)
def make_state(admin_email: str, secret: str | None = None) -> str:
    secret = secret or get_settings().app_secret_key
    payload = json.dumps({"e": admin_email, "t": int(time.time()), "n": secrets.token_urlsafe(8)}).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_state(state: str, secret: str | None = None, now: float | None = None) -> str | None:
    """Returns the admin email that started the flow, or None if invalid/expired."""
    secret = secret or get_settings().app_secret_key
    try:
        body, sig = state.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if (now or time.time()) - payload["t"] > STATE_TTL_SECONDS:
            return None
        return payload["e"]
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def authorize_url(state: str) -> str:
    s = get_settings()
    params = {"response_type": "code", "client_id": s.jobber_client_id, "redirect_uri": s.app_base_url, "state": state}
    return f"{s.jobber_oauth_base}/authorize?{urlencode(params)}"


def _jwt_exp(token: str) -> datetime | None:
    """Read `exp` from a JWT access token without verifying it (only for scheduling refresh)."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except Exception:
        return None


def _token_request(data: dict, http: httpx.Client | None = None) -> dict:
    s = get_settings()
    body = {"client_id": s.jobber_client_id, "client_secret": s.jobber_client_secret, **data}
    client = http or httpx.Client(timeout=30)
    r = client.post(f"{s.jobber_oauth_base}/token", data=body)
    if r.status_code >= 400:
        raise RuntimeError(f"Jobber token endpoint HTTP {r.status_code}: {r.text[:300]}")
    payload = r.json()
    if "access_token" not in payload:
        raise RuntimeError(f"Unexpected Jobber token response keys: {list(payload)}")
    return payload


def _save(session: Session, payload: dict, connected_by: str | None = None) -> OAuthToken:
    row = session.get(OAuthToken, PROVIDER) or OAuthToken(provider=PROVIDER)
    access = payload["access_token"]
    row.access_token_enc = encrypt(access)
    if payload.get("refresh_token"):
        row.refresh_token_enc = encrypt(payload["refresh_token"])
    exp = _jwt_exp(access)
    if exp is None and payload.get("expires_in"):
        exp = datetime.now(timezone.utc) + timedelta(seconds=int(payload["expires_in"]))
    row.access_expires_at = exp
    if connected_by:
        row.connected_by = connected_by
    session.add(row)
    session.flush()
    return row


def exchange_code(session: Session, code: str, connected_by: str, http: httpx.Client | None = None) -> None:
    payload = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": get_settings().app_base_url}, http)
    _save(session, payload, connected_by)


def is_connected(session: Session) -> bool:
    row = session.get(OAuthToken, PROVIDER)
    return bool(row and row.refresh_token_enc) or bool(get_settings().jobber_refresh_token)


def get_access_token(session: Session, force_refresh: bool = False, http: httpx.Client | None = None) -> str:
    """Returns a valid access token, refreshing (and saving the rotated refresh
    token) when needed. Locks the token row so two processes can't race."""
    row = session.execute(select(OAuthToken).where(OAuthToken.provider == PROVIDER).with_for_update()).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row and row.access_token_enc and not force_refresh and row.access_expires_at and row.access_expires_at > now + timedelta(minutes=2):
        return decrypt(row.access_token_enc)

    refresh = decrypt(row.refresh_token_enc) if row and row.refresh_token_enc else get_settings().jobber_refresh_token
    if not refresh:
        raise RuntimeError("Jobber is not connected. An admin must connect it on the Connections page.")
    payload = _token_request({"grant_type": "refresh_token", "refresh_token": refresh}, http)
    _save(session, payload)
    session.commit()  # persist rotated refresh token immediately
    return payload["access_token"]
