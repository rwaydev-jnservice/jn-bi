import httpx
import pytest

from pipedrive.client import PipedriveClient, ReadOnlyViolation


def make(handler):
    return PipedriveClient("tok", "jnservice", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_pagination_walks_all_pages():
    def handler(req):
        start = int(req.url.params["start"])
        assert req.method == "GET" and req.url.params["api_token"] == "tok"
        assert req.url.host == "jnservice.pipedrive.com"
        data = {0: [{"id": 1}, {"id": 2}], 2: [{"id": 3}]}[start]
        more = start == 0
        return httpx.Response(200, json={"success": True, "data": data,
                                         "additional_data": {"pagination": {"more_items_in_collection": more, "next_start": 2 if more else None}}})

    assert [d["id"] for d in make(handler).get_all("/deals", {"status": "open"}, limit=2)] == [1, 2, 3]


def test_pagination_stop_before():
    def handler(req):
        return httpx.Response(200, json={"success": True, "data": [{"id": 1}, {"id": 2}],
                                         "additional_data": {"pagination": {"more_items_in_collection": True, "next_start": 2}}})

    assert [d["id"] for d in make(handler).get_all("/deals", stop_before=lambda d: d["id"] == 2)] == [1]


def test_null_data_page_is_empty():
    c = make(lambda r: httpx.Response(200, json={"success": True, "data": None}))
    assert list(c.get_all("/activities")) == []


def test_non_get_is_refused():
    sent = []
    c = make(lambda r: sent.append(r) or httpx.Response(200, json={}))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ReadOnlyViolation):
            c._request(method, "/deals/1")
    assert sent == []
    assert not any(hasattr(c, m) for m in ("post", "put", "patch", "delete"))


def test_429_is_retried():
    responses = [httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"success": True, "data": []})]
    assert make(lambda r: responses.pop(0)).get("/deals") == {"success": True, "data": []}
