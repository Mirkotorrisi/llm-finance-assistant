"""Unit tests for the Amazon order-history parser service."""

import asyncio

import pytest

from src.services.amazon_order_parser import AmazonCsvError, AmazonOrderParser

CSV_HEADER = "ASIN,Currency,Order Date,Order ID,Product Name,Total Amount\n"


def _csv_bytes(rows: str) -> bytes:
    return (CSV_HEADER + rows).encode("utf-8")


class TestParseCsv:
    def test_parse_single_item_order(self):
        csv_content = _csv_bytes(
            "B001,EUR,2026-02-13T21:16:45Z,171-1111111-1111111,Chicco Passeggino,160.30\n"
        )
        orders = AmazonOrderParser.parse_csv(csv_content)

        assert len(orders) == 1
        assert orders[0]["order_id"] == "171-1111111-1111111"
        assert orders[0]["order_date"] == "2026-02-13"
        assert orders[0]["total_amount"] == 160.30
        assert orders[0]["products"] == ["Chicco Passeggino"]

    def test_groups_multi_item_order_and_sums_amount(self):
        csv_content = _csv_bytes(
            "B001,EUR,2026-02-13T21:16:45Z,171-2222222-2222222,Pantofole,17.95\n"
            "B002,EUR,2026-02-13T21:16:45Z,171-2222222-2222222,Grembiule,11.99\n"
        )
        orders = AmazonOrderParser.parse_csv(csv_content)

        assert len(orders) == 1
        order = orders[0]
        assert order["total_amount"] == pytest.approx(29.94)
        assert order["products"] == ["Pantofole", "Grembiule"]

    def test_handles_utf8_bom(self):
        csv_content = "﻿" + CSV_HEADER + "B001,EUR,2026-02-13T21:16:45Z,171-3-3,Product,10.00\n"
        orders = AmazonOrderParser.parse_csv(csv_content.encode("utf-8"))
        assert len(orders) == 1

    def test_missing_required_columns_raises(self):
        csv_content = b"Foo,Bar\n1,2\n"
        with pytest.raises(AmazonCsvError):
            AmazonOrderParser.parse_csv(csv_content)

    def test_skips_rows_without_order_id(self):
        csv_content = _csv_bytes(",EUR,2026-02-13T21:16:45Z,,Product,10.00\n")
        orders = AmazonOrderParser.parse_csv(csv_content)
        assert orders == []


class TestMatchOrdersToTransactions:
    def _tx(self, id_, date, amount, description, category="shopping"):
        return {"id": id_, "date": date, "amount": amount, "description": description, "category": category}

    def test_matches_single_candidate(self):
        orders = [
            {"order_id": "O1", "order_date": "2026-02-13", "total_amount": 29.94, "products": ["A", "B"]}
        ]
        transactions = [
            self._tx(1, "2026-02-15", -29.94, "AMAZON.IT MKTPLACE"),
            self._tx(2, "2026-02-15", -50.00, "SUPERMARKET"),
        ]
        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, transactions)

        assert len(matched) == 1
        assert matched[0]["transaction"]["id"] == 1
        assert unmatched == []

    def test_ignores_non_amazon_transactions(self):
        orders = [{"order_id": "O1", "order_date": "2026-02-13", "total_amount": 29.94, "products": ["A"]}]
        transactions = [self._tx(1, "2026-02-15", -29.94, "SUPERMARKET PURCHASE")]

        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, transactions)

        assert matched == []
        assert unmatched[0]["reason"] == "no_matching_transaction"

    def test_outside_date_window_is_unmatched(self):
        orders = [{"order_id": "O1", "order_date": "2026-02-13", "total_amount": 29.94, "products": ["A"]}]
        transactions = [self._tx(1, "2026-04-15", -29.94, "AMAZON.IT")]

        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, transactions)

        assert matched == []
        assert unmatched[0]["reason"] == "no_matching_transaction"

    def test_ambiguous_amount_match_is_unmatched(self):
        orders = [{"order_id": "O1", "order_date": "2026-02-13", "total_amount": 29.94, "products": ["A"]}]
        transactions = [
            self._tx(1, "2026-02-15", -29.94, "AMAZON.IT"),
            self._tx(2, "2026-02-16", -29.94, "AMAZON.IT"),
        ]

        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, transactions)

        assert matched == []
        assert unmatched[0]["reason"] == "ambiguous_match"
        assert set(unmatched[0]["candidate_transaction_ids"]) == {1, 2}

    def test_each_transaction_claimed_by_at_most_one_order(self):
        orders = [
            {"order_id": "O1", "order_date": "2026-02-13", "total_amount": 29.94, "products": ["A"]},
            {"order_id": "O2", "order_date": "2026-02-14", "total_amount": 29.94, "products": ["B"]},
        ]
        # Only one real candidate transaction exists for the amount.
        transactions = [self._tx(1, "2026-02-15", -29.94, "AMAZON.IT")]

        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, transactions)

        assert len(matched) == 1
        assert matched[0]["order_id"] == "O1"  # earliest order claims it
        assert len(unmatched) == 1
        assert unmatched[0]["order_id"] == "O2"
        assert unmatched[0]["reason"] == "no_matching_transaction"


class TestCategorizeOrdersWithLlm:
    def test_returns_empty_when_provider_is_unavailable(self):
        from src.llm import LLMError

        class Unavailable:
            name = "stub"

            async def structured(self, *a, **k):
                raise LLMError("OPENAI_API_KEY is not set")

        matched = [{"order_id": "O1", "products": ["Chicco Passeggino"], "transaction": {"id": 1}}]
        result = asyncio.run(AmazonOrderParser.categorize_orders_with_llm(matched, ["shopping"], llm=Unavailable()))

        assert result == {}
