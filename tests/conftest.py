import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-that-is-long-enough-1234567890")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
# Tests must not depend on the developer's local .env (env vars win over .env).
os.environ["JOBBER_HISTORY_START"] = "2025-01-01"
os.environ["SYNC_OVERLAP_MINUTES"] = "10"
os.environ["INFERENCE_WINDOW_DAYS"] = "14"
os.environ["ESTIMATE_INFERENCE_WINDOW_DAYS"] = "30"
os.environ["USE_QUOTE_ORIGIN_ENGINE"] = "true"  # production setting since 2026-10-21
os.environ["REPORT_TIMEZONE"] = "America/New_York"
os.environ["WEEK_STARTS_ON"] = "SUNDAY"


@pytest.fixture(scope="session")
def db_url():
    """Real PostgreSQL (embedded via pgserver), migrated with Alembic."""
    pgserver = pytest.importorskip("pgserver")
    from alembic import command
    from alembic.config import Config

    srv = pgserver.get_server(str(ROOT / ".localdb"), cleanup_mode=None)
    srv.psql("DROP DATABASE IF EXISTS jnbi_test WITH (FORCE);")
    srv.psql("CREATE DATABASE jnbi_test;")
    url = srv.get_uri("jnbi_test").replace("postgresql://", "postgresql+psycopg://", 1)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "database" / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def session(db_url):
    from sqlalchemy import text

    from database.connection import get_sessionmaker
    from database.models import Base

    Session = get_sessionmaker(db_url)
    s = Session()
    yield s
    s.rollback()
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    s.commit()
    s.close()
