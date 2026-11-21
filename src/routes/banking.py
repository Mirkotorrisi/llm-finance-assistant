"""Bank connections: consent flow with Enable Banking, manual sync, forgetting a bank."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from src.api_client import get_api_client, request_context
from src.banking.config import BankingConfig
from src.banking.enable_banking import EnableBankingClient, EnableBankingError
from src.banking.scheduler import BankSyncScheduler
from src.banking.secrets import SecretStore
from src.banking.sync import BankSync, ConsentError, complete_consent, psu_headers_for
from src.llm import get_llm_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/banking", tags=["banking"])

_cfg: BankingConfig | None = None
_store: SecretStore | None = None
_scheduler: BankSyncScheduler | None = None


def config() -> BankingConfig:
    global _cfg
    if _cfg is None:
        _cfg = BankingConfig()
    return _cfg


def store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore(config().secrets_dir)
    return _store


def reset_banking() -> None:
    """Forget the cached config/store (tests change the environment)."""
    global _cfg, _store, _scheduler
    _cfg = None
    _store = None
    _scheduler = None


def new_client(cfg: BankingConfig | None = None) -> EnableBankingClient:
    cfg = cfg or config()
    if not cfg.configured():
        raise HTTPException(status_code=503, detail="Bank connections are not configured: set ENABLE_BANKING_APP_ID and the private key (see the Banks page)")
    return EnableBankingClient(cfg.app_id or "", cfg.private_key(), cfg.api_url)


async def make_sync() -> BankSync:
    return BankSync(config(), store(), await get_api_client(), get_llm_client, client_factory=lambda: new_client())


def scheduler() -> BankSyncScheduler:
    global _scheduler
    if _scheduler is None:
        cfg = config()

        def factory():
            # built lazily so the API client exists; the scheduler awaits sync_due on the returned object
            return _SyncProxy()

        _scheduler = BankSyncScheduler(factory, enabled=cfg.sync_mode == "auto" and cfg.configured())
    return _scheduler


class _SyncProxy:
    async def sync_due(self):
        sync = await make_sync()
        return await sync.sync_due()


def _bank_error(exc: EnableBankingError) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Bank API error {exc.code or exc.status}: {exc.message}")


# ---------------------------------------------------------------------- status and banks


@router.get("/status")
async def status() -> dict[str, Any]:
    cfg = config()
    return {**cfg.status(), "scheduler_running": scheduler()._task is not None}


@router.get("/aspsps")
async def aspsps(country: str | None = None, q: str | None = None) -> dict[str, Any]:
    client = new_client()
    try:
        banks = await client.aspsps(country=country.upper() if country else None, psu_type="personal")
    except EnableBankingError as exc:
        raise _bank_error(exc)
    finally:
        await client.aclose()
    out = []
    for bank in banks:
        if q and q.lower() not in str(bank.get("name", "")).lower():
            continue
        out.append({
            "name": bank.get("name"),
            "country": bank.get("country"),
            "logo": bank.get("logo"),
            "beta": bank.get("beta", False),
            "psu_types": bank.get("psu_types") or [],
            "max_consent_days": int((bank.get("maximum_consent_validity") or 0) // 86400) or None,
            "required_psu_headers": bank.get("required_psu_headers") or [],
        })
    return {"aspsps": sorted(out, key=lambda b: (b["country"] or "", b["name"] or ""))}


# ---------------------------------------------------------------------- consent


class ConnectRequest(BaseModel):
    aspsp_name: str = Field(..., max_length=120)
    country: str = Field(..., min_length=2, max_length=2)
    psu_type: str = Field(default="personal")
    connection_id: int | None = Field(default=None, description="Renew this existing connection (keeps its account mapping).")


@router.post("/connect")
async def connect(
    body: ConnectRequest,
    x_workspace_id: int | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Start the consent: returns the bank's authorization URL to open in the browser."""
    cfg = config()
    client = new_client(cfg)
    state = uuid.uuid4().hex
    try:
        banks = await client.aspsps(country=body.country.upper())
        bank = next((b for b in banks if str(b.get("name", "")).lower() == body.aspsp_name.lower()), None)
        max_seconds = (bank or {}).get("maximum_consent_validity")
        days = cfg.consent_days
        if max_seconds:
            days = min(days, max(1, int(max_seconds // 86400)))
        valid_until = datetime.now(UTC) + timedelta(days=days)
        old_ref = None
        if body.connection_id is not None:
            with request_context(x_workspace_id, x_user_id):
                api = await get_api_client()
                existing = await api.request("GET", "/api/bank-connections")
            current = next((c for c in existing if c["id"] == body.connection_id), None)
            if current is None:
                raise HTTPException(status_code=404, detail=f"Bank connection {body.connection_id} not found")
            old_ref = current["session_ref"]
        started = await client.start_auth(
            bank.get("name") if bank else body.aspsp_name, body.country.upper(), state, cfg.callback_url, valid_until,
            psu_type=body.psu_type, language=cfg.language,
        )
    except EnableBankingError as exc:
        raise _bank_error(exc)
    finally:
        await client.aclose()
    store().put_pending(state, {
        "workspace_id": x_workspace_id, "user_id": x_user_id, "aspsp_name": bank.get("name") if bank else body.aspsp_name,
        "country": body.country.upper(), "psu_type": body.psu_type, "connection_id": body.connection_id, "old_session_ref": old_ref,
    })
    return {"url": started["url"], "state": state, "valid_until": valid_until.isoformat(), "redirect_url": cfg.callback_url}


async def _finish(code: str, state: str) -> dict[str, Any]:
    pending = store().pop_pending(state)
    if pending is None:
        raise HTTPException(status_code=400, detail="Unknown or expired consent state: start the connection again")
    client = new_client()
    try:
        return await complete_consent(client, store(), await get_api_client(), pending, code)
    except EnableBankingError as exc:
        raise _bank_error(exc)
    except ConsentError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.aclose()


@router.get("/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None):
    """Where the bank sends the browser back. Completes the consent and returns to the app's Banks page."""
    ui = config().ui_url
    if error or not code or not state:
        reason = error_description or error or "missing code or state"
        return RedirectResponse(f"{ui}/banking?error={quote(reason)}", status_code=302)
    try:
        connection = await _finish(code, state)
    except HTTPException as exc:
        return RedirectResponse(f"{ui}/banking?error={quote(str(exc.detail))}", status_code=302)
    return RedirectResponse(f"{ui}/banking?connected={connection['id']}", status_code=302)


class PasteCallback(BaseModel):
    url: str | None = Field(default=None, description="The full redirect URL the browser landed on.")
    code: str | None = None
    state: str | None = None


@router.post("/callback")
async def paste_callback(body: PasteCallback) -> dict[str, Any]:
    """Fallback when the browser could not reach the local callback: the user pastes the URL (or code and state)."""
    code, state = body.code, body.state
    if body.url:
        query = parse_qs(urlparse(body.url).query)
        code = code or (query.get("code") or [None])[0]
        state = state or (query.get("state") or [None])[0]
        if query.get("error"):
            raise HTTPException(status_code=400, detail=(query.get("error_description") or query.get("error"))[0])
    if not code or not state:
        raise HTTPException(status_code=400, detail="The URL has no code and state")
    return await _finish(code, state)


# ---------------------------------------------------------------------- sync and removal


class SyncRequest(BaseModel):
    connection_id: int | None = None


@router.post("/sync")
async def sync_now(
    body: SyncRequest,
    request: Request,
    x_workspace_id: int | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Sync the current project's connections (or one) now. Runs with the user present."""
    if not config().configured():
        raise HTTPException(status_code=503, detail="Bank connections are not configured")
    async with scheduler().lock:
        sync = await make_sync()
        reports = await sync.sync_workspace(x_workspace_id, x_user_id, body.connection_id, psu_headers=await psu_headers_for(config(), user_present=True))
    return {"reports": [r.to_dict() for r in reports]}


@router.delete("/connections/{connection_id}")
async def forget(connection_id: int, x_workspace_id: int | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Revoke the consent at the bank (best effort), delete the secret and the API record."""
    with request_context(x_workspace_id, x_user_id):
        api = await get_api_client()
        connections = await api.request("GET", "/api/bank-connections")
        current = next((c for c in connections if c["id"] == connection_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail=f"Bank connection {connection_id} not found")
        secret = store().get_session(current["session_ref"])
        if secret and secret.get("session_id") and config().configured():
            client = new_client()
            try:
                await client.delete_session(secret["session_id"])
            except EnableBankingError as exc:
                logger.warning("Could not revoke session of connection %s at the bank (%s)", connection_id, exc.code or exc.status)
            finally:
                await client.aclose()
        store().delete_session(current["session_ref"])
        await api.request("DELETE", f"/api/bank-connections/{connection_id}")
    return {"message": "Bank connection removed"}
