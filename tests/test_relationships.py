"""Relationship rules incl. the confirmed regression case (Job #2718)."""

from jobber import normalize as N
from services import relationships as R
from tests.fixtures import (
    CAUET, CLIENT, JOB_2718, JOB_2729, PROPERTY, QUOTE_105733, WESLEY,
    job_node, quote_node, regression_nodes, visit_node,
)


def _build(jobs, quotes, visits, requests=()):
    j = [N.job(x) for x in jobs]
    q = [N.quote(x)[0] for x in quotes]
    vrows = [N.visit(x) for x in visits]
    v = [r for r, _ in vrows]
    vu = {r["visit_id"]: users for r, users in vrows}
    return R.build(list(requests), q, j, v, [], [], vu, window_days=14)


def _edge(edges, frm, to):
    return [e for e in edges if e.from_id == frm and e.to_id == to]


def test_regression_2718_inferred_and_2729_direct():
    edges, flows = _build(*regression_nodes())
    e1 = _edge(edges, JOB_2718, QUOTE_105733)
    assert len(e1) == 1
    assert e1[0].method == "INFERRED"  # never DIRECT
    assert e1[0].rel_type == "SERVICE_JOB_REPAIR_QUOTE"
    assert 0 < e1[0].confidence < 1
    assert e1[0].signals["same_client_id"] and e1[0].signals["same_property_id"]
    assert e1[0].signals["visit_technician_ids"] == [WESLEY]

    e2 = _edge(edges, QUOTE_105733, JOB_2729)
    assert len(e2) == 1 and e2[0].method == "DIRECT" and e2[0].rel_type == "QUOTE_CONVERTED_JOB"
    assert flows[QUOTE_105733] == "SERVICE"


def test_job_2718_to_quote_is_never_direct():
    edges, _ = _build(*regression_nodes())
    assert not [e for e in edges if e.from_id == JOB_2718 and e.to_id == QUOTE_105733 and e.method == "DIRECT"]


def test_converted_job_is_not_a_service_candidate():
    edges, _ = _build(*regression_nodes())
    assert not [e for e in edges if e.from_id == JOB_2729 and e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"]


def test_two_service_jobs_require_review():
    jobs, quotes, visits = regression_nodes()
    jobs.append(job_node("job-other", 2720, "REACT_SCHEDULE_DAY", created="2026-04-09T13:00:00Z", completed="2026-04-09T15:00:00Z"))
    edges, flows = _build(jobs, quotes, visits)
    rr = [e for e in edges if e.to_id == QUOTE_105733 and e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"]
    assert len(rr) == 2 and all(e.method == "REVIEW_REQUIRED" for e in rr)
    assert {tuple(e.signals["competing_job_ids"]) for e in rr} == {("job-other",), (JOB_2718,)}
    assert flows[QUOTE_105733] == "SERVICE"


def test_different_client_id_never_matches_even_with_same_address():
    jobs, _, visits = regression_nodes()
    q = quote_node("q-x", 1, request_id=None, client="other-client", prop_id=PROPERTY, jobs=[])
    edges, flows = _build(jobs, [q], visits)
    assert not [e for e in edges if e.to_id == "q-x" and e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"]
    assert flows["q-x"] == "UNLINKED"


def test_different_property_never_matches():
    jobs, _, visits = regression_nodes()
    q = quote_node("q-y", 2, client=CLIENT, prop_id="other-property", jobs=[])
    edges, _ = _build(jobs, [q], visits)
    assert not [e for e in edges if e.to_id == "q-y"]


def test_quote_outside_window_not_inferred():
    jobs, _, visits = regression_nodes()
    q = quote_node("q-late", 3, created="2026-06-30T12:00:00Z", jobs=[])
    edges, flows = _build(jobs, [q], visits)
    assert not [e for e in edges if e.to_id == "q-late" and e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"]
    assert flows["q-late"] == "UNLINKED"


def test_quote_before_job_not_inferred():
    jobs, _, visits = regression_nodes()
    q = quote_node("q-early", 4, created="2026-03-01T12:00:00Z", jobs=[])
    edges, _ = _build(jobs, [q], visits)
    assert not [e for e in edges if e.to_id == "q-early" and e.rel_type == "SERVICE_JOB_REPAIR_QUOTE"]


def test_estimate_quote_with_request_is_direct_not_inferred():
    jobs, _, visits = regression_nodes()
    q = quote_node("q-est", 5, request_id="req-1", jobs=[])
    edges, flows = _build(jobs, [q], visits)
    assert flows["q-est"] == "ESTIMATE"
    assert [e.method for e in edges if e.to_id == "q-est"] == ["DIRECT"]
    assert _edge(edges, "req-1", "q-est")[0].rel_type == "REQUEST_QUOTE"


def test_job_visit_and_salesperson_signal():
    jobs, quotes, visits = regression_nodes()
    visits[0] = visit_node("visit-2718-1", JOB_2718, users=(CAUET,))
    edges, _ = _build(jobs, quotes, visits)
    e = _edge(edges, JOB_2718, QUOTE_105733)[0]
    assert e.signals["salesperson_attended_visit"] is True
    assert _edge(edges, JOB_2718, "visit-2718-1")[0].method == "DIRECT"
