"""Thin async client for the Enable Banking API (https://enablebanking.com/docs/api/reference/).

Authentication is a short-lived JWT signed RS256 with the application's private key (``kid`` = application id).
Only the account-information endpoints are used: there is no payment initiation anywhere in this code base.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
import jwt

MAX_PAGES = 60
SESSION_GONE = {"EXPIRED_SESSION", "REVOKED_SESSION", "CLOSED_SESSION", "SESSION_DOES_NOT_EXIST", "WRONG_SESSION_STATUS"}
RATE_LIMITED = {"ASPSP_RATE_LIMIT_EXCEEDED"}


class EnableBankingError(Exception):
    def __init__(self, status: int, code: str | None, message: str):
        super().__init__(f"{code or status}: {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def session_gone(self) -> bool:
        return self.code in SESSION_GONE

    @property
    def rate_limited(self) -> bool:
        return self.code in RATE_LIMITED or self.status == 429


def make_jwt(app_id: str, private_key_pem: bytes, ttl_seconds: int = 3600, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": issued, "exp": issued + ttl_seconds}
    return jwt.encode(payload, private_key_pem, algorithm="RS256", headers={"kid": app_id, "typ": "JWT"})


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key_pem: bytes,
        base_url: str = "https://api.enablebanking.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ):
        self.app_id = app_id
        self._key = private_key_pem
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    def _auth_header(self) -> str:
        if self._token is None or time.time() > self._token_expires - 60:
            self._token = make_jwt(self.app_id, self._key)
            self._token_expires = time.time() + 3600
        return f"Bearer {self._token}"

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        sent = {"Authorization": self._auth_header(), **(headers or {})}
        response = await self._http.request(method, path, json=json, params=params, headers=sent)
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise EnableBankingError(response.status_code, body.get("error"), str(body.get("message") or response.text)[:500])
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------ banks

    async def aspsps(self, country: str | None = None, psu_type: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in (("country", country), ("psu_type", psu_type)) if v}
        data = await self._request("GET", "/aspsps", params=params or None)
        return list((data or {}).get("aspsps") or [])

    # ------------------------------------------------------------------ consent

    async def start_auth(
        self,
        aspsp_name: str,
        country: str,
        state: str,
        redirect_url: str,
        valid_until: datetime,
        psu_type: str = "personal",
        language: str = "it",
    ) -> dict[str, Any]:
        body = {
            "access": {"valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
            "language": language,
        }
        return await self._request("POST", "/auth", json=body)

    async def create_session(self, code: str) -> dict[str, Any]:
        return await self._request("POST", "/sessions", json={"code": code})

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/sessions/{session_id}")

    async def delete_session(self, session_id: str) -> None:
        await self._request("DELETE", f"/sessions/{session_id}")

    # ------------------------------------------------------------------ account data

    async def balances(self, account_uid: str, psu_headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/accounts/{account_uid}/balances", headers=psu_headers)
        return list((data or {}).get("balances") or [])

    async def transactions(
        self,
        account_uid: str,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        psu_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Every page of the account's transactions in the period (``continuation_key`` paging)."""
        out: list[dict[str, Any]] = []
        continuation: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {}
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            if status:
                params["transaction_status"] = status
            if continuation:
                params["continuation_key"] = continuation
            data = await self._request("GET", f"/accounts/{account_uid}/transactions", params=params or None, headers=psu_headers) or {}
            out.extend(data.get("transactions") or [])
            continuation = data.get("continuation_key")
            if not continuation:
                break
        return out
