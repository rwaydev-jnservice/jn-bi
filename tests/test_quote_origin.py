"""Quote origin classification engine: regression cases A-H."""

from datetime import datetime, timezone

from jobber import normalize as N
from services import quote_origin as QO
from tests.fixtures import JOB_2718, JOB_2729, QUOTE_105733, regression_nodes

C, P = "client-1", "prop-1"


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def quote(qid="q1", created="2026-10-10T15:00:00", request_id=None, client=C, prop=P, converted=()):
    return {"quote_id": qid, "request_id": request_id, "client_id": client, "property_id": prop, "created_at": ts(created),
            "converted_job_ids": list(converted)}


def job(jid, created="2026-10-08T12:00:00", request_id=None, origin_quote_id=None, client=C, prop=P, number=1):
    return {"job_id": jid, "job_number": number, "client_id": client, "property_id": prop, "request_id": request_id,
            "origin_quote_id": origin_quote_id, "created_at": ts(created), "start_at": None, "completed_at": None}


def visit(vid, jid, start="2026-10-10T13:00:00", completed="2026-10-10T14:30:00"):
    return {"visit_id": vid, "job_id": jid, "start_at": ts(start), "end_at": ts(start), "completed_at": ts(completed) if completed else None}


def request(rid, created="2026-10-01T12:00:00", client=C, prop=P):
    return {"request_id": rid, "client_id": client, "property_id": prop, "created_at": ts(created)}


def assessment(aid, rid, start="2026-10-05T14:00:00", completed="2026-10-05T15:00:00"):
    return {"assessment_id": aid, "request_id": rid, "start_at": ts(start), "end_at": ts(start), "completed_at": ts(completed) if completed else None}


def run(quotes, requests=(), assessments=(), jobs=(), visits=()):
    res = QO.classify(list(quotes), list(requests), list(assessments), list(jobs), list(visits), service_window_days=14, estimate_window_days=30)
    return {o.quote_id: o for o in res}


def test_A_direct_estimate():
    o = run([quote(request_id="r1")], [request("r1")], [assessment("a1", "r1")])["q1"]
    assert (o.flow_type, o.method, o.request_id, o.assessment_id, o.confidence) == ("ESTIMATE", "DIRECT", "r1", "a1", 1.0)


def test_A_direct_estimate_wins_even_with_service_candidate():
    o = run([quote(request_id="r1")], [request("r1")], [], [job("j1")], [visit("v1", "j1")])["q1"]
    assert (o.flow_type, o.method) == ("ESTIMATE", "DIRECT")


def test_B_inferred_service():
    o = run([quote()], jobs=[job("j1")], visits=[visit("v1", "j1")])["q1"]
    assert (o.flow_type, o.method, o.service_job_id, o.visit_id) == ("SERVICE", "INFERRED", "j1", "v1")
    assert 0.8 <= o.confidence <= 0.95 and "Job #1" in o.reason


def test_B_service_requires_visit_before_quote():
    o = run([quote(created="2026-10-10T09:00:00")], jobs=[job("j1")], visits=[visit("v1", "j1", start="2026-10-10T13:00:00")])["q1"]
    assert o.flow_type == "OTHER"


def test_B_quote_converted_job_and_quote_created_job_are_not_origins():
    jobs = [job("j-conv", created="2026-10-09T12:00:00"), job("j-from-quote", origin_quote_id="other-quote")]
    visits = [visit("v1", "j-conv"), visit("v2", "j-from-quote")]
    o = run([quote(converted=["j-conv"])], jobs=jobs, visits=visits)["q1"]
    assert o.flow_type == "OTHER"


def test_C_inferred_estimate():
    o = run([quote()], [request("r1")], [assessment("a1", "r1")])["q1"]
    assert (o.flow_type, o.method, o.request_id, o.assessment_id) == ("ESTIMATE", "INFERRED", "r1", "a1")
    assert o.confidence == 0.9  # assessment completed, quote 5 days later
    late = run([quote(created="2026-10-25T15:00:00")], [request("r1")], [assessment("a1", "r1")])["q1"]
    assert late.flow_type == "ESTIMATE" and late.confidence == 0.8  # 20 days later: still in window, less confident


def test_C_request_without_assessment_is_not_a_candidate():
    assert run([quote()], [request("r1")], [])["q1"].flow_type == "OTHER"


def test_D_service_and_estimate_both_plausible_is_review():
    o = run([quote()], [request("r1")], [assessment("a1", "r1")], [job("j1")], [visit("v1", "j1")])["q1"]
    assert (o.flow_type, o.method) == ("REVIEW_REQUIRED", "REVIEW_REQUIRED")
    assert o.candidate_service_job_ids == ["j1"] and o.candidate_request_ids == ["r1"] and o.candidate_visit_ids == ["v1"]
    assert o.service_job_id is None and o.request_id is None


def test_E_multiple_service_jobs_is_review():
    jobs = [job("j1", number=1), job("j2", created="2026-10-09T12:00:00", number=2)]
    o = run([quote()], jobs=jobs, visits=[visit("v1", "j1"), visit("v2", "j2", start="2026-10-09T13:00:00", completed="2026-10-09T14:00:00")])["q1"]
    assert o.flow_type == "REVIEW_REQUIRED" and sorted(o.candidate_service_job_ids) == ["j1", "j2"]


def test_F_multiple_requests_is_review():
    reqs = [request("r1"), request("r2", created="2026-10-02T12:00:00")]
    o = run([quote()], reqs, [assessment("a1", "r1"), assessment("a2", "r2", start="2026-10-06T14:00:00")])["q1"]
    assert o.flow_type == "REVIEW_REQUIRED" and sorted(o.candidate_request_ids) == ["r1", "r2"]


def test_G_unlinked():
    o = run([quote()], [request("r9", client="other")], [assessment("a9", "r9")], [job("j9", prop="other-prop")], [visit("v9", "j9")])["q1"]
    assert (o.flow_type, o.method) == ("OTHER", "UNLINKED")


def test_G_same_name_or_address_never_links():
    # different client_id, same property id -> no link (IDs only)
    o = run([quote()], jobs=[job("j1", client="client-2")], visits=[visit("v1", "j1")])["q1"]
    assert o.flow_type == "OTHER"


def test_window_limits():
    late_service = run([quote(created="2026-11-05T15:00:00")], jobs=[job("j1")], visits=[visit("v1", "j1")])["q1"]
    assert late_service.flow_type == "OTHER"  # > 14 days after the visit
    late_estimate = run([quote(created="2026-11-20T15:00:00")], [request("r1")], [assessment("a1", "r1")])["q1"]
    assert late_estimate.flow_type == "OTHER"  # > 30 days after the assessment


def test_H_known_real_example_2718_105733_2729():
    jobs_n, quotes_n, visits_n = regression_nodes()
    jobs = [N.job(j) for j in jobs_n]
    quotes = [N.quote(q)[0] for q in quotes_n]
    visits = [N.visit(v)[0] for v in visits_n]
    o = {x.quote_id: x for x in QO.classify(quotes, [], [], jobs, visits, service_window_days=14, estimate_window_days=30)}[QUOTE_105733]
    assert (o.flow_type, o.method) == ("SERVICE", "INFERRED")  # NEVER DIRECT
    assert o.service_job_id == JOB_2718 and o.visit_id == "visit-2718-1"
    assert JOB_2729 not in (o.candidate_service_job_ids or [])  # the converted job is a descendant, not the origin
    q = next(x for x in quotes if x["quote_id"] == QUOTE_105733)
    assert q["converted_job_ids"] == [JOB_2729]  # descendant stays DIRECT and separate
