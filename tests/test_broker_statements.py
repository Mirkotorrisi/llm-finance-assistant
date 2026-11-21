"""Known statement layouts read with rules: Revolut's savings deposit and its Robo/trading account statement."""

import io
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app import app
from src.services.broker_statements import detect, parse_revolut_savings, parse_revolut_trading
from src.services.file_processor import FileProcessor

SAVINGS = """Deposito senza vincoli
Generato in data 21 nov 2026
Periodo: 26 set 2025 - 21 nov 2026
Riepilogo del saldo
Saldo iniziale 0,00€
Totale depositato 20.000,00€
Saldo finale 10.024,74€
Transazioni da 26 settembre 2025 a 21 novembre 2026
Data Descrizione Tasso di interesse 
lordo guadagnato Entrate Uscite Saldo
26 set 
2025Deposito sul conto "Conto deposito senza vincoli"20.000,00
€20.000,00
€
27 set 
2025Interessi netti pagati nel conto "Conto deposito 
senza vincoli" in data 27 set 20251.50% 0,61€20.000,61
€
Pagina  di 1 30
Data Descrizione Tasso di interesse 
lordo guadagnato Entrate Uscite Saldo
26 feb 
2026 Prelievo dal conto "Conto deposito senza vincoli" 5.000,00€
15.000,61
€
26 feb 
2026 Prelievo dal conto "Conto deposito senza vincoli" 5.000,00€
10.000,61
€
21 nov 
2026
Interessi netti pagati nel conto "Conto deposito 
senza vincoli" in data 21 nov 2026 1.40% 0,28€ 10.024,74€
Interessi
Il tasso di interesse è il rendimento che guadagni"""

TRADING = """Account Statement
Generated on the 21 Nov 2026
Period 12 Aug 2024 - 21 Nov 2026
USD Account summary
Starting Ending
Positions Value US$0 US$0
Cash value* US$0 US$0
USD Portfolio breakdown
Symbol Company ISIN Quantity Price Value % of Portfolio
USD Transactions
Date Symbol Type Quantity Price Side Value Fees Commission
EUR Account summary
Starting Ending
Positions Value €0 €5,589.49
Cash value* €0 €55.03
Total €0 €5,644.52
EUR Portfolio breakdown
Symbol Company ISIN Quantity Price Value % of Portfolio
XDWI Xtrackers MSCI World Industrials UCITS ETF (Acc) IE00BM67HV82 2.42477636 €69.70 €169.01 2.99%
PRAC Amundi EUR Corporate Bond UCITS ETF DR – EUR (Acc) LU2089238625 20.77233051 €20.04 €416.35 7.38%
Positions Value €585.36 99.03%
Cash value €55.03 0.97%
EUR Transactions
Date Symbol Type Quantity Price Side Value Fees Commission
12 Aug 2024 06:41:54 GMT Cash top-up €1,000 €0 €0
12 Aug 2024 09:30:02 GMT QDVY Trade - Market 4.29830217 €4.65 Buy €20 €0 €0
12 Sep 2024 00:27:50 GMT Robo management fee -€0.69 €0 €0
27 Sep 2024 15:17:55 GMT IUSU Dividend €6.37 €0 €0
01 Apr 2026 12:00:51 GMT IS3Q Trade - Market 9.57492113 €68.01 Sell €651.19 €0 €0
10 Nov 2026 15:30:47 GMT XDWI Trade - Market 2.42477636 €73.78 Buy €178.90 €0 €0
Glossary
Revolut Securities Europe UAB LEI: 9845001DE7E84FF54124"""


def test_savings_statement_rows_and_closing_balance():
    assert detect(SAVINGS) == "revolut_savings"
    rows, meta = parse_revolut_savings(SAVINGS)
    assert [(r["date"], r["amount"], r["category"]) for r in rows] == [
        ("2025-09-26", 20000.0, "transfer"), ("2025-09-27", 0.61, "interest"),
        ("2026-02-26", -5000.0, "transfer"), ("2026-02-26", -5000.0, "transfer"), ("2026-11-21", 0.28, "interest"),
    ]
    assert rows[1]["description"] == "Interessi netti conto deposito Revolut (1.50%)"
    assert meta == {"kind": "revolut_savings", "closing_balance": 10024.74, "closing_date": "2026-11-21", "holdings": []}


def test_trading_statement_rows_holdings_and_values():
    assert detect(TRADING) == "revolut_trading"
    rows, meta = parse_revolut_trading(TRADING)
    kinds = Counter(r["category"] for r in rows)
    assert kinds == {"transfer": 1, "investments": 4, "bank_fees": 1}
    by_date = {(r["date"], r["amount"]): r for r in rows}
    buy = by_date[("2026-11-10", -178.9)]
    assert buy["description"] == "Buy trade IE00BM67HV82 XDWI Xtrackers MSCI World Industrials UCITS ETF (Acc), quantity: 2.42477636, price: 73.78"
    assert by_date[("2026-04-01", 651.19)]["description"].startswith("Sell trade IS3Q, quantity: 9.57492113")
    assert by_date[("2024-09-27", 6.37)]["description"] == "Cash Dividend IUSU"
    assert by_date[("2024-09-12", -0.69)]["category"] == "bank_fees"
    assert all(r["currency"] == "EUR" for r in rows)
    assert meta["closing_date"] == "2026-11-21" and meta["cash_value"] == 55.03
    assert [h["isin"] for h in meta["holdings"]] == ["IE00BM67HV82", "LU2089238625"]
    assert meta["holdings"][1]["asset_class"] == "bonds" and meta["holdings"][0]["price"] == 69.7
    assert meta["closing_balance"] == round(169.01 + 416.35 + 55.03, 2)


class FakePdfReader:
    def __init__(self, text):
        page = type("Page", (), {"extract_text": lambda self_: text})()
        self.pages = [page]


def test_known_pdf_layouts_bypass_the_model(monkeypatch):
    import src.services.file_processor as fp

    monkeypatch.setattr(fp, "PdfReader", lambda content: FakePdfReader(SAVINGS))
    rows = FileProcessor.extract_from_pdf(io.BytesIO(b"%PDF"))
    assert "pdf_text" not in rows[0] and rows[0]["__meta__"]["closing_balance"] == 10024.74 and len(rows) == 5
    monkeypatch.setattr(fp, "PdfReader", lambda content: FakePdfReader("Estratto conto generico\nData Descrizione Importo"))
    chunks = FileProcessor.extract_from_pdf(io.BytesIO(b"%PDF"))
    assert "pdf_text" in chunks[0]  # anything else still goes to the model


class FakeApi:
    def __init__(self):
        self.calls = []
        self.imports = []

    async def call_tool(self, name, args):
        if name == "resolve_merchants":
            return [{"description": d, "category": None} for d in args["descriptions"]]
        if name == "get_blueprint":
            return {"blueprint": {"categories": [{"key": "interest", "label": "Interessi", "kind": "income"}, {"key": "transfer", "label": "Giroconti"}]}}
        if name == "import_statement":
            self.imports.append(args)
            return {"batch_id": 1, "row_count": len(args["transactions"]), "added_count": len(args["transactions"]), "skipped_count": 0,
                    "transfers_detected": 0, "duplicate_file": False, "invalid_rows": [], "transactions": [{**r, "id": i, "reviewed": True} for i, r in enumerate(args["transactions"])]}
        raise AssertionError(name)

    async def request(self, method, path, json=None, params=None):
        self.calls.append((method, path, json))
        if path == "/api/portfolio/holdings":
            return {"instruments_created": len(json["holdings"]), "instruments_updated": 0}
        return {"saved": 1}


def test_upload_of_a_known_statement_records_balance_and_holdings(monkeypatch):
    import src.routes.statements as statements
    import src.services.file_processor as fp

    api = FakeApi()

    async def fake_api():
        return api

    monkeypatch.setattr(statements, "get_api_client", fake_api)
    monkeypatch.setattr(fp, "PdfReader", lambda content: FakePdfReader(TRADING))
    with TestClient(app) as client:
        res = client.post("/statements/upload", files={"file": ("robo.pdf", b"%PDF-1.4 fake", "application/pdf")}, data={"account_id": "9"})
        assert res.status_code == 200, res.text
        job = res.json()["job_id"]
        status = client.get(f"/statements/jobs/{job}").json()
    assert status["status"] == "complete", status
    result = status["result"]
    assert result["transactions_added"] == 6
    assert result["closing_balance"] == {"date": "2026-11-21", "balance": round(169.01 + 416.35 + 55.03, 2)}
    assert result["holdings"]["instruments_created"] == 2
    posted = {p for _, p, _ in api.calls}
    assert posted == {"/api/balance-snapshots/bulk", "/api/portfolio/holdings"}
    snapshot = next(j for m, p, j in api.calls if p == "/api/balance-snapshots/bulk")
    assert snapshot["source"] == "import" and snapshot["snapshots"][0]["account_id"] == 9
    categories = Counter(r.get("category") for r in api.imports[0]["transactions"])
    assert categories["investments"] == 4 and categories["transfer"] == 1
