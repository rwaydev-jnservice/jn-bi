"""Jobber GraphQL schema tools.

    python -m jobber.schema refresh            # download introspection snapshot
    python -m jobber.schema describe Quote Job # list fields of types
    python -m jobber.schema validate           # check all app queries vs snapshot

Introspection on api.getjobber.com currently works without a token; a token
is sent if one is available.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

import httpx
from graphql import GraphQLSchema, build_client_schema, get_introspection_query, parse, validate

from config import get_settings

SNAPSHOT_DIR = Path(__file__).resolve().parent / "schema_snapshot"


def snapshot_path(version: str | None = None) -> Path:
    return SNAPSHOT_DIR / f"schema_{version or get_settings().jobber_api_version}.json"


def download_introspection(version: str | None = None, access_token: str | None = None) -> dict:
    s = get_settings()
    headers = {"X-JOBBER-GRAPHQL-VERSION": version or s.jobber_api_version}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    r = httpx.post(s.jobber_graphql_url, json={"query": get_introspection_query(descriptions=True)}, headers=headers, timeout=90)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Introspection failed: {data['errors']}")
    return data


@lru_cache
def load_schema(version: str | None = None) -> GraphQLSchema:
    data = json.loads(snapshot_path(version).read_text(encoding="utf-8"))
    return build_client_schema(data["data"])


def validate_document(document: str, version: str | None = None) -> list[str]:
    return [e.message for e in validate(load_schema(version), parse(document))]


def describe(type_name: str, version: str | None = None) -> str:
    t = load_schema(version).get_type(type_name)
    if t is None:
        return f"{type_name}: NOT IN SCHEMA"
    lines = [f"== {type_name}"]
    fields = getattr(t, "fields", None) or {}
    for name, f in fields.items():
        dep = "  (deprecated)" if getattr(f, "deprecation_reason", None) else ""
        lines.append(f"  {name}: {f.type}{dep}")
    for v in getattr(t, "values", {}) or {}:
        lines.append(f"  [enum] {v}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "validate"
    if cmd == "refresh":
        data = download_introspection()
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        snapshot_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"Saved {snapshot_path()} ({len(data['data']['__schema']['types'])} types)")
    elif cmd == "describe":
        for name in argv[1:]:
            print(describe(name))
    elif cmd == "validate":
        from jobber.queries import ALL_DOCUMENTS

        bad = 0
        for name, doc in ALL_DOCUMENTS.items():
            errors = validate_document(doc)
            print(f"{'OK  ' if not errors else 'FAIL'} {name}")
            for e in errors:
                print(f"     - {e}")
            bad += bool(errors)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
