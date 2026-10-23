"""Bank statements come in many disguises: real .xls, .xlsx, HTML or text named .xls/.csv, with account info above
the table. Every one must come out as the same transactions."""

import asyncio
import datetime
import io

import openpyxl
import pytest
import xlwt

from src.services.file_processor import FileProcessor, FileValidationError
from src.services.transaction_parser import TransactionParser

# The layout of an Italian bank export: rows about the account, then the table, then a footer
HEAD = [
    ["Elenco movimenti", None, None, None, None],
    ["Conto: IT00X0000000000000000000000", None, None, None, None],
    ["Periodo: 01/03/2026 - 31/03/2026", None, None, None, None],
    [None, None, None, None, None],
]
HEADER = ["Data registrazione", "Data Valuta", "Causale", "Descrizione", "Importo (EUR)"]
BODY = [
    [datetime.date(2026, 3, 2), datetime.date(2026, 3, 3), "Pagamento POS", "COOP LOMBARDIA MILANO", -54.20],
    [datetime.date(2026, 3, 27), datetime.date(2026, 3, 27), "Bonifico", "STIPENDIO ACME", 2100.0],
]
FOOTER = [["Dati aggiornati alle: 23:08:14 del 22/10/2026", None, None, None, None]]
GRID = HEAD + [HEADER] + BODY + FOOTER


def run(coro):
    return asyncio.run(coro)


def expected_transactions():
    return [
        {"date": "2026-03-02", "description": "COOP LOMBARDIA MILANO", "amount": -54.2},
        {"date": "2026-03-27", "description": "STIPENDIO ACME", "amount": 2100.0},
    ]


def read(filename: str, raw: bytes):
    rows = FileProcessor.process_file(filename, io.BytesIO(raw), len(raw))
    parsed = run(TransactionParser.extract_async(rows))
    return [{k: t[k] for k in ("date", "description", "amount")} for t in parsed]


def xls_bytes() -> bytes:
    book = xlwt.Workbook()
    sheet = book.add_sheet("Movimenti")
    date_style = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    for r, row in enumerate(GRID):
        for c, value in enumerate(row):
            if value is None:
                continue
            sheet.write(r, c, value, date_style if isinstance(value, datetime.date) else xlwt.Style.default_style)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def xlsx_bytes() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    for row in GRID:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def html_bytes() -> bytes:
    def cell(v):
        return f"<td>{'' if v is None else (v.strftime('%d/%m/%Y') if isinstance(v, datetime.date) else str(v).replace('.', ',') if isinstance(v, float) else v)}</td>"

    rows = "".join("<tr>" + "".join(cell(v) for v in row) + "</tr>" for row in GRID)
    return f"<html><body><table>{rows}</table></body></html>".encode("cp1252")


def text_bytes(delimiter=";", encoding="cp1252") -> bytes:
    def cell(v):
        if v is None:
            return ""
        if isinstance(v, datetime.date):
            return v.strftime("%d/%m/%Y")
        return str(v).replace(".", ",") if isinstance(v, float) else str(v)

    return "\n".join(delimiter.join(cell(v) for v in row) for row in GRID).encode(encoding)


# ----------------------------------------------------------------------------- one result, every format

@pytest.mark.parametrize("filename,builder", [
    ("Elenco_Movimenti.xls", xls_bytes),      # the real binary format (what failed with "File is not a zip file")
    ("movimenti.xlsx", xlsx_bytes),
    ("movimenti.xls", html_bytes),            # HTML table saved with an .xls extension
    ("movimenti.csv", text_bytes),            # semicolons, decimal commas, Windows encoding
    ("movimenti.xls", lambda: text_bytes("\t", "utf-16")),  # tab-separated text named .xls
    ("movimenti.csv", xlsx_bytes),            # the extension lies the other way round
    ("movimenti.xlsx", xls_bytes),
])
def test_every_format_gives_the_same_transactions(filename, builder):
    assert read(filename, builder()) == expected_transactions()


def test_rows_above_the_table_and_the_footer_are_not_transactions():
    rows = FileProcessor.process_file("x.xls", io.BytesIO(xls_bytes()), 1)
    assert list(rows[0].keys()) == HEADER  # the table's own headings, not the account rows above
    assert len(rows) == 3  # two movements + the footer row, which the parser then ignores
    assert TransactionParser.parse_row(rows[-1]) is None


def test_the_registration_date_is_preferred_over_the_value_date():
    xls = xlwt.Workbook()
    sheet = xls.add_sheet("m")
    for c, h in enumerate(HEADER):
        sheet.write(0, c, h)
    style = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    sheet.write(1, 0, datetime.date(2026, 3, 2), style)
    sheet.write(1, 1, datetime.date(2026, 3, 9), style)
    sheet.write(1, 3, "Shop")
    sheet.write(1, 4, -1.0)
    out = io.BytesIO()
    xls.save(out)
    assert read("a.xls", out.getvalue())[0]["date"] == "2026-03-02"


def test_a_plain_csv_without_account_rows_still_works():
    raw = b"date,description,amount\n2026-01-15,Grocery,-50.00\n"
    assert read("a.csv", raw) == [{"date": "2026-01-15", "description": "Grocery", "amount": -50.0}]


def test_utf8_with_bom_and_accents():
    raw = "﻿Data;Descrizione;Importo\n05/03/2026;Caffè Müller;-1,20\n".encode("utf-8")
    assert read("a.csv", raw) == [{"date": "2026-03-05", "description": "Caffè Müller", "amount": -1.2}]


# ----------------------------------------------------------------------------- separate in/out columns

def test_separate_out_and_in_columns_become_one_signed_amount():
    raw = "Data;Descrizione;Uscite;Entrate\n05/03/2026;Bar;3,50;\n06/03/2026;Stipendio;;2.100,00\n".encode("cp1252")
    assert read("a.csv", raw) == [
        {"date": "2026-03-05", "description": "Bar", "amount": -3.5},
        {"date": "2026-03-06", "description": "Stipendio", "amount": 2100.0},
    ]
    dare_avere = b"Data;Descrizione;Dare;Avere\n05/03/2026;Bar;3,50;\n06/03/2026;Bonifico;;10,00\n"
    assert [t["amount"] for t in read("b.csv", dare_avere)] == [-3.5, 10.0]


# ----------------------------------------------------------------------------- errors

def test_a_pdf_with_a_spreadsheet_extension_is_explained():
    with pytest.raises(FileValidationError, match="PDF"):
        FileProcessor.process_file("a.xls", io.BytesIO(b"%PDF-1.4 ..."), 10)


def test_a_corrupt_spreadsheet_gets_a_readable_error():
    with pytest.raises(FileValidationError, match="Failed to read the file"):
        FileProcessor.process_file("a.xlsx", io.BytesIO(b"PK\x03\x04 not really a zip"), 10)


def test_a_file_with_no_recognisable_table_gives_no_transactions_not_a_crash():
    assert read("a.csv", b"hello\nworld\n") == []


def test_header_detection_needs_two_kinds_of_heading():
    from src.services.columns import header_kinds

    assert header_kinds("Data registrazione") == {"date"}
    assert header_kinds("Importo (EUR)") == {"amount"}
    assert header_kinds("Descrizione") == {"description"}
    assert header_kinds("Dati aggiornati alle: 23:08:14 del 22/10/2026") == set()  # a long metadata cell
    assert header_kinds("Data estratto 22/10/2026") == set()  # a date, not a heading
    assert FileProcessor.find_header_row(GRID) == 4
    assert FileProcessor.find_header_row([["Periodo", "marzo"], ["x", "y"]]) == 0  # nothing found: first non-empty row
