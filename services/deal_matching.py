"""Jobber Quote -> Pipedrive Deal matching.

Priority
1. JOBBER_QUOTE_ID : deal's "Jobber Quote ID" field equals the quote id/number
                     (field does not exist in Pipedrive yet, kept for later)
2. CONFIRMED       : a person previously confirmed the link (never overwritten)
3. CONTACT_MATCH   : the Jobber client's normalized email/phone matches the
                     deal's person, AND the deal is alive around the quote date.
                     If several deals qualify, a deal whose value equals the
                     quote total (within 1%) breaks the tie; otherwise all
                     candidates are stored as REVIEW_REQUIRED.

Email/phone alone never count unless the deal's timing is compatible with
the quote. Names and addresses are not used to create matches.

Segment: from the matched deal's Category. If the match needs review but
every candidate deal has the same segment, that segment is still known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database.models import DealMatch, JobberClient, JobberQuote, PipedriveDeal, utcnow
from services import segments as SEG

DEAL_OPENED_AFTER_QUOTE_MAX = timedelta(days=45)  # deal may be created a bit after the quote
DEAL_CLOSED_BEFORE_QUOTE_MAX = timedelta(days=30)  # closed deal still relevant shortly after
VALUE_TOLERANCE = Decimal("0.01")


@dataclass
class Match:
    quote_id: str
    deal_id: int
    method: str
    confidence: float
    reason: str
    signals: dict = field(default_factory=dict)


def _timing_ok(q: dict, d: dict) -> bool:
    qc = q.get("created_at")
    if qc is None or d.get("add_time") is None:
        return False
    if d["add_time"] > qc + DEAL_OPENED_AFTER_QUOTE_MAX:
        return False
    if d.get("status") == "open":
        return True
    closed = d.get("won_time") or d.get("lost_time") or d.get("update_time")
    return closed is not None and closed >= qc - DEAL_CLOSED_BEFORE_QUOTE_MAX


def _value_equal(q: dict, d: dict) -> bool:
    qv, dv = q.get("total"), d.get("value")
    if not qv or not dv:
        return False
    return abs(Decimal(qv) - Decimal(dv)) <= Decimal(qv) * VALUE_TOLERANCE


def _quote_key(v) -> str | None:
    """'105733', '#105733', 'Q-105733 ' -> '105733'. Encoded ids are kept as-is."""
    if v is None:
        return None
    s = str(v).strip()
    digits = re.sub(r"\D", "", s)
    if digits and len(digits) >= 3 and not s.startswith("Z2lk"):
        return digits
    return s or None


def match_quotes(quotes: list[dict], clients: dict[str, dict], deals: list[dict], confirmed: dict[str, int] | None = None) -> list[Match]:
    confirmed = confirmed or {}
    by_jq: dict[str, list[dict]] = {}
    by_email: dict[str, set[int]] = {}
    by_phone: dict[str, set[int]] = {}
    deal_by_id = {d["deal_id"]: d for d in deals}
    for d in deals:
        # one field may list several quotes: "105620, 105621"
        for part in re.split(r"[,;/\s]+(?=\S)", str(d.get("jobber_quote_id") or "")):
            k = _quote_key(part)
            if k:
                by_jq.setdefault(k, []).append(d)
        for e in d.get("person_emails") or []:
            by_email.setdefault(e, set()).add(d["deal_id"])
        for p in d.get("person_phones") or []:
            by_phone.setdefault(p, set()).add(d["deal_id"])

    out: list[Match] = []
    for q in quotes:
        qid = q["quote_id"]
        if qid in confirmed:
            out.append(Match(qid, confirmed[qid], "CONFIRMED", 1.0, "Confirmed by a person"))
            continue
        explicit = by_jq.get(qid) or by_jq.get(_quote_key(q.get("quote_number")) or "")
        if explicit:
            for d in explicit:
                out.append(Match(qid, d["deal_id"], "JOBBER_QUOTE_ID", 1.0, "Deal's Jobber Quote ID field"))
            continue
        c = clients.get(q.get("client_id")) or {}
        emails, phones = set(c.get("emails") or []), set(c.get("phones") or [])
        hits: dict[int, set[str]] = {}
        for e in emails:
            for did in by_email.get(e, ()):
                hits.setdefault(did, set()).add("email")
        for p in phones:
            for did in by_phone.get(p, ()):
                hits.setdefault(did, set()).add("phone")
        cands = [deal_by_id[did] for did in hits if _timing_ok(q, deal_by_id[did])]
        if not cands:
            continue
        sig = lambda d: {"matched_on": sorted(hits[d["deal_id"]]), "deal_status": d.get("status"),  # noqa: E731
                         "deal_value": str(d.get("value")), "quote_total": str(q.get("total")),
                         "value_equal": _value_equal(q, d), "candidate_count": len(cands)}
        if len(cands) == 1:
            d = cands[0]
            conf = 0.75 + (0.1 if len(hits[d["deal_id"]]) == 2 else 0) + (0.1 if _value_equal(q, d) else 0)
            out.append(Match(qid, d["deal_id"], "CONTACT_MATCH", round(conf, 2), "Only deal for this client's email/phone around the quote date", sig(d)))
            continue
        equal = [d for d in cands if _value_equal(q, d)]
        if len(equal) == 1:
            d = equal[0]
            out.append(Match(qid, d["deal_id"], "CONTACT_MATCH", 0.85, f"{len(cands)} deals for this contact; only one has the same value as the quote", sig(d)))
            continue
        for d in cands:
            out.append(Match(qid, d["deal_id"], "REVIEW_REQUIRED", round(0.5 / len(cands), 2),
                             f"{len(cands)} deals for this contact around the quote date; needs a person to pick", sig(d)))
    return out


def quote_segments(quotes: list[dict], matches: list[Match], deals: dict[int, dict]) -> dict[str, str]:
    by_quote: dict[str, list[Match]] = {}
    for m in matches:
        by_quote.setdefault(m.quote_id, []).append(m)
    out = {}
    for q in quotes:
        ms = by_quote.get(q["quote_id"])
        if not ms:
            out[q["quote_id"]] = SEG.NO_DEAL_MATCH
            continue
        segs = {deals[m.deal_id].get("segment") or SEG.UNCATEGORIZED for m in ms if m.deal_id in deals}
        out[q["quote_id"]] = segs.pop() if len(segs) == 1 else SEG.NO_DEAL_MATCH
    return out


def rebuild(session: Session) -> dict:
    quotes = [dict(r._mapping) for r in session.execute(select(JobberQuote.quote_id, JobberQuote.quote_number, JobberQuote.client_id,
                                                               JobberQuote.created_at, JobberQuote.total))]
    clients = {r.client_id: {"emails": r.emails, "phones": r.phones} for r in session.execute(
        select(JobberClient.client_id, JobberClient.emails, JobberClient.phones))}
    deals = [dict(r._mapping) for r in session.execute(select(
        PipedriveDeal.deal_id, PipedriveDeal.jobber_quote_id, PipedriveDeal.person_emails, PipedriveDeal.person_phones,
        PipedriveDeal.add_time, PipedriveDeal.update_time, PipedriveDeal.won_time, PipedriveDeal.lost_time,
        PipedriveDeal.status, PipedriveDeal.value, PipedriveDeal.segment))]
    existing = {(m.quote_id, m.deal_id): m for m in session.scalars(select(DealMatch))}
    confirmed = {m.quote_id: m.deal_id for m in existing.values() if m.confirmed_at and m.is_active}

    matches = match_quotes(quotes, clients, deals, confirmed)
    produced = set()
    counts: dict[str, int] = {}
    for m in matches:
        key = (m.quote_id, m.deal_id)
        produced.add(key)
        counts[m.method] = counts.get(m.method, 0) + 1
        row = existing.get(key)
        if row is None:
            session.add(DealMatch(quote_id=m.quote_id, deal_id=m.deal_id, match_method=m.method, confidence=m.confidence,
                                  reason=m.reason, signals=m.signals))
        elif row.confirmed_at is None:
            row.match_method, row.confidence, row.reason, row.signals, row.is_active = m.method, m.confidence, m.reason, m.signals, True
    for key, row in existing.items():
        if key not in produced and row.confirmed_at is None and row.is_active:
            row.is_active = False
            row.updated_at = utcnow()

    segs = quote_segments(quotes, matches, {d["deal_id"]: d for d in deals})
    for qid, seg in segs.items():
        session.execute(update(JobberQuote).where(JobberQuote.quote_id == qid).values(segment=seg))
    session.flush()
    counts["quotes_matched"] = len({m.quote_id for m in matches if m.method != "REVIEW_REQUIRED"})
    counts["quotes_review"] = len({m.quote_id for m in matches if m.method == "REVIEW_REQUIRED"})
    return counts
