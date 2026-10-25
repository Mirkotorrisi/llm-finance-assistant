"""Read a personal monthly sheet: one row per month, one column per account.

The sheet people keep by hand looks like::

    |           | ENTRATE UNICREDIT | SPESE UNICREDIT | SALDO |      <- flows (income / expenses per account)
    | GENNAIO   | 4.205,00          | 1.564,00        | ...   |
    ...
    |           | UNICREDIT | REVOLUT | TRADE INVESTIMENTI |         <- what each account held at month end
    | GENNAIO   | 95.841,00 | 39.371  | 4.246              |

Blocks are found by their month names in a column, whatever their position, so several tables stacked in one
sheet work. The result is a preview: the user maps columns to accounts and nothing is stored here.
"""

import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

MONTHS = {
    1: ("gennaio", "gen", "january", "jan"),
    2: ("febbraio", "feb", "february"),
    3: ("marzo", "mar", "march"),
    4: ("aprile", "apr", "april"),
    5: ("maggio", "mag", "may"),
    6: ("giugno", "giu", "june", "jun"),
    7: ("luglio", "lug", "july", "jul"),
    8: ("agosto", "ago", "august", "aug"),
    9: ("settembre", "set", "sett", "september", "sep"),
    10: ("ottobre", "ott", "october", "oct"),
    11: ("novembre", "nov", "november"),
    12: ("dicembre", "dic", "december", "dec"),
}
_MONTH_BY_WORD = {word: n for n, words in MONTHS.items() for word in words}
_INCOME_RE = re.compile(r"^(entrat\w*|income|ricav\w*)\s+(.+)$", re.IGNORECASE)
_EXPENSE_RE = re.compile(r"^(uscit\w*|spes\w*|expens\w*)\s+(.+)$", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()


def month_of(cell: Any) -> Optional[int]:
    word = unicodedata.normalize("NFKD", _text(cell)).encode("ascii", "ignore").decode().lower().strip(". ")
    return _MONTH_BY_WORD.get(word)


def parse_amount(cell: Any) -> Optional[float]:
    """A number from a cell: a float, or text such as '€ 4.205,00', '-€ 23.360,00', '1,234.50'. None if not a number."""
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return None if cell != cell else float(cell)  # NaN
    text = _text(cell).replace("\xa0", " ")
    if not text or not re.search(r"\d", text):
        return None
    if not re.fullmatch(r"[\s\-+()€$£.,\d]*(eur)?[\s\-+()€$£.,\d]*", text, re.IGNORECASE):
        return None  # words around the digits ("Estinzione mutuo 30mila"): a note, not an amount
    negative = text.startswith("-") or text.startswith("(") or "-" in text.split(re.search(r"\d", text).group())[0]
    digits = re.sub(r"[^0-9.,]", "", text)
    if not digits:
        return None
    if "," in digits and "." in digits:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif "," in digits:
        decimal = ","
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):
        decimal = None  # 1.234 / 12.345.678: thousands separators
    else:
        decimal = "."
    if decimal is None:
        digits = digits.replace(".", "")
    else:
        other = "." if decimal == "," else ","
        digits = digits.replace(other, "").replace(decimal, ".")
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if negative else value


def _guess_year(grid: List[List[Any]], filename: Optional[str], today: date) -> int:
    for source in ([filename or ""] + [_text(c) for row in grid[:15] for c in row]):
        match = _YEAR_RE.search(source)
        if match:
            return int(match.group(1))
    return today.year


def _month_column(row: List[Any]) -> Optional[int]:
    for j, cell in enumerate(row[:4]):
        if month_of(cell) is not None:
            return j
    return None


def _blocks(grid: List[List[Any]]) -> List[Tuple[int, int, List[int]]]:
    """(month column, header row, month rows) of every run of at least 3 consecutive month rows."""
    blocks: List[Tuple[int, int, List[int]]] = []
    i = 0
    while i < len(grid):
        col = _month_column(grid[i])
        if col is None:
            i += 1
            continue
        rows = [i]
        j = i + 1
        while j < len(grid) and j < len(grid) and len(grid[j]) > col and month_of(grid[j][col]) is not None:
            rows.append(j)
            j += 1
        if len(rows) >= 3:
            header = next((h for h in range(i - 1, max(i - 4, -1), -1) if sum(1 for c in grid[h] if _text(c)) >= 2), None)
            if header is not None:
                blocks.append((col, header, rows))
        i = j
    return blocks


def parse_balance_sheet(grid: List[List[Any]], filename: Optional[str] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """Balances and flows per month found in a grid. Months after the current one are dropped (placeholders)."""
    today = today or date.today()
    year = _guess_year(grid, filename, today)
    balances: List[Dict[str, Any]] = []
    flows: Dict[str, Dict[str, Any]] = {}
    notes: Dict[int, List[str]] = {}

    for month_col, header_row, month_rows in _blocks(grid):
        headers = {j: _text(c) for j, c in enumerate(grid[header_row]) if j != month_col and _text(c)}
        is_flows = sum(1 for h in headers.values() if _INCOME_RE.match(h) or _EXPENSE_RE.match(h)) >= 2
        for j, name in headers.items():
            values: Dict[str, float] = {}
            for r in month_rows:
                month = month_of(grid[r][month_col])
                cell = grid[r][j] if j < len(grid[r]) else None
                amount = parse_amount(cell)
                if amount is None:
                    if is_flows and _text(cell) and month:
                        notes.setdefault(month, []).append(_text(cell))
                    continue
                if (year, month) > (today.year, today.month) and amount == 0:
                    continue  # a future month filled with zeros is a placeholder
                values[str(month)] = round(amount, 2)
            if not values:
                continue
            income, expense = _INCOME_RE.match(name), _EXPENSE_RE.match(name)
            if is_flows and (income or expense):
                label = (income or expense).group(2).strip()
                entry = flows.setdefault(label.lower(), {"label": label, "income": {}, "expenses": {}})
                entry["income" if income else "expenses"] = {m: abs(v) for m, v in values.items()}
            elif not is_flows:
                balances.append({"name": name, "values": values})

    return {
        "year": year,
        "balances": balances,
        "flows": list(flows.values()),
        "notes": [{"month": m, "text": "; ".join(dict.fromkeys(t))} for m, t in sorted(notes.items())],
    }
