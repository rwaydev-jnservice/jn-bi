"""Summarize what is in the database after a sync.

    python scripts/sync_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from database.connection import session_scope  # noqa: E402
from database import models as M  # noqa: E402


def main() -> None:
    with session_scope() as s:
        def n(model, *where):
            return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

        print("== Sync runs (latest per entity)")
        for r in s.scalars(select(M.SyncRun).order_by(M.SyncRun.id)):
            secs = round((r.completed_at - r.started_at).total_seconds()) if r.completed_at else None
            err = (r.errors or "").splitlines()[0][:160] if r.errors else ""
            print(f"  {r.entity:12} {r.status:8} read={r.records_read:6} created={r.records_created:6} updated={r.records_updated:6} {secs}s {err}")

        print("\n== Records")
        tables = [
            ("People (Jobber users)", M.JobberPerson), ("Clients", M.JobberClient), ("Properties", M.JobberProperty),
            ("Requests", M.JobberRequest), ("Assessments", M.JobberAssessment), ("Quotes", M.JobberQuote),
            ("Quote line items", M.JobberQuoteLineItem), ("Jobs", M.JobberJob), ("Visits", M.JobberVisit),
            ("Invoices", M.JobberInvoice),
        ]
        for label, model in tables:
            print(f"  {label:22} {n(model):7,}")

        print("\n== Quotes")
        for status, cnt, total in s.execute(select(M.JobberQuote.status, func.count(), func.coalesce(func.sum(M.JobberQuote.total), 0))
                                            .group_by(M.JobberQuote.status).order_by(func.count().desc())):
            print(f"  {status:20} {cnt:6,}  ${float(total):,.0f}")
        print(f"  sent: {n(M.JobberQuote, M.JobberQuote.is_sent.is_(True)):,}   converted: {n(M.JobberQuote, M.JobberQuote.is_converted.is_(True)):,}")
        for flow, cnt in s.execute(select(M.JobberQuote.flow_type, func.count()).group_by(M.JobberQuote.flow_type)):
            print(f"  flow {flow}: {cnt:,}")

        print("\n== Jobs")
        print(f"  direct service jobs (no request, no quote): {n(M.JobberJob, M.JobberJob.is_direct_job.is_(True)):,}")
        print(f"  created from a quote: {n(M.JobberJob, M.JobberJob.is_quote_convert.is_(True)):,}")
        for src, cnt in s.execute(select(M.JobberJob.source, func.count()).group_by(M.JobberJob.source).order_by(func.count().desc()).limit(8)):
            print(f"  source {src}: {cnt:,}")

        print("\n== Relationships (active)")
        for rel, method, cnt in s.execute(select(M.Relationship.rel_type, M.Relationship.method, func.count())
                                          .where(M.Relationship.is_active.is_(True))
                                          .group_by(M.Relationship.rel_type, M.Relationship.method).order_by(M.Relationship.rel_type)):
            print(f"  {rel:26} {method:16} {cnt:6,}")
        review_quotes = s.scalar(select(func.count(func.distinct(M.Relationship.to_id))).where(
            M.Relationship.method == "REVIEW_REQUIRED", M.Relationship.is_active.is_(True)))
        print(f"  quotes needing review: {review_quotes:,}")

        print("\n== Date coverage")
        for label, col in [("quotes created", M.JobberQuote.created_at), ("jobs created", M.JobberJob.created_at),
                           ("requests created", M.JobberRequest.created_at), ("visits start", M.JobberVisit.start_at)]:
            lo, hi = s.execute(select(func.min(col), func.max(col))).one()
            print(f"  {label:17} {lo:%Y-%m-%d} -> {hi:%Y-%m-%d}" if lo else f"  {label:17} none")


if __name__ == "__main__":
    main()
