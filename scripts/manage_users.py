"""Manage app users from a terminal (passwords are typed, never passed as args).

    python scripts/manage_users.py add  owner@example.com "Owner Name" MANAGEMENT
    python scripts/manage_users.py list
    python scripts/manage_users.py password owner@example.com
    python scripts/manage_users.py disable owner@example.com
    python scripts/manage_users.py enable owner@example.com
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from database.connection import session_scope  # noqa: E402
from database.models import AppUser  # noqa: E402
from services.auth import ROLES, AuthError, create_user, set_password  # noqa: E402


def _ask_password() -> str:
    p1 = getpass.getpass("New password: ")
    p2 = getpass.getpass("Repeat password: ")
    if p1 != p2:
        raise AuthError("Passwords do not match.")
    return p1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    try:
        with session_scope() as s:
            if cmd == "add":
                email, name = args[0], args[1]
                role = args[2] if len(args) > 2 else "VIEW_ONLY"
                create_user(s, email, name, _ask_password(), role)
                print(f"Created {email} ({role.upper()})")
            elif cmd == "list":
                for u in s.scalars(select(AppUser).order_by(AppUser.email)):
                    print(f"{u.email:40} {u.role:11} {'active' if u.is_active else 'DISABLED':8} last login: {u.last_login_at}")
            elif cmd == "password":
                set_password(s, args[0], _ask_password())
                print("Password updated.")
            elif cmd in ("disable", "enable"):
                u = s.scalar(select(AppUser).where(AppUser.email == args[0].strip().lower()))
                if not u:
                    raise AuthError("User not found.")
                u.is_active = cmd == "enable"
                print(f"{u.email} {'enabled' if u.is_active else 'disabled'}.")
            else:
                print(f"Unknown command. Roles: {ROLES}")
                return 1
    except (AuthError, IndexError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
