"""Value and status reconciliation: Jobber quote vs its linked Pipedrive deal.

Only firm links are reconciled (Quote # field, confirmed, or single contact
match). A deal can carry several quotes (options, re-sends); its value is OK
if it equals ANY of them, and a WON deal is OK if ANY of its quotes converted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from config import get_settings
from database import models as M

FIRM = ("JOBBER_QUOTE_ID", "CONFIRMED", "CONTACT_MATCH")
CONVERTED = {"converted"}
APPROVED_OR_CONVERTED = {"approved", "converted"}


def values_match(a, b) -> bool:
    if a is None or b is None:
        return False
    a, b = Decimal(a), Decimal(b)
    return abs(a - b) <= max(Decimal("1.00"), abs(a) * Decimal("0.01"))


def status_check(quote_status: str | None, deal_status: str | None, other_quote_statuses: list[str]) -> str:
    any_converted = quote_status in CONVERTED or any(s in APPROVED_OR_CONVERTED for s in other_quote_statuses)
    if quote_status in CONVERTED and deal_status == "open":
        return "JOBBER_CONVERTED_PIPE_NOT_WON"
    if quote_status in CONVERTED and deal_status == "lost":
        return "STATUS_MISMATCH"
    if deal_status == "won" and not any_converted and quote_status not in APPROVED_OR_CONVERTED:
        return "PIPE_WON_JOBBER_NOT_CONVERTED"
    if deal_status == "lost" and quote_status in ("awaiting_response", "changes_requested", "approved"):
        return "STATUS_MISMATCH"
    return "OK"


def rebuild(session: Session) -> dict:
    start = datetime.fromisoformat(get_settings().jobber_history_start).replace(tzinfo=timezone.utc)
    quotes = {q.quote_id: q for q in session.scalars(select(M.JobberQuote).where(M.JobberQuote.created_at >= start))}
    deals = {d.deal_id: d for d in session.scalars(select(M.PipedriveDeal))}
    stage_names = dict(session.execute(select(M.PipedriveStage.stage_id, M.PipedriveStage.name)).all())
    links = [m for m in session.scalars(select(M.DealMatch).where(M.DealMatch.is_active.is_(True), M.DealMatch.match_method.in_(FIRM)))
             if m.quote_id in quotes and m.deal_id in deals]
    by_deal: dict[int, list] = {}
    for m in links:
        by_deal.setdefault(m.deal_id, []).append(quotes[m.quote_id])

    session.execute(delete(M.Reconciliation))
    counts: dict[str, int] = {}
    for m in links:
        q, d = quotes[m.quote_id], deals[m.deal_id]
        siblings = [x for x in by_deal[m.deal_id] if x.quote_id != q.quote_id]
        value_ok = any(values_match(x.total, d.value) for x in [q, *siblings])
        st = status_check(q.status, d.status, [x.status for x in siblings])
        session.add(M.Reconciliation(
            quote_id=q.quote_id, deal_id=d.deal_id, quote_value=q.total, deal_value=d.value,
            difference=(Decimal(d.value or 0) - Decimal(q.total or 0)), value_status="OK" if value_ok else "VALUE_MISMATCH",
            status_check=st, match_method=m.match_method, quote_status=q.status, deal_status=d.status,
            deal_stage=(stage_names.get(d.stage_id) or "").strip(), quotes_on_deal=len(by_deal[m.deal_id]),
        ))
        counts["value_" + ("ok" if value_ok else "mismatch")] = counts.get("value_" + ("ok" if value_ok else "mismatch"), 0) + 1
        counts[st] = counts.get(st, 0) + 1
    session.flush()
    return counts
