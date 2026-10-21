"""Start an embedded PostgreSQL for local development (no install needed).

    python scripts/local_db.py

Prints a DATABASE_URL to put in .env. Data lives in ./.localdb (git-ignored).
Production uses a managed Postgres (Render), not this.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def start(dbname: str = "jnbi"):
    import pgserver  # dev dependency

    server = pgserver.get_server(str(ROOT / ".localdb"), cleanup_mode=None)
    exists = server.psql(f"SELECT 1 FROM pg_database WHERE datname = '{dbname}';")
    if "1 row" not in exists:
        server.psql(f"CREATE DATABASE {dbname};")
    return server, server.get_uri(dbname)


if __name__ == "__main__":
    srv, uri = start()
    print(f"DATABASE_URL={uri}")
    if "--keep" in sys.argv:
        print("Postgres running. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
