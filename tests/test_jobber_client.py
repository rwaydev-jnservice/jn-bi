"""Jobber client: read-only guard, pagination, throttling, auth retry, schema validity."""

import json

import httpx
import pytest

from jobber import queries as Q
from jobber.client import JobberClient, JobberError, ReadOnlyViolation, assert_read_only
from jobber.schema import validate_document


def make_client(handler, tokens=None):
    tokens = tokens if tokens is not None else []

    def provide(force):
        tokens.append(force)
        return "tok-new" if force else "tok"

    return JobberClient(provide, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


@pytest.mark.parametrize("name,doc", list(Q.ALL_DOCUMENTS.items()))
def test_every_query_is_valid_against_2026_05_12_schema(name, doc):
    assert validate_document(doc) == [], name


@pytest.mark.parametrize("name,doc", list(Q.ALL_DOCUMENTS.items()))
def test_every_query_is_read_only(name, doc):
    assert_read_only(doc)


@pytest.mark.parametrize("doc", [
    "mutation { quoteCreate(input: {}) { quote { id } } }",
    "mutation M { jobClose(jobId: \"x\") { job { id } } }",
    "query Q { account { id } } mutation M { x }",
    "subscription { anything }",
])
def test_mutations_are_refused_and_never_sent(doc):
    sent = []
    client = make_client(lambda req: sent.append(req) or httpx.Response(200, json={"data": {}}))
    with pytest.raises(ReadOnlyViolation):
        client.execute(doc)
    assert sent == []


def _page(nodes, has_next, cursor):
    return {"data": {"quotes": {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}},
            "extensions": {"cost": {"requestedQueryCost": 100, "actualQueryCost": 50,
                                    "throttleStatus": {"maximumAvailable": 10000, "currentlyAvailable": 9000, "restoreRate": 500}}}}


def test_pagination_follows_cursors():
    pages = {None: _page([{"id": "1"}, {"id": "2"}], True, "c1"), "c1": _page([{"id": "3"}], True, "c2"), "c2": _page([{"id": "4"}], False, None)}
    seen_after = []

    def handler(req):
        body = json.loads(req.content)
        assert req.headers["X-JOBBER-GRAPHQL-VERSION"] == "2026-05-12"
        assert req.headers["Authorization"] == "Bearer tok"
        seen_after.append(body["variables"]["after"])
        assert body["variables"]["first"] == 2
        return httpx.Response(200, json=pages[body["variables"]["after"]])

    ids = [n["id"] for n in make_client(handler).paginate(Q.QUOTES, "quotes", {"filter": None}, page_size=2)]
    assert ids == ["1", "2", "3", "4"]
    assert seen_after == [None, "c1", "c2"]


def test_pagination_stop_when_ends_early():
    pages = {None: _page([{"id": "1"}, {"id": "2"}], True, "c1"), "c1": _page([{"id": "3"}], False, None)}
    calls = []

    def handler(req):
        after = json.loads(req.content)["variables"]["after"]
        calls.append(after)
        return httpx.Response(200, json=pages[after])

    ids = [n["id"] for n in make_client(handler).paginate(Q.QUOTES, "quotes", stop_when=lambda n: n["id"] == "2")]
    assert ids == ["1"] and calls == [None]


def test_throttled_error_is_retried():
    responses = [
        {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
         "extensions": {"cost": {"requestedQueryCost": 2000, "throttleStatus": {"maximumAvailable": 10000, "currentlyAvailable": 100, "restoreRate": 500}}}},
        _page([{"id": "1"}], False, None),
    ]
    client = make_client(lambda req: httpx.Response(200, json=responses.pop(0)))
    assert [n["id"] for n in client.paginate(Q.QUOTES, "quotes")] == ["1"]
    assert client.throttle.available == 9000


def test_too_expensive_page_is_halved_not_retried_forever():
    sizes = []

    def handler(req):
        first = json.loads(req.content)["variables"]["first"]
        sizes.append(first)
        if first * 500 > 10000:  # only pages whose cost exceeds the bucket are refused
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                                             "extensions": {"cost": {"requestedQueryCost": first * 500,
                                                                     "throttleStatus": {"maximumAvailable": 10000, "currentlyAvailable": 10000, "restoreRate": 500}}}})
        return httpx.Response(200, json=_page([{"id": "1"}], False, None))

    assert [n["id"] for n in make_client(handler).paginate(Q.QUOTES, "quotes", page_size=25)] == ["1"]
    assert sizes == [25, 12]


def test_low_budget_waits_before_request():
    waits = []
    responses = [_page([{"id": "1"}], True, "c1"), _page([{"id": "2"}], False, None)]
    responses[0]["extensions"]["cost"]["throttleStatus"]["currentlyAvailable"] = 10
    c = JobberClient(lambda f: "tok", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=responses.pop(0)))), sleep=waits.append)
    list(c.paginate(Q.QUOTES, "quotes"))
    assert waits and waits[0] > 0


def test_401_triggers_one_token_refresh():
    tokens = []
    responses = [httpx.Response(401, text="expired"), httpx.Response(200, json=_page([], False, None))]
    client = make_client(lambda req: responses.pop(0), tokens)
    list(client.paginate(Q.QUOTES, "quotes"))
    assert tokens == [False, True]


def test_5xx_retries_then_fails():
    client = make_client(lambda req: httpx.Response(502, text="bad gateway"))
    with pytest.raises(JobberError):
        client.execute(Q.ACCOUNT)


def test_graphql_errors_raise():
    client = make_client(lambda req: httpx.Response(200, json={"errors": [{"message": "Field 'x' doesn't exist"}]}))
    with pytest.raises(JobberError):
        client.execute(Q.ACCOUNT)
