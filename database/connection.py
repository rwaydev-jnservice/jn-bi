"""Database engine/session helpers."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().sqlalchemy_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set. See README > Local setup.")
    return create_engine(url, pool_pre_ping=True, future=True)


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on error."""
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
