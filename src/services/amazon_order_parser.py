"""Amazon order-history CSV parser.

Groups order line-items (a single Amazon order can span several product rows
billed together), matches each order to an existing bank transaction, and
re-categorizes that transaction via LLM based on the products actually
purchased — instead of leaving every Amazon charge tagged as generic
'shopping'.
"""

import csv
import io
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


from src.llm import LLMClient, get_llm_client
from src.llm.categorize import categorize_orders

logger = logging.getLogger(__name__)

# A candidate transaction must mention Amazon in its description.
AMAZON_DESCRIPTION_MARKERS = ("amazon", "amzn")

# Amazon usually charges on ship date, which can lag the order date by a
# couple of weeks for delayed or split shipments.
MATCH_WINDOW_DAYS = 30

# Floating point tolerance when comparing an order total to a transaction amount.
AMOUNT_TOLERANCE = 0.01

REQUIRED_COLUMNS = {"Order ID", "Order Date", "Product Name", "Total Amount"}


class AmazonCsvError(Exception):
    """Raised when the uploaded file isn't a valid Amazon order-history export."""

    pass


class AmazonOrderParser:
    """Parses Amazon 'Order History' CSV exports and reconciles them against
    existing transactions in the finance DB."""

    @staticmethod
    def _parse_order_date(value: Optional[str]) -> Optional[date]:
        """Amazon order dates look like '2026-02-13T21:16:45Z'."""
        if not value:
            return None
        value = value.strip()
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            logger.warning(f"Could not parse Amazon order date: {value}")
            return None

    @staticmethod
    def _parse_amount(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(str(value).strip())
        except ValueError:
            logger.warning(f"Could not parse Amazon order amount: {value}")
            return None

    @staticmethod
    def parse_csv(file_content: bytes) -> List[Dict[str, Any]]:
        """Parse raw CSV bytes into one dict per Order ID.

        Returns a list of:
            {"order_id": str, "order_date": "YYYY-MM-DD", "total_amount": float,
             "currency": str, "products": [str, ...]}
        """
        try:
            text_content = file_content.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise AmazonCsvError(f"Could not decode file as UTF-8: {str(e)}")

        reader = csv.DictReader(io.StringIO(text_content))
        fieldnames = set(reader.fieldnames or [])
        if not REQUIRED_COLUMNS.issubset(fieldnames):
            missing = REQUIRED_COLUMNS - fieldnames
            raise AmazonCsvError(
                f"Missing expected Amazon order-history columns: {', '.join(sorted(missing))}"
            )

        groups: Dict[str, Dict[str, Any]] = {}
        order_sequence: List[str] = []

        for row in reader:
            order_id = (row.get("Order ID") or "").strip()
            if not order_id:
                continue

            order_date = AmazonOrderParser._parse_order_date(row.get("Order Date"))
            amount = AmazonOrderParser._parse_amount(row.get("Total Amount"))
            product = (row.get("Product Name") or "").strip() or "Unknown product"

            if order_id not in groups:
                order_sequence.append(order_id)
                groups[order_id] = {
                    "order_id": order_id,
                    "order_date": order_date,
                    "total_amount": 0.0,
                    "currency": (row.get("Currency") or "EUR").strip() or "EUR",
                    "products": [],
                }

            group = groups[order_id]
            if amount is not None:
                group["total_amount"] += amount
            group["products"].append(product)
            if order_date and (group["order_date"] is None or order_date < group["order_date"]):
                group["order_date"] = order_date

        orders = []
        for order_id in order_sequence:
            g = groups[order_id]
            if g["order_date"] is None:
                logger.warning(f"Skipping order {order_id}: no parseable order date")
                continue
            g["order_date"] = g["order_date"].isoformat()
            g["total_amount"] = round(g["total_amount"], 2)
            orders.append(g)

        logger.info(f"Parsed {len(orders)} Amazon order(s) from {len(order_sequence)} row group(s)")
        return orders

    @staticmethod
    def _is_amazon_transaction(description: str) -> bool:
        desc_lower = (description or "").lower()
        return any(marker in desc_lower for marker in AMAZON_DESCRIPTION_MARKERS)

    @staticmethod
    def match_orders_to_transactions(
        orders: List[Dict[str, Any]],
        transactions: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Match each Amazon order to exactly one existing transaction.

        A transaction is a candidate if:
        - its description mentions Amazon
        - |amount| equals the order total (within AMOUNT_TOLERANCE)
        - its date falls within [order_date, order_date + MATCH_WINDOW_DAYS]

        Each transaction can be claimed by at most one order (earliest order first).
        Orders with zero or more than one candidate are reported as unmatched rather
        than guessed at, to avoid overwriting the wrong transaction's category.

        Returns (matched, unmatched).
        """
        amazon_transactions = [
            t for t in transactions if AmazonOrderParser._is_amazon_transaction(t.get("description", ""))
        ]

        used_transaction_ids: set = set()
        matched: List[Dict[str, Any]] = []
        unmatched: List[Dict[str, Any]] = []

        for order in sorted(orders, key=lambda o: o["order_date"]):
            order_date_obj = date.fromisoformat(order["order_date"])
            window_end = order_date_obj + timedelta(days=MATCH_WINDOW_DAYS)

            candidates = []
            for t in amazon_transactions:
                if t["id"] in used_transaction_ids:
                    continue
                try:
                    tx_date = date.fromisoformat(t["date"])
                except (ValueError, TypeError):
                    continue
                if not (order_date_obj <= tx_date <= window_end):
                    continue
                if abs(abs(t["amount"]) - order["total_amount"]) > AMOUNT_TOLERANCE:
                    continue
                candidates.append(t)

            if len(candidates) == 1:
                tx = candidates[0]
                used_transaction_ids.add(tx["id"])
                matched.append(
                    {
                        "order_id": order["order_id"],
                        "products": order["products"],
                        "total_amount": order["total_amount"],
                        "transaction": tx,
                    }
                )
            else:
                unmatched.append(
                    {
                        "order_id": order["order_id"],
                        "products": order["products"],
                        "total_amount": order["total_amount"],
                        "order_date": order["order_date"],
                        "reason": "ambiguous_match" if candidates else "no_matching_transaction",
                        "candidate_transaction_ids": [c["id"] for c in candidates],
                    }
                )

        return matched, unmatched

    @staticmethod
    async def categorize_orders_with_llm(
        matched_orders: List[Dict[str, Any]],
        existing_categories: List[str],
        llm: Optional[LLMClient] = None,
    ) -> Dict[str, str]:
        """Categorize each matched order's products via the configured LLM, in batches.

        Returns {order_id: category}. Orders the LLM does not answer for are omitted,
        leaving their transaction's category untouched.
        """
        if not matched_orders:
            return {}
        llm = llm or get_llm_client()

        categories = await categorize_orders(
            llm, [m["products"] for m in matched_orders], list(existing_categories)
        )
        return {
            matched_orders[position]["order_id"]: category for position, category in categories.items()
        }
