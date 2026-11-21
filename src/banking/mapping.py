"""Pure functions turning Enable Banking payloads into what the API stores: balances, import rows, account labels."""

from __future__ import annotations

from datetime import date
from typing import Any

# closing booked first (the accounting balance a statement would show), then the interim/expected ones
BALANCE_PREFERENCE = ("CLBD", "ITBD", "XPCD", "ITAV", "CLAV", "OPBD", "PRCD", "OTHR", "INFO")


def mask_iban(iban: str | None) -> str | None:
    if not iban:
        return None
    compact = "".join(iban.split())
    if len(compact) <= 8:
        return compact
    return f"{compact[:4]} •••• {compact[-4:]}"


def account_summary(account: dict[str, Any]) -> dict[str, Any]:
    """What the API keeps about a bank account (never the full IBAN)."""
    ident = account.get("account_id") or {}
    other = ident.get("other") or {}
    label = account.get("product") or account.get("name") or account.get("details") or other.get("identification") or "Conto"
    return {
        "identification_hash": account.get("identification_hash") or account.get("uid") or "",
        "bank_uid": account.get("uid"),
        "iban_masked": mask_iban(ident.get("iban")),
        "label": str(label)[:200],
        "currency": (account.get("currency") or "EUR")[:3],
        "cash_account_type": account.get("cash_account_type"),
    }


def _amount(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def pick_balance(balances: list[dict[str, Any]]) -> tuple[float, date | None, str] | None:
    """The balance to record: (amount, reference date, type), preferring the closing booked one."""
    ranked = sorted(
        balances,
        key=lambda b: BALANCE_PREFERENCE.index(b.get("balance_type")) if b.get("balance_type") in BALANCE_PREFERENCE else len(BALANCE_PREFERENCE),
    )
    for item in ranked:
        amount = _amount((item.get("balance_amount") or {}).get("amount"))
        if amount is None:
            continue
        ref = item.get("reference_date")
        try:
            ref_date = date.fromisoformat(str(ref)[:10]) if ref else None
        except ValueError:
            ref_date = None
        return amount, ref_date, str(item.get("balance_type") or "OTHR")
    return None


def describe(tx: dict[str, Any], aspsp_name: str) -> tuple[str, bool]:
    """A statement-like description and whether the bank actually said anything (Trade Republic does not)."""
    remittance = " ".join(str(p).strip() for p in (tx.get("remittance_information") or []) if p and str(p).strip())
    debit = (tx.get("credit_debit_indicator") or "").upper() == "DBIT"
    party = (tx.get("creditor") if debit else tx.get("debtor")) or {}
    counterparty = str(party.get("name") or "").strip()
    parts: list[str] = []
    if counterparty and counterparty.lower() not in remittance.lower():
        parts.append(counterparty)
    if remittance:
        parts.append(remittance)
    if not parts:
        code = (tx.get("bank_transaction_code") or {}).get("description")
        if code:
            parts.append(str(code))
    if not parts:
        return f"Movimento {aspsp_name}", False
    return " ".join(" ".join(parts).split())[:500], True


def external_id_of(tx: dict[str, Any]) -> str | None:
    """The bank's stable reference, when it gives one. ``transaction_id`` may change between calls: not used."""
    for key in ("entry_reference", "reference_number"):
        value = tx.get(key)
        if value and str(value).strip():
            return str(value).strip()[:128]
    return None


def transaction_to_row(tx: dict[str, Any], aspsp_name: str) -> dict[str, Any] | None:
    """An import row for a booked transaction; ``None`` for pending/rejected ones or unreadable payloads."""
    status = (tx.get("status") or "BOOK").upper()
    if status != "BOOK":
        return None
    day = tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date")
    amount = _amount((tx.get("transaction_amount") or {}).get("amount"))
    if not day or amount is None:
        return None
    indicator = (tx.get("credit_debit_indicator") or "").upper()
    signed = -abs(amount) if indicator == "DBIT" else abs(amount) if indicator == "CRDT" else amount
    description, has_description = describe(tx, aspsp_name)
    return {
        "date": str(day)[:10],
        "amount": round(signed, 2),
        "description": description,
        "currency": ((tx.get("transaction_amount") or {}).get("currency") or "EUR")[:3],
        "external_id": external_id_of(tx),
        "has_description": has_description,
    }


def rows_from_transactions(transactions: list[dict[str, Any]], aspsp_name: str) -> list[dict[str, Any]]:
    rows = [transaction_to_row(tx, aspsp_name) for tx in transactions]
    return [r for r in rows if r is not None]
