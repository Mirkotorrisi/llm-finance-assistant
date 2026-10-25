"""The user's monthly sheet (rows = months, columns = accounts) read into balances, flows and notes."""

import io
from datetime import date

import openpyxl
import pytest

from src.services.balance_sheet import month_of, parse_amount, parse_balance_sheet
from src.services.file_processor import FileProcessor

TODAY = date(2026, 10, 25)
MONTH_NAMES = ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO", "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"]


def sheet():
    flows = [["BILANCIO FAMILIARE 2026", None, None, None, None, None], ["", "ENTRATE UNICREDIT", "ENTRATE REVOLUT", "SPESE UNICREDIT", "SPESE TRADE REP", "SALDO", "Colonna 2"]]
    income, expenses = [4205, 3603, 6196, 9558, 3900, 6350, 12050, 3800, 6333, 9108, 0, 0], [1564, 1169, 1638, 31196, 8133, 1202, 20323, 8758, 8912, 750, 0, 0]
    for i, name in enumerate(MONTH_NAMES):
        note = {1: "Divano e macchina anticipo", 3: "Estinzione parziale mutuo 30mila"}.get(i)
        flows.append([name, f"€ {income[i]:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), 3000, expenses[i], 1294, 4347, note])
    balances = [[None] * 7, [None, "UNICREDIT", "REVOLUT", "TRADE INVESTIMENTI", "CONTANTI"]]
    values = [95841, 102027, 97672, 104132, 83770, 77934, 84161, 79101, 75560, 83684, 0, 0]
    for i, name in enumerate(MONTH_NAMES):
        balances.append([name, values[i], 39371 + i, 4246 + i, 1000 if i == 9 else None])
    return flows + balances


def test_amounts_in_italian_and_english_formats():
    assert parse_amount("€ 4.205,00") == 4205.0
    assert parse_amount("-€ 23.360,00") == -23360.0
    assert parse_amount("€ -3.682,00") == -3682.0
    assert parse_amount("1,234.50") == 1234.5
    assert parse_amount("12.345") == 12345.0 and parse_amount("12.5") == 12.5
    assert parse_amount(3000) == 3000.0 and parse_amount(None) is None
    assert parse_amount("Estinzione parziale mutuo 30mila") is None and parse_amount("") is None


def test_month_names_in_italian_and_english():
    assert [month_of(c) for c in ("Gennaio", "SET", "Dec.", "TOTALE", None)] == [1, 9, 12, None, None]


def test_two_stacked_tables_become_balances_flows_and_notes():
    out = parse_balance_sheet(sheet(), today=TODAY)
    assert out["year"] == 2026
    by_name = {b["name"]: b["values"] for b in out["balances"]}
    assert set(by_name) == {"UNICREDIT", "REVOLUT", "TRADE INVESTIMENTI", "CONTANTI"}
    assert by_name["UNICREDIT"]["1"] == 95841.0 and by_name["UNICREDIT"]["10"] == 83684.0
    assert "11" not in by_name["UNICREDIT"] and "12" not in by_name["UNICREDIT"]  # future months of zeros
    assert by_name["CONTANTI"] == {"10": 1000.0}

    flows = {f["label"]: f for f in out["flows"]}
    assert set(flows) == {"UNICREDIT", "REVOLUT", "TRADE REP"}  # SALDO and the free column are not accounts
    assert flows["UNICREDIT"]["income"]["1"] == 4205.0 and flows["UNICREDIT"]["expenses"]["4"] == 31196.0
    assert flows["TRADE REP"]["income"] == {} and flows["REVOLUT"]["expenses"] == {}

    assert out["notes"] == [{"month": 2, "text": "Divano e macchina anticipo"}, {"month": 4, "text": "Estinzione parziale mutuo 30mila"}]


def test_year_comes_from_the_file_name_when_the_sheet_has_none():
    grid = [r for r in sheet() if r[0] != "BILANCIO FAMILIARE 2026"]
    assert parse_balance_sheet(grid, filename="bilancio-2025.xlsx", today=TODAY)["year"] == 2025
    assert parse_balance_sheet(grid, today=TODAY)["year"] == 2026


def test_a_file_that_is_not_a_month_sheet_yields_nothing():
    out = parse_balance_sheet([["Data", "Descrizione", "Importo"], ["2026-03-02", "COOP", -54.2]], today=TODAY)
    assert out["balances"] == [] and out["flows"] == [] and out["notes"] == []


def test_reads_a_real_xlsx_and_a_semicolon_csv():
    book = openpyxl.Workbook()
    for row in sheet():
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    out = parse_balance_sheet(FileProcessor.read_grid(buffer.getvalue()), today=TODAY)
    assert {b["name"] for b in out["balances"]} == {"UNICREDIT", "REVOLUT", "TRADE INVESTIMENTI", "CONTANTI"}

    csv = "\n".join(";".join("" if c is None else str(c) for c in row) for row in sheet()).encode("cp1252")
    out = parse_balance_sheet(FileProcessor.read_grid(csv), today=TODAY)
    assert {f["label"] for f in out["flows"]} == {"UNICREDIT", "REVOLUT", "TRADE REP"}


def test_the_endpoint_returns_the_preview_and_refuses_other_files():
    from fastapi.testclient import TestClient

    from app import app

    book = openpyxl.Workbook()
    for row in sheet():
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    client = TestClient(app)

    ok = client.post("/statements/balance-sheet", files={"file": ("bilancio-2026.xlsx", buffer.getvalue())})
    assert ok.status_code == 200 and {b["name"] for b in ok.json()["balances"]} >= {"UNICREDIT", "REVOLUT"}

    statement = "Data;Descrizione;Importo\n2026-03-02;COOP;-54,20\n".encode()
    assert client.post("/statements/balance-sheet", files={"file": ("movimenti.csv", statement)}).status_code == 422
    pdf = client.post("/statements/balance-sheet", files={"file": ("bilancio.pdf", b"%PDF-1.4 x")})
    assert pdf.status_code == 400 and "xlsx" in pdf.json()["detail"]
