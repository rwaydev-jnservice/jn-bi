"""Background sync worker.

    python worker.py           # loop forever, every SYNC_INTERVAL_MINUTES
    python worker.py --once    # single run (for cron-style schedulers)

Runs Jobber sync, then rebuilds relationships. Pipedrive sync is added in
Phase 4. Uses a Postgres advisory lock so two workers never sync at once.
"""

from __future__ import annotations

import logging
import sys
import time

from sqlalchemy import text

from config import get_settings
from database.connection import get_sessionmaker, session_scope
from jobber import oauth
from jobber.client import JobberClient
from pipedrive.client import PipedriveClient
from services import classification, deal_matching, process_audit, quote_origin, reconciliation, relationships
from services.sync_jobber import JobberSync
from services.sync_pipedrive import PipedriveSync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")
LOCK_ID = 7_301_2026


def _token_provider():
    Session = get_sessionmaker()

    def provide(force: bool) -> str:
        with Session() as s:
            return oauth.get_access_token(s, force_refresh=force)

    return provide


def run_once() -> dict:
    cfg = get_settings()
    result: dict = {}
    with session_scope() as s:
        got = s.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_ID}).scalar()
        if not got:
            log.warning("Another sync is running; skipping this cycle.")
            return {"skipped": True}
        try:
            if cfg.jobber_configured and oauth.is_connected(s):
                result["jobber"] = JobberSync(s, JobberClient(_token_provider())).run()
                result["relationships"] = relationships.rebuild(s)
                result["quote_origin"] = quote_origin.rebuild(s)
                s.commit()
            else:
                log.warning("Jobber not configured/connected; skipping Jobber sync.")
            if cfg.pipedrive_configured:
                result["pipedrive"] = PipedriveSync(s, PipedriveClient()).run()
            else:
                log.warning("Pipedrive not configured; skipping Pipedrive sync.")
            result["classification"] = classification.rebuild(s)
            result["deal_matching"] = deal_matching.rebuild(s)
            result["process_audit"] = process_audit.rebuild(s)
            result["reconciliation"] = reconciliation.rebuild(s)
            s.commit()
        finally:
            s.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
    log.info("Sync result: %s", result)
    return result


def main() -> None:
    if "--once" in sys.argv:
        run_once()
        return
    interval = max(1, get_settings().sync_interval_minutes) * 60
    while True:
        started = time.time()
        try:
            run_once()
        except Exception:
            log.exception("Sync cycle failed")
        time.sleep(max(30, interval - (time.time() - started)))


if __name__ == "__main__":
    main()
