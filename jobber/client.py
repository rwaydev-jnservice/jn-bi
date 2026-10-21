"""READ-ONLY Jobber GraphQL client.

Safety: every document is parsed before sending. Anything other than a
`query` operation (mutation, subscription) raises ReadOnlyViolation and is
never sent. There is intentionally no method for mutations.

Rate limits: Jobber uses query-cost throttling and reports the bucket state in
`extensions.cost.throttleStatus` (maximumAvailable / currentlyAvailable /
restoreRate). The client waits when the bucket is low and backs off on
THROTTLED errors and HTTP 429/5xx.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import httpx
from graphql import FragmentDefinitionNode, OperationDefinitionNode, OperationType, parse

from config import get_settings

log = logging.getLogger(__name__)


class ReadOnlyViolation(Exception):
    """Raised when something tries to send a non-query operation to Jobber."""


class JobberError(Exception):
    pass


class JobberAuthError(JobberError):
    pass


class QueryTooExpensive(JobberError):
    """The query's requested cost exceeds Jobber's whole bucket; waiting won't help."""


def assert_read_only(document: str) -> None:
    doc = parse(document)
    for definition in doc.definitions:
        if isinstance(definition, FragmentDefinitionNode):
            continue
        if not isinstance(definition, OperationDefinitionNode):
            raise ReadOnlyViolation(f"Unsupported GraphQL definition: {definition.kind}")
        if definition.operation != OperationType.QUERY:
            raise ReadOnlyViolation(f"Jobber is READ ONLY in V1: refusing '{definition.operation.value}' operation.")


@dataclass
class ThrottleState:
    maximum: float | None = None
    available: float | None = None
    restore_rate: float | None = None
    last_cost: float = 0.0


class JobberClient:
    def __init__(
        self,
        token_provider: Callable[[bool], str],
        http: httpx.Client | None = None,
        api_version: str | None = None,
        url: str | None = None,
        max_retries: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """token_provider(force_refresh) -> access token."""
        s = get_settings()
        self._token_provider = token_provider
        self._http = http or httpx.Client(timeout=60)
        self._version = api_version or s.jobber_api_version
        self._url = url or s.jobber_graphql_url
        self._max_retries = max_retries
        self._sleep = sleep
        self.throttle = ThrottleState()
        self.requests_made = 0

    # ------------------------------------------------------------------
    def _wait_for_budget(self) -> None:
        t = self.throttle
        if t.available is None or not t.restore_rate or not t.last_cost:
            return
        needed = t.last_cost * 1.1
        if t.available < needed:
            wait = min((needed - t.available) / t.restore_rate, 30)
            log.info("Jobber throttle: waiting %.1fs (available %.0f, need ~%.0f)", wait, t.available, needed)
            self._sleep(wait)

    def _record_cost(self, payload: dict) -> None:
        cost = (payload.get("extensions") or {}).get("cost") or {}
        status = cost.get("throttleStatus") or {}
        if status:
            self.throttle.maximum = status.get("maximumAvailable")
            self.throttle.available = status.get("currentlyAvailable")
            self.throttle.restore_rate = status.get("restoreRate")
        if cost.get("requestedQueryCost") is not None:
            self.throttle.last_cost = float(cost["requestedQueryCost"])

    def execute(self, document: str, variables: dict[str, Any] | None = None) -> dict:
        assert_read_only(document)  # never send a mutation
        force_refresh = False
        attempt = 0
        while True:
            attempt += 1
            self._wait_for_budget()
            token = self._token_provider(force_refresh)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-JOBBER-GRAPHQL-VERSION": self._version,
                "Content-Type": "application/json",
            }
            try:
                resp = self._http.post(self._url, json={"query": document, "variables": variables or {}}, headers=headers)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise JobberError(f"Network error talking to Jobber: {exc}") from exc
                self._sleep(min(2**attempt, 60))
                continue
            self.requests_made += 1

            if resp.status_code == 401:
                if force_refresh:
                    raise JobberAuthError("Jobber rejected the access token after refresh. Reconnect Jobber (Connections page).")
                force_refresh = True
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self._max_retries:
                    raise JobberError(f"Jobber HTTP {resp.status_code} after {attempt} attempts: {resp.text[:300]}")
                retry_after = resp.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 60))
                continue
            if resp.status_code >= 400:
                raise JobberError(f"Jobber HTTP {resp.status_code}: {resp.text[:500]}")

            payload = resp.json()
            self._record_cost(payload)
            errors = payload.get("errors") or []
            if errors:
                throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" or e.get("message") == "Throttled" for e in errors)
                if throttled:
                    t = self.throttle
                    if t.maximum and t.last_cost and t.last_cost > t.maximum:
                        raise QueryTooExpensive(f"Query costs {t.last_cost:.0f} points; Jobber's maximum is {t.maximum:.0f}. Use a smaller page.")
                    if attempt > self._max_retries:
                        raise JobberError("Jobber kept throttling the request.")
                    t = self.throttle
                    wait = ((t.last_cost or 1000) / (t.restore_rate or 500)) + 1
                    self._sleep(min(wait, 60))
                    continue
                raise JobberError(f"Jobber GraphQL errors: {errors}")
            return payload.get("data") or {}

    def paginate(
        self,
        document: str,
        root_field: str,
        variables: dict[str, Any] | None = None,
        page_size: int | None = None,
        stop_when: Callable[[dict], bool] | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """Yield nodes of a Relay connection (`nodes` + `pageInfo`).

        stop_when(node) -> True stops iteration *before* yielding that node
        (used for sorted, early-exit incremental sync)."""
        variables = dict(variables or {})
        variables["first"] = page_size or get_settings().jobber_page_size
        after = None
        pages = 0
        while True:
            variables["after"] = after
            try:
                data = self.execute(document, variables)
            except QueryTooExpensive:
                if variables["first"] <= 1:
                    raise
                variables["first"] = max(1, variables["first"] // 2)
                log.warning("Jobber query too expensive; retrying with page size %s", variables["first"])
                continue
            conn = data.get(root_field) or {}
            for node in conn.get("nodes") or []:
                if stop_when and stop_when(node):
                    return
                yield node
            pages += 1
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage") or (max_pages and pages >= max_pages):
                return
            after = info.get("endCursor")
            if not after:
                return
