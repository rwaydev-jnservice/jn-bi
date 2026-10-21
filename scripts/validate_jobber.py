"""Live READ-ONLY Jobber validation.

    python scripts/validate_jobber.py

1. Confirms authentication (account name).
2. Pulls a small sample (1 page of 5) of every object type.
3. Fetches the known regression chain by EncodedId and checks:
     Job #2718  -> Quote #105733 : INFERRED (never DIRECT)
     Quote #105733 -> Job #2729  : DIRECT
4. Writes validation_output/jobber_validation.md
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database.connection import get_sessionmaker  # noqa: E402
from jobber import normalize as N  # noqa: E402
from jobber import oauth  # noqa: E402
from jobber import queries as Q  # noqa: E402
from jobber.client import JobberClient  # noqa: E402
from services import relationships as R  # noqa: E402

KNOWN = {
    "job_2718": "Z2lkOi8vSm9iYmVyL0pvYi8xNTg2OTIyMzA=",
    "quote_105733": "Z2lkOi8vSm9iYmVyL1F1b3RlLzY2NTk1MDY3",
    "job_2729": "Z2lkOi8vSm9iYmVyL0pvYi8xNTg4MDkwMDE=",
    "client": "Z2lkOi8vSm9iYmVyL0NsaWVudC8xNTQ5MjA3NDM=",
    "property": "Z2lkOi8vSm9iYmVyL1Byb3BlcnR5LzE2MDc2MDYzMg==",
}


def main() -> int:
    Session = get_sessionmaker()

    def provide(force):
        with Session() as s:
            return oauth.get_access_token(s, force_refresh=force)

    c = JobberClient(provide)
    out: list[str] = [f"# Jobber validation — {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    ok = True

    acct = c.execute(Q.ACCOUNT)["account"]
    out += [f"Authenticated to account: **{acct['name']}**", ""]

    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    samples = {
        "users": (Q.USERS, "users", {}),
        "clients": (Q.CLIENTS, "clients", {"filter": None}),
        "requests": (Q.REQUESTS, "requests", {"filter": None}),
        "quotes": (Q.QUOTES, "quotes", {"filter": None}),
        "jobs": (Q.JOBS, "jobs", {"filter": None}),
        "visits": (Q.VISITS, "visits", {"filter": {"startAt": {"after": iso(now), "before": iso(now + timedelta(days=7))}}}),
        "assessments": (Q.ASSESSMENTS, "scheduledItems", {"filter": {"scheduleItemType": "ASSESSMENT", "occursWithin": {"startAt": iso(now - timedelta(days=14)), "endAt": iso(now + timedelta(days=14))}}}),
        "invoices": (Q.INVOICES, "invoices", {"filter": None}),
    }
    out += ["## Sample pull (first page, 5 records)", "", "| Object | Records | Status |", "|---|---|---|"]
    for name, (doc, root, vars_) in samples.items():
        try:
            nodes = list(c.paginate(doc, root, vars_, page_size=5, max_pages=1))
            out.append(f"| {name} | {len(nodes)} | OK |")
        except Exception as exc:  # report, keep going
            ok = False
            out.append(f"| {name} | - | FAILED: {str(exc)[:200]} |")
    out += ["", f"Throttle after sample: {c.throttle}", ""]

    job2718 = c.execute(Q.JOB_BY_ID, {"id": KNOWN["job_2718"]})["job"]
    quote = c.execute(Q.QUOTE_BY_ID, {"id": KNOWN["quote_105733"]})["quote"]
    job2729 = c.execute(Q.JOB_BY_ID, {"id": KNOWN["job_2729"]})["job"]

    checks = [
        ("Job #2718 number", job2718 and job2718["jobNumber"] == 2718),
        ("Job #2718 request is null", job2718 and job2718["request"] is None),
        ("Job #2718 quote is null", job2718 and job2718["quote"] is None),
        ("Job #2718 client", job2718 and job2718["client"]["id"] == KNOWN["client"]),
        ("Job #2718 property", job2718 and job2718["property"]["id"] == KNOWN["property"]),
        ("Quote #105733 number", quote and str(quote["quoteNumber"]) == "105733"),
        ("Quote #105733 status converted", quote and quote["quoteStatus"] == "converted"),
        ("Quote #105733 request is null", quote and quote["request"] is None),
        ("Job #2729 source QUOTE_CONVERT", job2729 and job2729["source"] == "QUOTE_CONVERT"),
        ("Job #2729 -> Quote #105733 (Job.quote)", job2729 and (job2729["quote"] or {}).get("id") == KNOWN["quote_105733"]),
    ]
    out += ["## Known regression objects", "", f"- Job #2718 source: `{job2718 and job2718['source']}`", ""]
    for label, passed in checks:
        ok &= bool(passed)
        out.append(f"- [{'x' if passed else ' '}] {label}")

    # Run the real relationship engine on these live objects
    jobs = [N.job(job2718), N.job(job2729)]
    vparsed = [N.visit(v) for v in (job2718.get("visits") or {}).get("nodes", [])]
    visits = [r for r, _ in vparsed]
    visit_users = {r["visit_id"]: users for r, users in vparsed}
    qrow, _ = N.quote(quote)
    edges, flows = R.build([], [qrow], jobs, visits, [], [], visit_users)
    people = {u["id"]: N.person(u)["name"] for u in c.paginate(Q.USERS, "users")}
    techs = sorted({people.get(u, u) for us in visit_users.values() for u in us})
    sp = people.get(qrow["salesperson_id"], qrow["salesperson_id"])
    out += ["", f"- Technician(s) on Job #2718 visits: **{', '.join(techs) or 'none assigned'}**",
            f"- Salesperson on Quote #105733: **{sp or 'not set'}**",
            f"- Jobber users (active and inactive): {', '.join(sorted(n for n in people.values() if n))}"]
    e1 = [e for e in edges if e.from_id == KNOWN["job_2718"] and e.to_id == KNOWN["quote_105733"]]
    e2 = [e for e in edges if e.from_id == KNOWN["quote_105733"] and e.to_id == KNOWN["job_2729"]]
    r1 = e1[0].method if e1 else "NONE"
    r2 = e2[0].method if e2 else "NONE"
    out += ["", "## Relationship engine on live data", "",
            f"- Job #2718 → Quote #105733: **{r1}** (expected INFERRED){' — signals: ' + json.dumps(e1[0].signals) if e1 else ''}",
            f"- Quote #105733 → Job #2729: **{r2}** (expected DIRECT)",
            f"- Quote #105733 flow type: **{flows.get(KNOWN['quote_105733'])}** (expected SERVICE)"]
    ok &= r1 == "INFERRED" and r2 == "DIRECT"

    out += ["", f"**Overall: {'PASS' if ok else 'FAIL'}**", f"API requests made: {c.requests_made}"]
    dest = ROOT / "validation_output"
    dest.mkdir(exist_ok=True)
    (dest / "jobber_validation.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
