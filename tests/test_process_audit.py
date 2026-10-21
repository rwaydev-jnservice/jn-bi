"""Expected-stage engine, gap rules and reconciliation rules."""

from datetime import datetime, timezone

from services.process_audit import evaluate, expected_state, stage_key
from services.reconciliation import status_check, values_match

T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_stage_names_from_both_sales_pipelines():
    assert stage_key("Visit ") == "VISIT"  # trailing space seen in Pipedrive
    assert stage_key("Negotiate") == stage_key("Negotiation") == "NEGOTIATE"
    assert stage_key("Opportunity") == "OPPORTUNITY"
    assert stage_key("Met/Reviewed") is None


def test_expected_priority_order():
    assert expected_state(True, T, T, T, T).status == "WON"
    assert expected_state(False, None, T, T, T).stage == "NEGOTIATE"
    assert expected_state(False, None, None, T, T).stage == "QUOTE"
    assert expected_state(False, None, None, None, T).stage == "VISIT"
    assert expected_state(False) is None


def test_behind_is_action_with_required_code():
    gaps = evaluate(expected_state(False, None, T), "open", "Quote")
    assert [(g.gap_type, g.severity) for g in gaps] == [("PIPE_STAGE_NEGOTIATE_REQUIRED", "ACTION")]
    gaps = evaluate(expected_state(False, None, None, None, T), "open", "Lead")
    assert gaps[0].gap_type == "PIPE_STAGE_VISIT_REQUIRED"


def test_in_stage_is_ok_and_ahead_is_info():
    assert evaluate(expected_state(False, None, T), "open", "Negotiate") == []
    assert evaluate(expected_state(False, None, T), "open", "Negotiation") == []
    gaps = evaluate(expected_state(False, None, None, T), "open", "Negotiate")
    assert [(g.gap_type, g.severity) for g in gaps] == [("DEAL_STAGE_AHEAD", "INFO")]


def test_converted_quote_requires_won():
    exp = expected_state(True, T)
    assert evaluate(exp, "won", "Negotiate") == []
    assert evaluate(exp, "open", "Negotiate")[0].gap_type == "PIPE_WON_REQUIRED"
    assert evaluate(exp, "lost", "Negotiate")[0].gap_type == "STATUS_MISMATCH"


def test_won_deal_without_converted_quote():
    assert evaluate(expected_state(False, None, T), "won", "Negotiate")[0].gap_type == "STATUS_MISMATCH"
    assert evaluate(expected_state(False, None, None, None, T), "won", "Visit", "SERVICE_JOB") == []


def test_unmapped_stage_is_info():
    g = evaluate(expected_state(False, None, T), "open", "Backup")
    assert g[0].gap_type == "STAGE_UNMAPPED" and g[0].severity == "INFO"


def test_values_match_tolerance():
    assert values_match(1000, 1000.5) and values_match(100000, 100900)
    assert not values_match(1000, 1200) and not values_match(None, 5)


def test_reconciliation_status_rules():
    assert status_check("converted", "won", []) == "OK"
    assert status_check("converted", "open", []) == "JOBBER_CONVERTED_PIPE_NOT_WON"
    assert status_check("converted", "lost", []) == "STATUS_MISMATCH"
    assert status_check("awaiting_response", "won", []) == "PIPE_WON_JOBBER_NOT_CONVERTED"
    assert status_check("archived", "won", ["converted"]) == "OK"  # another option on the deal converted
    assert status_check("awaiting_response", "lost", []) == "STATUS_MISMATCH"
    assert status_check("awaiting_response", "open", []) == "OK"
