"""READ-ONLY Pipedrive client (API v1, API-token auth).

Only GET requests exist on this class. There is deliberately no post/put/
patch/delete, and `_request` refuses any other HTTP method.
Patterns (start/limit pagination, trailing-space stage names, pipeline 1 =
sales pipeline) match the company's existing, working Pipedrive report.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx

from config import get_settings


class PipedriveError(Exception):
    pass


class ReadOnlyViolation(Exception):
    pass


class PipedriveClient:
    def __init__(self, api_token: str | None = None, domain: str | None = None, http: httpx.Client | None = None,
                 max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        s = get_settings()
        self._token = api_token or s.pipedrive_api_token
        domain = domain or s.pipedrive_company_domain
        if not self._token or not domain:
            raise PipedriveError("PIPEDRIVE_API_TOKEN and PIPEDRIVE_COMPANY_DOMAIN must be set.")
        self._base = f"https://{domain}.pipedrive.com/api/v1"
        self._http = http or httpx.Client(timeout=60)
        self._max_retries = max_retries
        self._sleep = sleep
        self.requests_made = 0

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method.upper() != "GET":
            raise ReadOnlyViolation("Pipedrive is READ ONLY in V1.")
        q = {k: v for k, v in (params or {}).items() if v is not None}
        q["api_token"] = self._token
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self._http.get(f"{self._base}{path}", params=q)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise PipedriveError(f"Network error: {exc}") from exc
                self._sleep(min(2**attempt, 30))
                continue
            self.requests_made += 1
            if r.status_code == 429 or r.status_code >= 500:
                if attempt > self._max_retries:
                    raise PipedriveError(f"Pipedrive HTTP {r.status_code} after retries ({path})")
                reset = r.headers.get("x-ratelimit-reset") or r.headers.get("Retry-After")
                self._sleep(float(reset) if reset and reset.replace(".", "").isdigit() else min(2**attempt, 30))
                continue
            if r.status_code >= 400:
                raise PipedriveError(f"Pipedrive HTTP {r.status_code} ({path}): {r.text[:300]}")
            body = r.json()
            if body.get("success") is False:
                raise PipedriveError(f"Pipedrive error ({path}): {body.get('error')}")
            return body

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params)

    def get_all(self, path: str, params: dict | None = None, limit: int = 500,
                stop_before: Callable[[dict], bool] | None = None) -> Iterator[dict]:
        """Walk start/limit pagination (additional_data.pagination)."""
        start = 0
        while True:
            body = self.get(path, {**(params or {}), "start": start, "limit": limit})
            for item in body.get("data") or []:
                if stop_before and stop_before(item):
                    return
                yield item
            pg = (body.get("additional_data") or {}).get("pagination") or {}
            if not pg.get("more_items_in_collection"):
                return
            start = pg.get("next_start")
            if start is None:
                return

    # convenience
    def deal_fields(self) -> list[dict[str, Any]]:
        return list(self.get_all("/dealFields"))

    def find_deal_field(self, name: str) -> dict | None:
        target = name.strip().lower()
        return next((f for f in self.deal_fields() if (f.get("name") or "").strip().lower() == target), None)
