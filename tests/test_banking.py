"""Bank connector: payload mapping, encrypted secrets, the Enable Banking client, the sync and the consent flow.

The bank is a fake HTTP transport, the finance API an in-memory fake: nothing leaves the machine.
"""

import json
import logging
import os
import stat
from datetime import date

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from src.api_client import scope_headers
from src.banking.config import BankingConfig
from src.banking.enable_banking import EnableBankingClient, EnableBankingError, make_jwt
from src.banking.mapping import (
    account_summary,
    describe,
    mask_iban,
    pick_balance,
    rows_from_transactions,
    transaction_to_row,
)
from src.banking.secrets import SecretStore
from src.banking.sync import BankSync


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem, public


# ── mapping ─────────────────────────────────────────────────────────────────────

TX_POS = {
    "entry_reference": "2026112100123",
    "transaction_amount": {"currency": "EUR", "amount": "45.00"},
    "creditor": {"name": "ESSELUNGA SPA"},
    "credit_debit_indicator": "DBIT",
    "status": "BOOK",
    "booking_date": "2026-11-20",
    "value_date": "2026-11-21",
    "remittance_information": ["PAGAMENTO POS CARTA 1234"],
}
TX_SALARY = {
    "transaction_amount": {"currency": "EUR", "amount": "1500.00"},
    "debtor": {"name": "ACME SRL"},
    "credit_debit_indicator": "CRDT",
    "status": "BOOK",
    "booking_date": "2026-11-02",
    "remittance_information": ["Stipendio ottobre ACME SRL"],
    "reference_number": "RF123",
}
TX_MUTE = {  # Trade Republic style: amount, currency and date only
    "transaction_amount": {"currency": "EUR", "amount": "200.00"},
    "credit_debit_indicator": "DBIT",
    "status": "BOOK",
    "booking_date": "2026-11-15",
}
TX_PENDING = {**TX_POS, "status": "PDNG", "entry_reference": "P1"}


def test_rows_sign_description_and_reference():
    pos = transaction_to_row(TX_POS, "UniCredit")
    assert pos == {
        "date": "2026-11-20", "amount": -45.0, "description": "ESSELUNGA SPA PAGAMENTO POS CARTA 1234",
        "currency": "EUR", "external_id": "2026112100123", "has_description": True,
    }
    salary = transaction_to_row(TX_SALARY, "UniCredit")
    assert salary["amount"] == 1500.0 and salary["external_id"] == "RF123"
    assert salary["description"] == "Stipendio ottobre ACME SRL"  # the counterparty is already in the text
    mute = transaction_to_row(TX_MUTE, "Trade Republic")
    assert mute["description"] == "Movimento Trade Republic" and mute["has_description"] is False
    assert mute["external_id"] is None  # no stable reference: the fingerprint multiset dedupes instead
    assert transaction_to_row(TX_PENDING, "x") is None
    assert len(rows_from_transactions([TX_POS, TX_PENDING, TX_SALARY], "x")) == 2


def test_describe_falls_back_to_the_bank_code():
    text, has = describe({"bank_transaction_code": {"description": "Canone mensile"}, "credit_debit_indicator": "DBIT"}, "Banca")
    assert text == "Canone mensile" and has is True


def test_pick_balance_prefers_closing_booked_and_masks_ibans():
    balances = [
        {"balance_type": "ITAV", "balance_amount": {"currency": "EUR", "amount": "900.00"}},
        {"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "1000.50"}, "reference_date": "2026-11-20"},
    ]
    assert pick_balance(balances) == (1000.5, date(2026, 11, 20), "CLBD")
    assert pick_balance([{"balance_type": "OTHR", "balance_amount": {"amount": "abc"}}]) is None
    assert mask_iban("IT60 X054 2811 1010 0000 0123 456") == "IT60 •••• 3456"
    summary = account_summary({
        "uid": "u1", "identification_hash": "h1", "account_id": {"iban": "IT60X0542811101000000123456"},
        "product": "Conto MyGenius", "currency": "EUR", "cash_account_type": "CACC",
    })
    assert summary == {"identification_hash": "h1", "bank_uid": "u1", "iban_masked": "IT60 •••• 3456", "label": "Conto MyGenius", "currency": "EUR", "cash_account_type": "CACC"}


# ── secrets ─────────────────────────────────────────────────────────────────────


def test_secret_store_encrypts_at_rest_with_private_permissions(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.put_session("ref1", {"session_id": "sess-123"})
    store.put_pending("state1", {"workspace_id": 1})
    assert store.get_session("ref1") == {"session_id": "sess-123"}
    raw = (tmp_path / "secrets" / "banking.enc").read_bytes()
    assert b"sess-123" not in raw and b"session_id" not in raw
    assert stat.S_IMODE(os.stat(tmp_path / "secrets" / "store.key").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "secrets").st_mode) == 0o700
    pending = store.pop_pending("state1")
    assert pending["workspace_id"] == 1 and store.pop_pending("state1") is None
    store.delete_session("ref1")
    assert store.get_session("ref1") is None


# ── client ──────────────────────────────────────────────────────────────────────


def test_jwt_is_rs256_with_the_application_id_as_kid(rsa_key):
    pem, public = rsa_key
    token = make_jwt("app-1", pem, now=1_700_000_000)
    assert jwt.get_unverified_header(token) == {"alg": "RS256", "kid": "app-1", "typ": "JWT"}
    claims = jwt.decode(token, public, algorithms=["RS256"], audience="api.enablebanking.com", options={"verify_exp": False})
    assert claims == {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": 1_700_000_000, "exp": 1_700_003_600}


class FakeBank:
    """Enable Banking behind httpx.MockTransport: scripted accounts, balances, paged transactions and errors."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.balances = {"u1": [{"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "1000.50"}, "reference_date": "2026-11-20"}]}
        self.transactions = {"u1": [TX_POS, TX_SALARY, TX_PENDING]}
        self.page_size = 2
        self.fail_with: dict | None = None  # {"status": 401, "error": "EXPIRED_SESSION", "message": "..."}
        self.sessions_created: list[str] = []
        self.deleted_sessions: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["Authorization"].startswith("Bearer ")
        path = request.url.path
        if self.fail_with and path.startswith("/accounts/"):
            f = self.fail_with
            return httpx.Response(f["status"], json={"message": f["message"], "error": f["error"], "code": f["status"]})
        if path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "UniCredit", "country": "IT", "logo": "x", "psu_types": ["personal"], "auth_methods": [], "maximum_consent_validity": 15552000, "beta": False},
                {"name": "Revolut", "country": "LT", "logo": "x", "psu_types": ["personal", "business"], "auth_methods": [], "maximum_consent_validity": 7776000, "beta": False},
            ]})
        if path == "/auth":
            body = json.loads(request.content)
            return httpx.Response(200, json={"url": f"https://bank.example/auth?state={body['state']}", "authorization_id": "auth-1", "psu_id_hash": "p"})
        if path == "/sessions" and request.method == "POST":
            code = json.loads(request.content)["code"]
            self.sessions_created.append(code)
            return httpx.Response(200, json={
                "session_id": "sess-" + code, "aspsp": {"name": "UniCredit", "country": "IT"}, "psu_type": "personal",
                "access": {"valid_until": "2027-05-20T10:00:00.000Z", "balances": True, "transactions": True},
                "accounts": [
                    {"uid": "u1", "identification_hash": "h1", "account_id": {"iban": "IT60X0542811101000000123456"}, "product": "Conto MyGenius", "currency": "EUR", "cash_account_type": "CACC"},
                    {"uid": "u2", "identification_hash": "h2", "account_id": {"iban": "IT60X0542811101000000999999"}, "product": "Genius Card", "currency": "EUR", "cash_account_type": "CARD"},
                ],
            })
        if path.startswith("/sessions/") and request.method == "DELETE":
            self.deleted_sessions.append(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if path.endswith("/balances"):
            uid = path.split("/")[2]
            return httpx.Response(200, json={"balances": self.balances.get(uid, [])})
        if path.endswith("/transactions"):
            uid = path.split("/")[2]
            rows = self.transactions.get(uid, [])
            start = int(request.url.params.get("continuation_key") or 0)
            page = rows[start:start + self.page_size]
            nxt = start + self.page_size
            return httpx.Response(200, json={"transactions": page, "continuation_key": str(nxt) if nxt < len(rows) else None})
        return httpx.Response(404, json={"message": "no route", "error": "NOT_FOUND"})


async def test_client_pages_transactions_and_maps_errors(rsa_key):
    bank = FakeBank()
    client = EnableBankingClient("app-1", rsa_key[0], transport=httpx.MockTransport(bank.handler))
    rows = await client.transactions("u1", date_from="2026-10-01", date_to="2026-11-21")
    assert len(rows) == 3
    tx_calls = [c for c in bank.calls if c.url.path.endswith("/transactions")]
    assert len(tx_calls) == 2 and tx_calls[1].url.params["continuation_key"] == "2"
    assert tx_calls[0].url.params["date_from"] == "2026-10-01"
    bank.fail_with = {"status": 401, "error": "EXPIRED_SESSION", "message": "Session is expired"}
    with pytest.raises(EnableBankingError) as exc:
        await client.balances("u1")
    assert exc.value.session_gone and not exc.value.rate_limited
    await client.aclose()


# ── sync against a fake API ──────────────────────────────────────────────────────


class FakeApi:
    """The finance API endpoints the bank sync and the consent flow use, kept in memory."""

    def __init__(self):
        self.connections: dict[int, dict] = {}
        self.snapshots: list[dict] = []
        self.imports: list[dict] = []
        self.scopes: list[dict] = []
        self.next_id = 1
        self.import_result = {"added_count": 2, "skipped_count": 1, "suspect_duplicates": 1}

    def add_connection(self, **overrides):
        cid = self.next_id
        self.next_id += 1
        conn = {
            "id": cid, "workspace_id": 1, "aspsp_name": "UniCredit", "aspsp_country": "IT", "session_ref": "ref1", "status": "active",
            "sync_balances": True, "sync_transactions": True, "valid_until": "2027-05-20T10:00:00",
            "accounts": [
                {"id": 10, "bank_uid": "u1", "identification_hash": "h1", "account_id": 5, "enabled": True, "last_transaction_date": None},
                {"id": 11, "bank_uid": "u2", "identification_hash": "h2", "account_id": None, "enabled": True, "last_transaction_date": None},
            ],
        }
        conn.update(overrides)
        self.connections[cid] = conn
        return conn

    async def request(self, method, path, json=None, params=None):
        self.scopes.append(scope_headers())
        if path == "/api/bank-connections/due":
            return [{"workspace_id": c["workspace_id"], "connection_id": c["id"]} for c in self.connections.values() if c["status"] == "active"]
        if path == "/api/bank-connections" and method == "GET":
            return list(self.connections.values())
        if path == "/api/bank-connections" and method == "POST":
            cid = json.get("renew_connection_id") or self.next_id
            if not json.get("renew_connection_id"):
                self.next_id += 1
            conn = {**json, "id": cid, "status": "active", "accounts": [{**a, "id": 100 + i, "account_id": None} for i, a in enumerate(json["accounts"])]}
            self.connections[cid] = conn
            return conn
        if path.startswith("/api/bank-connections/") and "/links/" in path and method == "PUT":
            cid, lid = int(path.split("/")[3]), int(path.split("/")[5])
            link = next(l for l in self.connections[cid]["accounts"] if l["id"] == lid)
            link.update(json)
            return link
        if path.startswith("/api/bank-connections/") and method == "PUT":
            cid = int(path.rsplit("/", 1)[1])
            self.connections[cid].update(json)
            return self.connections[cid]
        if path.startswith("/api/bank-connections/") and method == "DELETE":
            self.connections.pop(int(path.rsplit("/", 1)[1]))
            return {"message": "ok"}
        if path == "/api/balance-snapshots/bulk":
            self.snapshots.append(json)
            return {"saved": 1}
        if path == "/api/imports":
            self.imports.append(json)
            return self.import_result
        raise AssertionError(f"unexpected API call {method} {path}")

    # the classification step uses call_tool
    async def call_tool(self, name, args):
        if name == "resolve_merchants":
            return [{"description": d, "category": "spesa" if "ESSELUNGA" in d else None} for d in args["descriptions"]]
        if name == "get_blueprint":
            return {"blueprint": {"categories": [{"key": "spesa", "label": "Spesa"}, {"key": "stipendio", "label": "Stipendio", "kind": "income"}]}}
        raise AssertionError(name)


class StubLLM:
    """Classifies everything it is asked as 'stipendio' with middling confidence; records what it saw."""

    def __init__(self):
        self.seen: list[str] = []

    async def complete_json(self, *args, **kwargs):  # the classifier's entry point varies: patched below
        raise NotImplementedError


@pytest.fixture
def patched_classifier(monkeypatch):
    seen = []

    async def fake_classify(rows, api, llm):
        resolved = await api.call_tool("resolve_merchants", {"descriptions": [r["description"] for r in rows]})
        out = []
        for row, res in zip(rows, resolved):
            seen.append(row["description"])
            if res["category"]:
                out.append({**row, "category": res["category"], "category_source": "rule", "confidence": None})
            else:
                out.append({**row, "category": "stipendio", "category_source": "ai", "confidence": 0.7})
        return out

    import src.banking.sync as sync_module

    monkeypatch.setattr(sync_module, "classify_for_import", fake_classify)
    return seen


def make_sync(tmp_path, api, bank, rsa_key, today=date(2026, 11, 21)):
    cfg = BankingConfig(app_id="app-1", key_path=tmp_path / "k.pem", secrets_dir=tmp_path / "secrets")
    store = SecretStore(cfg.secrets_dir)
    store.put_session("ref1", {"session_id": "sess-1"})
    sync = BankSync(cfg, store, api, lambda: None, client_factory=lambda: EnableBankingClient("app-1", rsa_key[0], transport=httpx.MockTransport(bank.handler)), today=lambda: today)
    return sync, store


async def test_sync_records_balance_and_imports_booked_rows_with_scope(tmp_path, rsa_key, patched_classifier, caplog):
    api, bank = FakeApi(), FakeBank()
    api.add_connection()
    sync, _ = make_sync(tmp_path, api, bank, rsa_key)
    with caplog.at_level(logging.INFO):
        reports = await sync.sync_due()
    assert len(reports) == 1 and reports[0].status == "ok"
    acc = reports[0].accounts
    assert len(acc) == 1 and acc[0].link_id == 10  # the unmapped card is skipped
    assert acc[0].balance_recorded and acc[0].transactions_fetched == 2 and acc[0].transactions_added == 2 and acc[0].suspect_duplicates == 1

    assert api.snapshots == [{"snapshots": [{"account_id": 5, "date": "2026-11-20", "balance": 1000.5}], "source": "bank"}]
    imported = api.imports[0]
    assert imported["account_id"] == 5 and imported["source"] == "bank" and imported["filename"] == "UniCredit sync 2026-11-21"
    by_ext = {r["external_id"]: r for r in imported["transactions"]}
    assert by_ext["2026112100123"]["category"] == "spesa" and by_ext["2026112100123"]["category_source"] == "rule"
    assert by_ext["RF123"]["category_source"] == "ai" and "has_description" not in by_ext["RF123"]
    # first sync asks for 89 days of history
    tx_call = next(c for c in bank.calls if c.url.path.endswith("/transactions"))
    assert tx_call.url.params["date_from"] == "2026-08-24" and tx_call.url.params["date_to"] == "2026-11-21"
    link = api.connections[1]["accounts"][0]
    assert link["last_transaction_date"] == "2026-11-20" and link["last_balance"] == 1000.5
    assert api.connections[1]["last_sync_status"] == "ok" and api.connections[1]["last_sync_at"]
    # every API call was made in the connection's workspace
    assert all(s.get("X-Workspace-Id") == "1" for s in api.scopes[1:])
    # nothing sensitive in the logs
    assert "sess-1" not in caplog.text and "IT60" not in caplog.text and "1000.5" not in caplog.text


async def test_mute_rows_skip_the_model_and_the_next_sync_overlaps(tmp_path, rsa_key, patched_classifier):
    api, bank = FakeApi(), FakeBank()
    api.add_connection(aspsp_name="Trade Republic", aspsp_country="DE")
    api.connections[1]["accounts"][0]["last_transaction_date"] = "2026-11-10"
    bank.transactions["u1"] = [TX_MUTE, TX_POS]
    sync, _ = make_sync(tmp_path, api, bank, rsa_key)
    reports = await sync.sync_workspace(1, "user-1", 1)
    assert reports[0].accounts[0].without_description == 1
    assert patched_classifier == ["ESSELUNGA SPA PAGAMENTO POS CARTA 1234"]  # the mute row never reached the model
    rows = {r["description"]: r for r in api.imports[0]["transactions"]}
    assert rows["Movimento Trade Republic"]["category"] == "uncategorized" and rows["Movimento Trade Republic"]["category_source"] == "fallback"
    tx_call = next(c for c in bank.calls if c.url.path.endswith("/transactions"))
    assert tx_call.url.params["date_from"] == "2026-11-03"  # last known minus 7 days


async def test_expired_session_marks_the_connection_and_rate_limits_are_reported(tmp_path, rsa_key, patched_classifier):
    api, bank = FakeApi(), FakeBank()
    api.add_connection()
    bank.fail_with = {"status": 401, "error": "EXPIRED_SESSION", "message": "Session is expired"}
    sync, _ = make_sync(tmp_path, api, bank, rsa_key)
    reports = await sync.sync_due()
    assert reports[0].status == "expired" and api.connections[1]["status"] == "expired"
    assert api.snapshots == [] and api.imports == []

    api2, bank2 = FakeApi(), FakeBank()
    api2.add_connection()
    bank2.fail_with = {"status": 429, "error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "ASPSP Rate limit exceeded"}
    sync2, _ = make_sync(tmp_path / "b", api2, bank2, rsa_key)
    reports2 = await sync2.sync_due()
    assert reports2[0].status == "rate_limited" and api2.connections[1]["status"] == "active"
    assert api2.connections[1]["last_sync_status"] == "rate_limited"


async def test_missing_secret_is_an_error_not_a_crash(tmp_path, rsa_key):
    api, bank = FakeApi(), FakeBank()
    api.add_connection(session_ref="unknown")
    sync, _ = make_sync(tmp_path, api, bank, rsa_key)
    reports = await sync.sync_due()
    assert reports[0].status == "error" and "connect the bank again" in reports[0].error
    assert api.connections[1]["last_sync_status"] == "error"


# ── routes: consent flow ─────────────────────────────────────────────────────────


@pytest.fixture
def banking_app(tmp_path, monkeypatch, rsa_key):
    import src.routes.banking as routes
    from app import app

    key_path = tmp_path / "eb.pem"
    key_path.write_bytes(rsa_key[0])
    monkeypatch.setenv("ENABLE_BANKING_APP_ID", "app-1")
    monkeypatch.setenv("ENABLE_BANKING_KEY_PATH", str(key_path))
    monkeypatch.setenv("FINANCE_UI_URL", "http://ui.test")
    monkeypatch.setenv("FINANCE_GATEWAY_URL", "http://gw.test")
    routes.reset_banking()
    bank = FakeBank()
    api = FakeApi()
    monkeypatch.setattr(routes, "new_client", lambda cfg=None: EnableBankingClient("app-1", rsa_key[0], transport=httpx.MockTransport(bank.handler)))

    async def fake_api_client():
        return api

    monkeypatch.setattr(routes, "get_api_client", fake_api_client)
    with TestClient(app) as client:
        yield client, bank, api, routes


def test_status_reports_configuration(banking_app):
    client, _, _, _ = banking_app
    body = client.get("/banking/status").json()
    assert body["configured"] is True and body["redirect_url"] == "http://gw.test/banking/callback" and body["scheduler_running"] is False


def test_unconfigured_gateway_says_so(monkeypatch):
    import src.routes.banking as routes
    from app import app

    routes.reset_banking()
    with TestClient(app) as client:
        assert client.get("/banking/status").json()["configured"] is False
        assert client.get("/banking/aspsps", params={"country": "IT"}).status_code == 503
        assert client.post("/banking/sync", json={}).status_code == 503


def test_consent_flow_from_connect_to_callback(banking_app, caplog):
    client, bank, api, routes = banking_app
    banks = client.get("/banking/aspsps", params={"country": "IT", "q": "uni"}).json()["aspsps"]
    assert banks == [{"name": "UniCredit", "country": "IT", "logo": "x", "beta": False, "psu_types": ["personal"], "max_consent_days": 180, "required_psu_headers": []}]

    started = client.post("/banking/connect", json={"aspsp_name": "unicredit", "country": "it"}, headers={"X-Workspace-Id": "7", "X-User-Id": "u-1"})
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    assert started.json()["url"] == f"https://bank.example/auth?state={state}"
    auth_body = json.loads(next(c for c in bank.calls if c.url.path == "/auth").content)
    assert auth_body["redirect_url"] == "http://gw.test/banking/callback" and auth_body["aspsp"] == {"name": "UniCredit", "country": "IT"}
    assert auth_body["access"]["valid_until"].startswith("2027-05-") or auth_body["access"]["valid_until"] > "2027"

    with caplog.at_level(logging.INFO):
        done = client.get("/banking/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == "http://ui.test/banking?connected=1"
    conn = api.connections[1]
    assert conn["aspsp_name"] == "UniCredit" and conn["valid_until"] == "2027-05-20T10:00:00.000Z"
    assert [a["iban_masked"] for a in conn["accounts"]] == ["IT60 •••• 3456", "IT60 •••• 9999"]
    assert api.scopes[-1] == {"X-Workspace-Id": "7", "X-User-Id": "u-1"}
    secret = routes.store().get_session(conn["session_ref"])
    assert secret["session_id"] == "sess-c0de"
    assert "sess-c0de" not in caplog.text and "IT60X054" not in caplog.text

    # the state is single-use
    again = client.get("/banking/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
    assert again.status_code == 302 and "error=" in again.headers["location"]

    # the bank reported an error: back to the app with the reason
    failed = client.get("/banking/callback", params={"error": "access_denied", "error_description": "User cancelled"}, follow_redirects=False)
    assert failed.headers["location"] == "http://ui.test/banking?error=User%20cancelled"


def test_paste_callback_and_renewal_replace_the_secret(banking_app):
    client, bank, api, routes = banking_app
    first = client.post("/banking/connect", json={"aspsp_name": "UniCredit", "country": "IT"}, headers={"X-Workspace-Id": "1"}).json()
    conn = client.post("/banking/callback", json={"url": f"http://gw.test/banking/callback?code=one&state={first['state']}"}).json()
    old_ref = conn["session_ref"]

    renew = client.post("/banking/connect", json={"aspsp_name": "UniCredit", "country": "IT", "connection_id": conn["id"]}, headers={"X-Workspace-Id": "1"}).json()
    renewed = client.post("/banking/callback", json={"code": "two", "state": renew["state"]}).json()
    assert renewed["id"] == conn["id"] and renewed["session_ref"] != old_ref
    assert routes.store().get_session(old_ref) is None and routes.store().get_session(renewed["session_ref"])["session_id"] == "sess-two"
    assert client.post("/banking/callback", json={"url": "http://gw.test/banking/callback?nothing=1"}).status_code == 400


def test_forget_revokes_at_the_bank_and_deletes_the_secret(banking_app):
    client, bank, api, routes = banking_app
    started = client.post("/banking/connect", json={"aspsp_name": "UniCredit", "country": "IT"}, headers={"X-Workspace-Id": "1"}).json()
    conn = client.post("/banking/callback", json={"code": "zz", "state": started["state"]}).json()
    gone = client.delete(f"/banking/connections/{conn['id']}", headers={"X-Workspace-Id": "1"})
    assert gone.status_code == 200
    assert bank.deleted_sessions == ["sess-zz"] and conn["id"] not in api.connections
    assert routes.store().get_session(conn["session_ref"]) is None


# ── access log redaction ─────────────────────────────────────────────────────────


def test_callback_codes_are_blanked_in_access_logs(caplog):
    from src.banking.logging import RedactCallbackFilter, redact_query

    assert redact_query('GET /banking/callback?code=abc123&state=s9 HTTP/1.1') == 'GET /banking/callback?code=***&state=*** HTTP/1.1'
    assert redact_query("/api/transactions?category=code") == "/api/transactions?category=code"
    logger = logging.getLogger("uvicorn.access")
    logger.addFilter(RedactCallbackFilter())
    try:
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/banking/callback?code=SECRET&state=ST", "1.1", 302)
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/banking/status?code=keep", "1.1", 200)
    finally:
        logger.filters.clear()
    assert "SECRET" not in caplog.text and "code=***&state=***" in caplog.text
    assert "code=keep" in caplog.text  # only the callback path is touched


def test_callback_redaction_end_to_end(banking_app, caplog):
    client, bank, api, routes = banking_app
    from src.banking.logging import install

    install()
    started = client.post("/banking/connect", json={"aspsp_name": "UniCredit", "country": "IT"}, headers={"X-Workspace-Id": "1"}).json()
    with caplog.at_level(logging.INFO):
        client.get("/banking/callback", params={"code": "VERYSECRET", "state": started["state"]}, follow_redirects=False)
    assert "VERYSECRET" not in caplog.text


async def test_session_accounts_fetches_details_when_only_uids_are_given(rsa_key):
    from src.banking.sync import session_accounts

    bank = FakeBank()
    details = {"uid": "u1", "identification_hash": "h" * 133, "account_id": {"iban": "IT60X0542811101000000123456"}, "name": "ROSSI MARIO", "currency": "EUR", "cash_account_type": "CACC"}

    def handler(request):
        if request.url.path == "/accounts/u1/details":
            return httpx.Response(200, json=details)
        return bank.handler(request)

    client = EnableBankingClient("app-1", rsa_key[0], transport=httpx.MockTransport(handler))
    out = await session_accounts(client, {"accounts": ["u1"]})
    await client.aclose()
    assert out == [{"identification_hash": "h" * 133, "bank_uid": "u1", "iban_masked": "IT60 •••• 3456", "label": "ROSSI MARIO", "currency": "EUR", "cash_account_type": "CACC"}]


def test_api_refusal_after_consent_keeps_the_secret_and_explains(banking_app):
    client, bank, api, routes = banking_app

    async def refusing(method, path, json=None, params=None):
        if path == "/api/bank-connections" and method == "POST":
            request = httpx.Request("POST", "http://api.test/api/bank-connections")
            response = httpx.Response(422, json={"detail": [{"loc": ["body", "accounts", 0, "identification_hash"], "msg": "String should have at most 128 characters", "input": "SECRET-HASH"}]}, request=request)
            raise httpx.HTTPStatusError("422", request=request, response=response)
        return await FakeApi.request(api, method, path, json, params)

    api.request = refusing
    started = client.post("/banking/connect", json={"aspsp_name": "UniCredit", "country": "IT"}, headers={"X-Workspace-Id": "1"}).json()
    done = client.get("/banking/callback", params={"code": "c1", "state": started["state"]}, follow_redirects=False)
    assert done.status_code == 302 and "error=" in done.headers["location"]
    assert "identification_hash" in done.headers["location"] and "SECRET-HASH" not in done.headers["location"]
    # the session the bank granted is still in the store, so it can be recovered without a new consent
    stored = routes.store()._load()["sessions"]
    assert any(v["session_id"] == "sess-c1" for v in stored.values())


async def test_psu_headers_follow_the_policy(monkeypatch):
    from src.banking import sync as sync_module
    from src.banking.sync import psu_headers_for

    assert await psu_headers_for(BankingConfig(psu_ip="off"), user_present=True) == {}
    assert await psu_headers_for(BankingConfig(psu_ip="auto"), user_present=False) == {}
    assert await psu_headers_for(BankingConfig(psu_ip="203.0.113.9"), user_present=True) == {"Psu-Ip-Address": "203.0.113.9"}
    monkeypatch.setitem(sync_module._PUBLIC_IP_CACHE, "ip", "198.51.100.7")
    monkeypatch.setitem(sync_module._PUBLIC_IP_CACHE, "at", 10**12)
    assert await psu_headers_for(BankingConfig(psu_ip="auto"), user_present=True) == {"Psu-Ip-Address": "198.51.100.7"}
