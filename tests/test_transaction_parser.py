"""Unit tests for transaction parser service."""

import pytest
from src.services.transaction_parser import TransactionParser


class TestDateParsing:
    """Tests for date parsing."""
    
    def test_parse_date_iso_format(self):
        """Test parsing ISO format dates."""
        result = TransactionParser.parse_date("2026-01-11")
        assert result == "2026-01-11"
    
    def test_parse_date_slash_format(self):
        """Test parsing slash format dates."""
        result = TransactionParser.parse_date("11/01/2026")
        assert result == "2026-01-11"
    
    def test_parse_date_invalid(self):
        """Test parsing invalid dates."""
        result = TransactionParser.parse_date("invalid")
        assert result is None


class TestAmountParsing:
    """Tests for amount parsing."""
    
    def test_parse_amount_simple(self):
        """Test parsing simple amounts."""
        assert TransactionParser.parse_amount("50.00") == 50.0
        assert TransactionParser.parse_amount("100") == 100.0
    
    def test_parse_amount_with_currency(self):
        """Test parsing amounts with currency symbols."""
        assert TransactionParser.parse_amount("$50.00") == 50.0
        assert TransactionParser.parse_amount("€100.00") == 100.0
    
    def test_parse_amount_european_format(self):
        """Test parsing European format amounts."""
        assert TransactionParser.parse_amount("1.234,56") == 1234.56
    
    def test_parse_amount_invalid(self):
        """Test parsing invalid amounts."""
        result = TransactionParser.parse_amount("invalid")
        assert result is None


class TestCurrencyExtraction:
    """Tests for currency extraction."""
    
    def test_extract_currency_symbols(self):
        """Test extracting currency from symbols."""
        assert TransactionParser.extract_currency("$50.00") == "USD"
        assert TransactionParser.extract_currency("€50.00") == "EUR"
        assert TransactionParser.extract_currency("£50.00") == "GBP"
    
    def test_extract_currency_default(self):
        """Test default currency."""
        assert TransactionParser.extract_currency("50.00") == "EUR"


class TestCategorization:
    """Tests for transaction categorization."""
    
    def test_categorize_income(self):
        """Test categorizing income transactions."""
        category = TransactionParser.categorize_transaction("Salary payment", 2000.0)
        assert category == "income"
    
    def test_categorize_food(self):
        """Test categorizing food transactions."""
        category = TransactionParser.categorize_transaction("Grocery shopping", -50.0)
        assert category == "food"
    
    def test_categorize_transport(self):
        """Test categorizing transport transactions."""
        category = TransactionParser.categorize_transaction("Gas station", -40.0)
        assert category == "transport"
    
    def test_categorize_shopping(self):
        """Test categorizing shopping transactions."""
        category = TransactionParser.categorize_transaction("Amazon purchase", -75.0)
        assert category == "shopping"
    
    def test_categorize_other(self):
        """Test categorizing unknown transactions."""
        category = TransactionParser.categorize_transaction("Unknown expense", -25.0)
        assert category == "other"


class TestRowParsing:
    """Tests for row parsing."""
    
    def test_parse_row_csv_format(self):
        """Test parsing CSV-style row."""
        row = {
            "date": "2026-01-11",
            "description": "Grocery",
            "amount": "50.00",
            "currency": "USD"
        }
        
        result = TransactionParser.parse_row(row)
        
        assert result is not None
        assert result["date"] == "2026-01-11"
        assert result["description"] == "Grocery"
        assert result["amount"] == 50.0
        assert result["currency"] == "USD"
    
    def test_parse_row_missing_date(self):
        """Test parsing row with missing date."""
        row = {
            "description": "Grocery",
            "amount": "50.00"
        }
        
        result = TransactionParser.parse_row(row)
        
        assert result is None
    
    def test_parse_row_missing_amount(self):
        """Test parsing row with missing amount."""
        row = {
            "date": "2026-01-11",
            "description": "Grocery"
        }
        
        result = TransactionParser.parse_row(row)
        
        assert result is None


class TestItalianHeaders:
    def test_a_typical_italian_bank_csv_is_read(self):
        row = {"Data": "05/03/2026", "Descrizione": "PAGAMENTO POS COOP", "Importo": "-54,20", "Valuta": "EUR"}
        result = TransactionParser.parse_row(row)
        assert result["date"] == "2026-03-05"
        assert result["description"] == "PAGAMENTO POS COOP"
        assert result["amount"] == -54.20
        assert result["currency"] == "EUR"

    def test_other_common_variants(self):
        for date_key, desc_key, amount_key in [
            ("Data operazione", "Causale", "Importo (EUR)"),
            ("Data contabile", "Descrizione operazione", "Importo EUR"),
            ("DATA VALUTA", "DETTAGLI", "IMPORTO"),
        ]:
            result = TransactionParser.parse_row({date_key: "2026-03-05", desc_key: "Bar", amount_key: "-3,50"})
            assert result is not None, (date_key, desc_key, amount_key)
            assert result["amount"] == -3.50

    def test_a_category_column_in_italian_is_kept(self):
        row = {"Data": "2026-03-05", "Descrizione": "Bar", "Importo": "-3", "Categoria": "Svago"}
        assert TransactionParser.parse_row(row)["category"] == "svago"



def test_pdf_chunks_the_model_cannot_read_are_reported_not_dropped():
    """A failed chunk used to vanish silently; now the caller learns which pages are missing."""
    import asyncio

    from src.llm import LLMError
    from src.services.transaction_parser import TransactionParser

    class StubLLM:
        async def structured(self, system, prompt, schema, temperature=0.0):
            if "page 2" in prompt:
                raise LLMError("rate limited")
            return schema(transactions=[{"date": "2026-01-05", "description": "COOP", "amount": -12.5}])

    rows = [{"pdf_text": "page 1 text", "pages": "1"}, {"pdf_text": "page 2 text", "pages": "2"}]
    failed: list[str] = []
    out = asyncio.run(TransactionParser.extract_async(rows, llm=StubLLM(), on_failed_chunks=failed.extend))
    assert len(out) == 1 and out[0]["description"] == "COOP"
    assert failed == ["2"]
