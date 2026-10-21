"""Pipedrive normalization, quote->deal matching and segments."""

from datetime import datetime, timezone
from decimal import Decimal

from pipedrive import normalize as PN
from services import deal_matching as DM
from services import segments as SEG

CAT = {"key": "catkey", "labels": {94: "Small Jobs", 96: "Big Contracts - Maintenance", 97: "Big Jobs - GCs"}}


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def raw_deal(did, cat=94, email="a@x.com", phone="(617) 555-0100", status="open", value=1000, add="2026-09-01 10:00:00", **kw):
    return {"id": did, "title": f"Deal {did}", "status": status, "value": value, "currency": "USD", "pipeline_id": 1, "stage_id": 3,
            "add_time": add, "update_time": "2026-09-20 10:00:00", "won_time": kw.get("won"), "lost_time": kw.get("lost"),
            "user_id": {"id": 7, "name": "Cauet Passos", "value": 7}, "catkey": cat,
            "person_id": {"value": 500 + did, "name": "Client", "email": [{"value": email, "primary": True}],
                          "phone": [{"value": phone, "primary": True}], "owner_id": 7}}


def deal_row(*a, **k):
    return PN.deal(raw_deal(*a, **k), CAT)


def quote(qid="q1", client="c1", total=1000, created="2026-09-10T12:00:00"):
    return {"quote_id": qid, "quote_number": "1", "client_id": client, "created_at": ts(created), "total": Decimal(total)}


CLIENTS = {"c1": {"emails": ["a@x.com"], "phones": ["6175550100"]}}


def test_normalize_deal_category_owner_and_person():
    d = deal_row(1, cat="97")
    assert d["category"] == "Big Jobs - GCs" and d["segment"] == SEG.GC_PROJECT
    assert d["owner_id"] == 7 and d["owner_name"] == "Cauet Passos" and d["person_id"] == 501
    assert d["person_emails"] == ["a@x.com"] and d["person_phones"] == ["6175550100"]
    assert d["add_time"] == ts("2026-09-01T10:00:00")
    assert PN.deal({**raw_deal(2), "catkey": None}, CAT)["segment"] == SEG.UNCATEGORIZED


def test_segment_mapping_matches_existing_reports():
    assert SEG.segment_for_category("Reactivation") == SEG.RESIDENTIAL
    assert SEG.segment_for_category("Big Jobs - Homeowners") == SEG.RESIDENTIAL
    assert SEG.segment_for_category("Big Contracts - Maintenance") == SEG.PROPERTY_MANAGEMENT
    assert SEG.segment_for_category(None) == SEG.UNCATEGORIZED


def test_single_contact_match():
    ms = DM.match_quotes([quote()], CLIENTS, [deal_row(1)])
    assert len(ms) == 1 and ms[0].method == "CONTACT_MATCH" and ms[0].deal_id == 1
    assert ms[0].signals["matched_on"] == ["email", "phone"] and ms[0].confidence >= 0.9


def test_no_contact_overlap_no_match():
    assert DM.match_quotes([quote()], {"c1": {"emails": ["z@z.com"], "phones": []}}, [deal_row(1)]) == []


def test_old_closed_deal_is_not_a_candidate():
    old = deal_row(1, status="lost", add="2025-01-01 10:00:00", lost="2025-02-01 10:00:00")
    assert DM.match_quotes([quote()], CLIENTS, [old]) == []


def test_deal_created_long_after_quote_is_not_a_candidate():
    assert DM.match_quotes([quote()], CLIENTS, [deal_row(1, add="2026-12-30 10:00:00")]) == []


def test_value_breaks_tie():
    ms = DM.match_quotes([quote(total=2500)], CLIENTS, [deal_row(1, value=900), deal_row(2, value=2500)])
    assert [(m.deal_id, m.method) for m in ms] == [(2, "CONTACT_MATCH")]


def test_ambiguous_is_review_required():
    ms = DM.match_quotes([quote(total=2500)], CLIENTS, [deal_row(1, value=900), deal_row(2, value=1200)])
    assert {m.method for m in ms} == {"REVIEW_REQUIRED"} and {m.deal_id for m in ms} == {1, 2}


def test_confirmed_wins():
    ms = DM.match_quotes([quote()], CLIENTS, [deal_row(1), deal_row(2)], confirmed={"q1": 2})
    assert [(m.deal_id, m.method) for m in ms] == [(2, "CONFIRMED")]


def test_explicit_jobber_quote_id_wins():
    d = {**deal_row(9), "jobber_quote_id": "q1", "person_emails": [], "person_phones": []}
    ms = DM.match_quotes([quote()], CLIENTS, [d, deal_row(1)])
    assert [(m.deal_id, m.method) for m in ms] == [(9, "JOBBER_QUOTE_ID")]


def test_quote_number_written_in_different_ways():
    q = {**quote(total=7), "quote_number": "105733"}
    for written in ("105733", "#105733", " Q-105733 ", "105620, 105733"):
        d = {**deal_row(9), "jobber_quote_id": written, "person_emails": [], "person_phones": []}
        ms = DM.match_quotes([q], CLIENTS, [d, deal_row(1)])
        assert [(m.deal_id, m.method) for m in ms] == [(9, "JOBBER_QUOTE_ID")], written


def test_quote_number_beats_ambiguous_gc_contact():
    q = {**quote(total=5000), "quote_number": "105837"}
    gc_a, gc_b = deal_row(1, cat=97, value=900), {**deal_row(2, cat=97, value=1200), "jobber_quote_id": "105837"}
    ms = DM.match_quotes([q], CLIENTS, [gc_a, gc_b])
    assert [(m.deal_id, m.method) for m in ms] == [(2, "JOBBER_QUOTE_ID")]


def test_segments_from_matches():
    deals = [deal_row(1, cat=97, value=900), deal_row(2, cat=97, value=1200), deal_row(3, cat=96, email="p@m.com", phone="")]
    quotes = [quote("q1", total=5000), quote("q2", client="c2"), quote("q3", client="none")]
    clients = {**CLIENTS, "c2": {"emails": ["p@m.com"], "phones": []}}
    ms = DM.match_quotes(quotes, clients, deals)
    segs = DM.quote_segments(quotes, ms, {d["deal_id"]: d for d in deals})
    assert segs["q1"] == SEG.GC_PROJECT  # review needed, but both candidates are GC
    assert segs["q2"] == SEG.PROPERTY_MANAGEMENT
    assert segs["q3"] == SEG.NO_DEAL_MATCH
