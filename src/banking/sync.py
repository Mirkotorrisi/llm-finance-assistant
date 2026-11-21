"""Pull balances and booked transactions from the banks into the API.

For every enabled, mapped account of a connection: the current balance becomes a ``BalanceSnapshot`` (source
``bank``) and the booked transactions since the last known one (minus an overlap) go through the same pipeline
as an uploaded statement: merchant rules, then the model, then ``POST /api/imports`` with ``source: bank`` and the
bank's ``external_id`` (the API dedupes and flags suspect twins of statement rows). Rows the bank gives without any
wording (Trade Republic) skip the model and land as ``uncategorized``.

Nothing here logs an IBAN, a session id or an amount: only counts and connection ids.
"""

from __future__ import annotations

import logging
import uuid

import httpx
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from src.api_client import FinanceApiClient, request_context
from src.banking.config import BankingConfig
from src.banking.enable_banking import EnableBankingClient, EnableBankingError
from src.banking.mapping import account_summary, pick_balance, rows_from_transactions
from src.banking.secrets import SecretStore
from src.services.classification import classify_for_import

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], EnableBankingClient]


_PUBLIC_IP_CACHE: Dict[str, Any] = {"ip": None, "at": 0.0}
PUBLIC_IP_TTL = 3600.0


async def psu_headers_for(cfg: BankingConfig, user_present: bool) -> Dict[str, str]:
    """Headers telling the bank the user is behind the call. Only when the user pressed the button and the policy
    asks for it; scheduled runs send none (they are unattended, and banks cap those at a few per day)."""
    if not user_present or cfg.psu_ip in ("", "off"):
        return {}
    ip = cfg.psu_ip
    if ip == "auto":
        import time

        if _PUBLIC_IP_CACHE["ip"] is None or time.time() - _PUBLIC_IP_CACHE["at"] > PUBLIC_IP_TTL:
            try:
                async with httpx.AsyncClient(timeout=10) as web:
                    _PUBLIC_IP_CACHE["ip"] = (await web.get("https://api.ipify.org")).text.strip()
                    _PUBLIC_IP_CACHE["at"] = time.time()
            except httpx.HTTPError:
                return {}
        ip = _PUBLIC_IP_CACHE["ip"] or ""
    return {"Psu-Ip-Address": ip} if ip else {}


@dataclass
class AccountReport:
    link_id: int
    account_id: int | None
    balance_recorded: bool = False
    transactions_fetched: int = 0
    transactions_added: int = 0
    transactions_skipped: int = 0
    suspect_duplicates: int = 0
    without_description: int = 0
    error: str | None = None


@dataclass
class SyncReport:
    connection_id: int
    workspace_id: int
    status: str = "ok"  # ok | partial | error | expired | rate_limited | skipped
    error: str | None = None
    accounts: list[AccountReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "error": self.error,
            "accounts": [a.__dict__ for a in self.accounts],
        }


class BankSync:
    def __init__(
        self,
        cfg: BankingConfig,
        store: SecretStore,
        api: FinanceApiClient,
        llm_factory: Callable[[], Any],
        client_factory: ClientFactory | None = None,
        today: Callable[[], date] | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.llm_factory = llm_factory
        self._client_factory = client_factory
        self._today = today or date.today

    def client(self) -> EnableBankingClient:
        if self._client_factory is not None:
            return self._client_factory()
        return EnableBankingClient(self.cfg.app_id or "", self.cfg.private_key(), self.cfg.api_url)

    # ------------------------------------------------------------------ entry points

    async def sync_due(self, max_age_hours: float | None = None) -> list[SyncReport]:
        """Every connection (any workspace) not synced within ``max_age_hours``."""
        hours = max_age_hours if max_age_hours is not None else self.cfg.sync_interval_hours
        due = await self.api.request("GET", "/api/bank-connections/due", params={"max_age_hours": hours})
        reports: list[SyncReport] = []
        for item in due:
            reports.extend(await self.sync_workspace(item["workspace_id"], None, item["connection_id"]))
        return reports

    async def sync_workspace(
        self,
        workspace_id: int | None,
        user_id: str | None,
        connection_id: int | None = None,
        psu_headers: dict[str, str] | None = None,
    ) -> list[SyncReport]:
        with request_context(workspace_id, user_id):
            connections = await self.api.request("GET", "/api/bank-connections")
            if connection_id is not None:
                connections = [c for c in connections if c["id"] == connection_id]
            reports = []
            for connection in connections:
                reports.append(await self._sync_connection(connection, psu_headers))
            return reports

    # ------------------------------------------------------------------ one connection

    async def _sync_connection(self, connection: dict[str, Any], psu_headers: dict[str, str] | None) -> SyncReport:
        workspace_id = connection.get("workspace_id") or 0
        report = SyncReport(connection_id=connection["id"], workspace_id=workspace_id)
        if connection.get("status") != "active":
            report.status = "skipped"
            report.error = f"connection is {connection.get('status')}"
            return report
        secret = self.store.get_session(connection["session_ref"])
        if not secret or not secret.get("session_id"):
            report.status = "error"
            report.error = "session secret missing: connect the bank again"
            await self._record(connection["id"], report)
            return report

        client = self.client()
        psu_headers = psu_headers or None
        try:
            for link in connection.get("accounts") or []:
                if not link.get("enabled") or not link.get("account_id") or not link.get("bank_uid"):
                    continue
                account_report = AccountReport(link_id=link["id"], account_id=link["account_id"])
                report.accounts.append(account_report)
                try:
                    if connection.get("sync_balances", True):
                        await self._sync_balance(client, connection, link, account_report, psu_headers)
                    if connection.get("sync_transactions", True):
                        await self._sync_transactions(client, connection, link, account_report, psu_headers)
                except EnableBankingError as exc:
                    if exc.session_gone:
                        raise
                    account_report.error = f"{exc.code or exc.status}: {exc.message}"
                    if exc.rate_limited:
                        report.status = "rate_limited"
                    logger.warning("Bank sync: connection %s account link %s failed (%s)", connection["id"], link["id"], exc.code or exc.status)
            if report.status == "ok" and any(a.error for a in report.accounts):
                report.status = "partial"
        except EnableBankingError as exc:
            report.status = "expired" if exc.session_gone else ("rate_limited" if exc.rate_limited else "error")
            report.error = f"{exc.code or exc.status}: {exc.message}"
            logger.warning("Bank sync: connection %s stopped (%s)", connection["id"], exc.code or exc.status)
        except Exception as exc:
            report.status = "error"
            report.error = str(exc)[:500]
            logger.exception("Bank sync: connection %s failed", connection["id"])
        finally:
            await client.aclose()
        await self._record(connection["id"], report)
        return report

    async def _record(self, connection_id: int, report: SyncReport) -> None:
        changes: dict[str, Any] = {
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_sync_status": report.status,
            "last_error": report.error or "; ".join(f"{a.link_id}: {a.error}" for a in report.accounts if a.error) or None,
        }
        if report.status == "expired":
            changes["status"] = "expired"
        try:
            await self.api.request("PUT", f"/api/bank-connections/{connection_id}", json=changes)
        except Exception:
            logger.exception("Bank sync: could not record the outcome of connection %s", connection_id)

    async def _sync_balance(self, client, connection, link, report: AccountReport, psu_headers) -> None:
        picked = pick_balance(await client.balances(link["bank_uid"], psu_headers))
        if picked is None:
            return
        amount, ref_date, _kind = picked
        day = ref_date or self._today()
        day = min(day, self._today())
        await self.api.request(
            "POST", "/api/balance-snapshots/bulk",
            json={"snapshots": [{"account_id": link["account_id"], "date": day.isoformat(), "balance": amount}], "source": "bank"},
        )
        await self.api.request(
            "PUT", f"/api/bank-connections/{connection['id']}/links/{link['id']}",
            json={"last_balance": amount, "last_balance_date": day.isoformat()},
        )
        report.balance_recorded = True

    async def _sync_transactions(self, client, connection, link, report: AccountReport, psu_headers) -> None:
        today = self._today()
        if link.get("last_transaction_date"):
            start = date.fromisoformat(link["last_transaction_date"]) - timedelta(days=self.cfg.overlap_days)
        else:
            start = today - timedelta(days=self.cfg.initial_history_days)
        raw = await client.transactions(link["bank_uid"], date_from=start.isoformat(), date_to=today.isoformat(), psu_headers=psu_headers)
        rows = rows_from_transactions(raw, connection["aspsp_name"])
        report.transactions_fetched = len(rows)
        if not rows:
            return

        described = [r for r in rows if r.pop("has_description", True)]
        mute = [r for r in rows if r not in described]
        report.without_description = len(mute)
        for row in mute:
            row.pop("has_description", None)
            # a placeholder the next statement upload will take over (same amount and date, with the words)
            row.update(category="uncategorized", category_source="fallback", confidence=None, mute=True)
        if described:
            described = await classify_for_import(described, self.api, self.llm_factory())
        payload = [
            {k: v for k, v in row.items() if k in ("date", "amount", "description", "currency", "category", "category_source", "confidence", "external_id", "mute")}
            for row in described + mute
        ]
        imported = await self.api.request(
            "POST", "/api/imports",
            json={
                "account_id": link["account_id"],
                "transactions": payload,
                "filename": f"{connection['aspsp_name']} sync {today.isoformat()}",
                "source": "bank",
            },
        )
        report.transactions_added = imported.get("added_count", 0)
        report.transactions_skipped = imported.get("skipped_count", 0)
        report.suspect_duplicates = imported.get("suspect_duplicates", 0)
        latest = max(r["date"] for r in rows)
        await self.api.request(
            "PUT", f"/api/bank-connections/{connection['id']}/links/{link['id']}",
            json={"last_transaction_date": latest},
        )


# ---------------------------------------------------------------------- consent completion (shared by the routes)


class ConsentError(Exception):
    """The consent exists at the bank but could not be registered in the app."""


def _api_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        detail = exc.response.json().get("detail")
    except ValueError:
        detail = exc.response.text
    if isinstance(detail, list):  # pydantic validation errors: field path and message, never the input value
        return "; ".join(f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in detail)
    return str(detail)[:300]


async def session_accounts(client: EnableBankingClient, session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Account summaries of a session. ``POST /sessions`` returns full account objects, ``GET /sessions/{id}`` only
    their uids: in that case each account's details are fetched."""
    out: List[Dict[str, Any]] = []
    for item in session.get("accounts") or []:
        if isinstance(item, str):
            item = await client.account_details(item)
        if isinstance(item, dict):
            out.append(account_summary(item))
    return out


async def complete_consent(
    client: EnableBankingClient,
    store: SecretStore,
    api: FinanceApiClient,
    pending: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    """Exchange the authorization code for a session, store its id encrypted and register the connection in the API.
    Returns the API's connection record."""
    session = await client.create_session(code)
    ref = uuid.uuid4().hex
    # the secret is stored first: whatever happens next, the consent the user just gave is not lost
    store.put_session(ref, {"session_id": session["session_id"], "aspsp": session.get("aspsp"), "created": datetime.now(timezone.utc).isoformat()})
    accounts = await session_accounts(client, session)
    valid_until = (session.get("access") or {}).get("valid_until")
    body = {
        "provider": "enablebanking",
        "aspsp_name": pending["aspsp_name"],
        "aspsp_country": pending["country"],
        "psu_type": pending.get("psu_type") or "personal",
        "session_ref": ref,
        "valid_until": valid_until,
        "accounts": accounts,
        "renew_connection_id": pending.get("connection_id"),
    }
    with request_context(pending.get("workspace_id"), pending.get("user_id")):
        try:
            connection = await api.request("POST", "/api/bank-connections", json=body)
        except httpx.HTTPStatusError as exc:
            detail = _api_detail(exc)
            logger.error("Consent completed at the bank but the API refused the connection record (%s)", detail)
            raise ConsentError(f"The bank accepted the consent but the app could not save it: {detail}") from exc
    old_ref = pending.get("old_session_ref")
    if old_ref and old_ref != ref:
        store.delete_session(old_ref)
    logger.info("Bank connection %s registered with %d account(s)", connection["id"], len(accounts))
    return connection
