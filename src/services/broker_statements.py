"""Deterministic readers for broker and savings statements whose layout is known.

The generic PDF path sends the text to the model; these statements are long, regular and carry data the model would
lose (running balances, ISINs, quantities, prices), so they are read with rules and never reach the model.

Each reader returns ``(rows, meta)``: ``rows`` are statement rows in the tabular shape the row parser understands
(date, description, amount, currency, category hint), ``meta`` carries what a row cannot: the closing balance and
date (recorded as a balance snapshot) and the holdings at statement date (instruments, quantities, prices).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

IT_MONTHS = {"gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12}
EN_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_IT_MONTH_RE = "|".join(IT_MONTHS)
_ISIN_RE = r"[A-Z]{2}[A-Z0-9]{9}[0-9]"


def _it_amount(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def _en_amount(text: str) -> float:
    return float(text.replace(",", ""))


def _it_date(day: str, month: str, year: str) -> date:
    return date(int(year), IT_MONTHS[month.lower()], int(day))


def _en_date(day: str, month: str, year: str) -> date:
    return date(int(year), EN_MONTHS[month.lower()[:3]], int(day))


# ---------------------------------------------------------------------- detection


def detect(text: str) -> Optional[str]:
    if "Deposito senza vincoli" in text and "Interessi netti pagati" in text:
        return "revolut_savings"
    if "Revolut Securities Europe UAB" in text and "Portfolio breakdown" in text:
        return "revolut_trading"
    return None


def parse(text: str) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    kind = detect(text)
    if kind == "revolut_savings":
        return parse_revolut_savings(text)
    if kind == "revolut_trading":
        return parse_revolut_trading(text)
    return None


# ---------------------------------------------------------------------- Revolut "Deposito senza vincoli"

# pypdf glues the year to the next token ("2025Deposito", "in data 27 set 20251.50%"): separators are optional
_SAVINGS_ROW = re.compile(
    rf"(\d{{1,2}})\s+({_IT_MONTH_RE})\s*(\d{{4}})\s*(Deposito|Prelievo|Interessi)(.*?)"
    rf"(?=\d{{1,2}}\s+(?:{_IT_MONTH_RE})\s*\d{{4}}\s*(?:Deposito|Prelievo|Interessi)|Interessi\s+Il tasso|Pagina\s+(?:\d+\s+)?di|$)",
    re.DOTALL,
)
_MONEY_IT = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")
_IN_DATA = re.compile(rf"in data \d{{1,2}}\s+(?:{_IT_MONTH_RE})\s*\d{{4}}", re.IGNORECASE)
_RATE = re.compile(r"(\d{1,2}\.\d{1,2})%")


def parse_revolut_savings(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    flat = re.sub(r"[ \t]+", " ", text)
    rows: List[Dict[str, Any]] = []
    last_balance: Optional[float] = None
    last_day: Optional[date] = None
    for m in _SAVINGS_ROW.finditer(flat):
        day, month, year, kind, body = m.groups()
        # the description repeats a date ("in data 27 set 2025") and the running balance ends the row
        amounts = _MONEY_IT.findall(body)
        if len(amounts) < 2:
            continue
        amount = _it_amount(amounts[-2])
        balance = _it_amount(amounts[-1])
        when = _it_date(day, month, year)
        if kind == "Prelievo":
            rows.append({"date": when.isoformat(), "description": "Giroconto dal conto deposito Revolut (prelievo)", "amount": -amount, "currency": "EUR", "category": "transfer"})
        elif kind == "Deposito":
            rows.append({"date": when.isoformat(), "description": "Giroconto verso il conto deposito Revolut (deposito)", "amount": amount, "currency": "EUR", "category": "transfer"})
        else:
            rate = _RATE.search(_IN_DATA.sub("", body))
            label = f"Interessi netti conto deposito Revolut ({rate.group(1)}%)" if rate else "Interessi netti conto deposito Revolut"
            rows.append({"date": when.isoformat(), "description": label, "amount": amount, "currency": "EUR", "category": "interest"})
        last_balance, last_day = balance, when
    closing = re.search(r"Saldo finale\s*(\d{1,3}(?:\.\d{3})*,\d{2})\s*€", flat)
    period = re.search(rf"Periodo:\s*\d{{1,2}}\s+(?:{_IT_MONTH_RE})\s+\d{{4}}\s*-\s*(\d{{1,2}})\s+({_IT_MONTH_RE})\s+(\d{{4}})", flat)
    meta = {
        "kind": "revolut_savings",
        "closing_balance": _it_amount(closing.group(1)) if closing else last_balance,
        "closing_date": (_it_date(*period.groups()) if period else last_day).isoformat() if (period or last_day) else None,
        "holdings": [],
    }
    return rows, meta


# ---------------------------------------------------------------------- Revolut Securities "Account Statement" (Robo / trading)

_TRADING_SECTION = re.compile(r"(EUR|USD) Account summary")
_HOLDING = re.compile(
    rf"^(\S+)\s+(.+?)\s+({_ISIN_RE})\s+([\d.]+)\s+(?:€|US\$)([\d,]+(?:\.\d+)?)\s+(?:€|US\$)([\d,]+(?:\.\d+)?)\s+[\d.]+%\s*$",
    re.MULTILINE,
)
_TX = re.compile(
    r"(\d{2}) ([A-Z][a-z]{2}) (\d{4}) \d{2}:\d{2}:\d{2} GMT\s+(?:([A-Z0-9]{2,6})\s+)?"
    r"(Cash top-up|Cash withdrawal|Trade - Market|Trade - Limit|Dividend|Robo management fee|Custody fee|Interest|[A-Za-z][A-Za-z \-]{2,30}?)\s+"
    r"(?:([\d.]+)\s+(?:€|US\$)([\d,]+(?:\.\d+)?)\s+(Buy|Sell)\s+)?"
    r"(-?)(?:€|US\$)([\d,]+(?:\.\d+)?)\s+(?:€|US\$)[\d,.]+\s+(?:€|US\$)[\d,.]+",
)
_PERIOD = re.compile(r"Period\s+\d{1,2} [A-Z][a-z]{2} \d{4}\s*-\s*(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})")
_POSITIONS_VALUE = re.compile(r"Positions Value\s+(?:€|US\$)([\d,]+(?:\.\d+)?)\s+(?:€|US\$)([\d,]+(?:\.\d+)?)")
_CASH_VALUE = re.compile(r"Cash value\*?\s+(?:€|US\$)([\d,]+(?:\.\d+)?)\s+(?:€|US\$)([\d,]+(?:\.\d+)?)")

BOND_WORDS = re.compile(r"bond|govies|gilts|treasury|high yield|corporate|obbligaz|aggregate", re.IGNORECASE)
COMMODITY_WORDS = re.compile(r"gold|commodit|oro|silver|metal", re.IGNORECASE)


def guess_asset_class(name: str) -> str:
    if BOND_WORDS.search(name):
        return "bonds"
    if COMMODITY_WORDS.search(name):
        return "commodities"
    return "equity"


def parse_revolut_trading(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    flat = re.sub(r"[ \t]+", " ", text)
    period = _PERIOD.search(flat)
    closing_date = _en_date(*period.groups()) if period else None

    # holdings (the Portfolio breakdown): symbol → isin, name, quantity, price
    holdings: List[Dict[str, Any]] = []
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for symbol, name, isin, qty, price, value in _HOLDING.findall(flat):
        item = {
            "symbol": symbol, "name": name.strip(), "isin": isin, "quantity": float(qty), "price": _en_amount(price),
            "value": _en_amount(value), "currency": "EUR" if f"€{price}" in flat else "USD",
            "asset_class": guess_asset_class(name), "type": "etf" if "ETF" in name.upper() else "stock",
        }
        holdings.append(item)
        by_symbol[symbol] = item

    # transactions: the currency of each section is the one of its "Account summary" heading
    rows: List[Dict[str, Any]] = []
    sections = list(_TRADING_SECTION.finditer(flat))
    for i, sec in enumerate(sections):
        currency = sec.group(1)
        start = sec.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(flat)
        chunk = flat[start:end]
        for m in _TX.finditer(chunk):
            day, month, year, symbol, kind, qty, price, side, minus, value = m.groups()
            when = _en_date(day, month, year).isoformat()
            amount = _en_amount(value)
            kind = kind.strip()
            info = by_symbol.get(symbol or "")
            label = f"{symbol} {info['name']}" if info else (symbol or "")
            isin = f"{info['isin']} " if info else ""
            if kind.startswith("Trade"):
                if side == "Sell":
                    rows.append({"date": when, "description": f"Sell trade {isin}{label}, quantity: {qty}, price: {price}", "amount": amount, "currency": currency, "category": "investments"})
                else:
                    rows.append({"date": when, "description": f"Buy trade {isin}{label}, quantity: {qty}, price: {price}", "amount": -amount, "currency": currency, "category": "investments"})
            elif kind == "Dividend":
                rows.append({"date": when, "description": f"Cash Dividend {isin}{label}".strip(), "amount": amount, "currency": currency, "category": "investments"})
            elif kind == "Cash top-up":
                rows.append({"date": when, "description": "Giroconto verso Revolut investimenti (versamento)", "amount": amount, "currency": currency, "category": "transfer"})
            elif kind == "Cash withdrawal":
                rows.append({"date": when, "description": "Giroconto da Revolut investimenti (prelievo)", "amount": -amount, "currency": currency, "category": "transfer"})
            elif "fee" in kind.lower():
                rows.append({"date": when, "description": f"Revolut investimenti: {kind.lower()}", "amount": -abs(amount), "currency": currency, "category": "bank_fees"})
            else:
                signed = -amount if minus else amount
                rows.append({"date": when, "description": f"Revolut investimenti: {kind}", "amount": signed, "currency": currency, "category": None})

    positions_value = sum(h["value"] for h in holdings)
    cash = _CASH_VALUE.findall(flat)
    cash_value = sum(_en_amount(c[1]) for c in cash) if cash else 0.0
    meta = {
        "kind": "revolut_trading",
        "closing_date": closing_date.isoformat() if closing_date else None,
        "closing_balance": round(positions_value + cash_value, 2) if (holdings or cash) else None,
        "positions_value": round(positions_value, 2),
        "cash_value": round(cash_value, 2),
        "holdings": holdings,
    }
    return rows, meta
