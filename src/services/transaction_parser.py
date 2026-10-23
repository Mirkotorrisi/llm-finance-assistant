"""Transaction parser service for converting extracted data to transactions."""

import asyncio
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from pydantic import BaseModel
from dotenv import load_dotenv

from src.llm import LLMClient, LLMError, get_llm_client
from src.services.columns import (
    AMOUNT_COLUMNS, CATEGORY_COLUMNS, CREDIT_COLUMNS, CURRENCY_COLUMNS, DATE_COLUMNS, DEBIT_COLUMNS, DESCRIPTION_COLUMNS,
)
from src.llm.categorize import categorize_transactions

load_dotenv()
logger = logging.getLogger(__name__)


_PDF_EXTRACT_PROMPT = """You are a financial document parsing expert.
Extract all financial transactions from the bank statement text provided.

Rules:
- Only extract clear, identifiable transactions
- Dates must be in ISO format (YYYY-MM-DD)
- Amounts are negative for expenses/debits, positive for income/credits
- Keep the description as printed on the statement
- Default currency to EUR if not specified
- Do not categorize: categories are decided later"""


class _ParsedTransaction(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str


class _ParsedTransactionList(BaseModel):
    transactions: List[_ParsedTransaction]


class _ExtractedTransaction(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"


class _ExtractedTransactionList(BaseModel):
    transactions: List[_ExtractedTransaction]


_PDF_SYSTEM_PROMPT = """You are a financial document parsing expert.
Extract all financial transactions from the bank statement text provided.

Rules:
- Only extract clear, identifiable transactions
- Dates must be in ISO format (YYYY-MM-DD)
- Amounts are negative for expenses/debits, positive for income/credits
- Categories should be short and descriptive (e.g. food, transport, shopping, utilities, income)
- Default currency to EUR if not specified
- Reuse existing categories when appropriate"""


class TransactionParser:
    """Service for parsing extracted data into transaction format."""
    
    # Maximum characters per PDF chunk sent to the LLM.
    # Each chunk is already bounded by PAGES_PER_CHUNK pages; this is a safety cap.
    MAX_PDF_TEXT_LENGTH = 40000
    
    @staticmethod
    def parse_date(date_str: Any) -> Optional[str]:
        """Parse date string into ISO format.
        
        Args:
            date_str: Date string or datetime object
            
        Returns:
            ISO format date string or None if parsing fails
        """
        if isinstance(date_str, datetime):
            return date_str.date().isoformat()
        
        if not isinstance(date_str, str):
            date_str = str(date_str)
        
        # Try common date formats
        date_formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%m-%d-%Y",
            "%Y/%m/%d",
            "%d.%m.%Y",
            "%d %b %Y",
            "%d %B %Y",
        ]
        
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str.strip(), fmt)
                return parsed_date.date().isoformat()
            except (ValueError, AttributeError):
                continue
        
        logger.warning(f"Could not parse date: {date_str}")
        return None
    
    @staticmethod
    def parse_amount(amount_str: Any) -> Optional[float]:
        """Parse amount string into float.
        
        Args:
            amount_str: Amount string or number
            
        Returns:
            Float amount or None if parsing fails
        """
        if isinstance(amount_str, (int, float)):
            return float(amount_str)
        
        if not isinstance(amount_str, str):
            amount_str = str(amount_str)
        
        # Remove currency symbols and whitespace
        cleaned = re.sub(r'[^\d.,-]', '', amount_str.strip())
        
        # Handle comma as decimal separator (European format)
        if ',' in cleaned and '.' in cleaned:
            # Format like 1.234,56 (European)
            cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            # Format like 1234,56
            cleaned = cleaned.replace(',', '.')
        
        try:
            return float(cleaned)
        except (ValueError, AttributeError):
            logger.warning(f"Could not parse amount: {amount_str}")
            return None
    
    @staticmethod
    def extract_currency(text: str) -> str:
        """Extract currency from text.
        
        Args:
            text: Text containing currency information
            
        Returns:
            Currency code (default: EUR)
        """
        currency_symbols = {
            '$': 'USD',
            '€': 'EUR',
            '£': 'GBP',
            '¥': 'JPY',
            'USD': 'USD',
            'EUR': 'EUR',
            'GBP': 'GBP',
            'JPY': 'JPY',
        }
        
        text_upper = str(text).upper()
        for symbol, code in currency_symbols.items():
            if symbol in str(text):
                return code
            if symbol in text_upper:
                return code
        
        return "EUR"  # Default currency
    
    @staticmethod
    def _match_merchant_rule(
        description: str, merchant_rules: Optional[List[Dict[str, str]]]
    ) -> Optional[str]:
        """Category of the first merchant rule whose pattern appears in the description, if any."""
        if not merchant_rules:
            return None
        desc_lower = description.lower()
        for rule in merchant_rules:
            if rule["pattern"].lower() in desc_lower:
                logger.debug(f"Merchant rule matched '{rule['pattern']}' → '{rule['category']}' for '{description}'")
                return rule["category"]
        return None

    @staticmethod
    def categorize_transaction(description: str, amount: float) -> str:
        """Rule-based categorization, used when the LLM is unavailable or leaves a row unanswered.
        
        Args:
            description: Transaction description
            amount: Transaction amount
            
        Returns:
            Category name
        """
        description_lower = description.lower()
        
        # Income categories
        if amount > 0:
            if any(word in description_lower for word in ['salary', 'wage', 'paycheck', 'income']):
                return "income"
            return "income"
        
        # Expense categories
        food_keywords = ['restaurant', 'grocery', 'food', 'supermarket', 'cafe', 'dinner', 'lunch']
        transport_keywords = ['gas station', 'fuel', 'uber', 'taxi', 'bus', 'train', 'parking', 'transport']
        shopping_keywords = ['amazon', 'shop', 'store', 'retail', 'purchase']
        utilities_keywords = ['electric', 'water', 'gas bill', 'internet', 'phone', 'utility']
        rent_keywords = ['rent', 'lease', 'housing']
        
        if any(word in description_lower for word in food_keywords):
            return "food"
        elif any(word in description_lower for word in transport_keywords):
            return "transport"
        elif any(word in description_lower for word in shopping_keywords):
            return "shopping"
        elif any(word in description_lower for word in utilities_keywords):
            return "utilities"
        elif any(word in description_lower for word in rent_keywords):
            return "rent"
        
        return "other"
    
    @staticmethod
    async def _parse_pdf_chunk_async(
        chunk: Dict[str, Any],
        existing_categories: List[str],
        llm: LLMClient,
        with_category: bool = True,
    ) -> List[Dict[str, Any]]:
        """Parse a single PDF chunk with the configured LLM (used by parse_transactions_async).

        ``with_category=False`` only extracts: categories are decided later, against the project's blueprint."""
        label = chunk.get("pages", f"chunk {chunk.get('chunk_index', '?')}")
        truncated = chunk["pdf_text"][: TransactionParser.MAX_PDF_TEXT_LENGTH]

        existing_categories_text = (
            "None - create appropriate categories"
            if not existing_categories
            else ", ".join(existing_categories)
        )
        user_prompt = (
            f"Bank statement text:\n\n{truncated}\n\n"
            f"Existing categories: {existing_categories_text}"
        )

        try:
            if not with_category:
                text = f"Bank statement text:\n\n{truncated}"
                extracted = await llm.structured(_PDF_EXTRACT_PROMPT, text, _ExtractedTransactionList, temperature=0.1)
                rows = [t.model_dump() for t in extracted.transactions]
                logger.info(f"Chunk {label}: {len(rows)} transactions extracted")
                return rows
            parsed = await llm.structured(_PDF_SYSTEM_PROMPT, user_prompt, _ParsedTransactionList, temperature=0.1)
            valid = [t.model_dump() for t in parsed.transactions]
            logger.info(f"Chunk {label}: {len(valid)} transactions extracted")
            return valid
        except LLMError as e:
            logger.warning(f"Skipped chunk {label}: {e}")
            return []

    @staticmethod
    async def _parse_rows_async(
        rows: List[Dict[str, Any]],
        existing_categories: List[str],
        merchant_rules: Optional[List[Dict[str, str]]],
        llm: LLMClient,
    ) -> List[Dict[str, Any]]:
        """CSV/Excel rows: parse without the LLM, then categorize the rest in batches."""
        transactions: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            try:
                transaction = TransactionParser.parse_row(row, merchant_rules=merchant_rules)
            except Exception as e:
                logger.warning(f"Error parsing row {i}: {str(e)}")
                continue
            if transaction:
                transactions.append(transaction)
            else:
                logger.debug(f"Skipped row {i}: Could not parse")

        pending = [i for i, t in enumerate(transactions) if t["category"] is None]
        if pending:
            known = list(existing_categories) + [t["category"] for t in transactions if t["category"]]
            categories = await categorize_transactions(
                llm, [transactions[i] for i in pending], list(dict.fromkeys(known))
            )
            for position, i in enumerate(pending):
                transactions[i]["category"] = categories.get(position) or TransactionParser.categorize_transaction(
                    transactions[i]["description"], transactions[i]["amount"]
                )

        logger.info(f"Parsed {len(transactions)} transactions from {len(rows)} rows")
        return transactions

    @staticmethod
    async def extract_async(
        rows: List[Dict[str, Any]],
        on_chunk_done: Optional[Any] = None,
        llm: Optional[LLMClient] = None,
    ) -> List[Dict[str, Any]]:
        """Read the transactions out of an uploaded file without deciding their categories.

        CSV/Excel rows are parsed locally (a category column in the file is kept, as ``category``); PDF chunks are
        sent to the LLM in parallel. The result goes to ``classify_for_import``.
        """
        if not (rows and all("pdf_text" in row for row in rows)):
            out: List[Dict[str, Any]] = []
            for i, row in enumerate(rows):
                try:
                    transaction = TransactionParser.parse_row(row)
                except Exception as e:
                    logger.warning(f"Error parsing row {i}: {str(e)}")
                    continue
                if transaction:
                    out.append(transaction)
            return out

        llm = llm or get_llm_client()
        total = len(rows)
        tasks = [
            asyncio.create_task(TransactionParser._parse_pdf_chunk_async(chunk, [], llm, with_category=False))
            for chunk in rows
        ]
        extracted: List[Dict[str, Any]] = []
        completed = 0
        for future in asyncio.as_completed(tasks):
            extracted.extend(await future)
            completed += 1
            if on_chunk_done is not None:
                on_chunk_done(completed, total)
        return extracted

    @staticmethod
    async def parse_transactions_async(
        rows: List[Dict[str, Any]],
        existing_categories: Optional[List[str]] = None,
        merchant_rules: Optional[List[Dict[str, str]]] = None,
        on_chunk_done: Optional[Any] = None,
        llm: Optional[LLMClient] = None,
    ) -> List[Dict[str, Any]]:
        """Parse extracted file rows into transactions using the configured LLM provider.

        PDF chunks are sent to the LLM in parallel via asyncio.as_completed (the provider caps
        its own concurrency). CSV/Excel rows are parsed locally and categorized in batches.
        on_chunk_done(completed, total) is called each time a PDF chunk finishes.
        """
        if existing_categories is None:
            existing_categories = []
        llm = llm or get_llm_client()

        if not (rows and all("pdf_text" in row for row in rows)):
            return await TransactionParser._parse_rows_async(rows, existing_categories, merchant_rules, llm)

        total = len(rows)
        logger.info(f"Parsing {total} PDF chunk(s) in parallel with {llm.name}")

        tasks = [
            asyncio.create_task(
                TransactionParser._parse_pdf_chunk_async(chunk, list(existing_categories), llm)
            )
            for chunk in rows
        ]

        all_transactions: List[Dict[str, Any]] = []
        completed = 0
        for future in asyncio.as_completed(tasks):
            chunk_txns = await future
            all_transactions.extend(chunk_txns)
            completed += 1
            if on_chunk_done is not None:
                on_chunk_done(completed, total)

        logger.info(f"PDF parsing complete: {len(all_transactions)} transactions extracted")
        return all_transactions

    @staticmethod
    def parse_row(
        row: Dict[str, Any],
        merchant_rules: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse a single row into transaction format.

        The category is the file's own category column, else the first matching merchant rule,
        else ``None``: the caller categorizes those rows in batch with the LLM.

        Args:
            row: Raw data row
            merchant_rules: Optional merchant pattern → category rules
            
        Returns:
            Transaction dictionary or None if parsing fails
        """
        # PDF chunks are parsed by the LLM, not row by row
        if "pdf_text" in row:
            return None
        
        # Handle structured data (CSV/Excel). Headings: English and the ones Italian banks use (columns.py)
        date_fields, description_fields, amount_fields = DATE_COLUMNS, DESCRIPTION_COLUMNS, AMOUNT_COLUMNS
        currency_fields, category_fields = CURRENCY_COLUMNS, CATEGORY_COLUMNS
        
        # Find fields (case-insensitive)
        row_lower = {k.lower().strip(): v for k, v in row.items() if v is not None}
        
        date = None
        for field in date_fields:
            if field in row_lower:
                date = TransactionParser.parse_date(row_lower[field])
                if date:
                    break
        
        if not date:
            return None
        
        description = None
        for field in description_fields:
            if field in row_lower:
                description = str(row_lower[field]).strip()
                if description and description.lower() not in ['nan', 'none', '']:
                    break
        
        if not description:
            description = "Transaction"
        
        amount = None
        # Banks with separate "out" and "in" columns (uscite/entrate, dare/avere): in minus out
        def filled(value) -> bool:
            return str(value).strip().lower() not in ("", "nan", "none")

        out_value = next((row_lower[f] for f in DEBIT_COLUMNS if f in row_lower and filled(row_lower[f]) and TransactionParser.parse_amount(row_lower[f]) is not None), None)
        in_value = next((row_lower[f] for f in CREDIT_COLUMNS if f in row_lower and filled(row_lower[f]) and TransactionParser.parse_amount(row_lower[f]) is not None), None)
        if out_value is not None or in_value is not None:
            money_in = abs(TransactionParser.parse_amount(in_value) or 0.0) if in_value is not None else 0.0
            money_out = abs(TransactionParser.parse_amount(out_value) or 0.0) if out_value is not None else 0.0
            amount = money_in - money_out
        for field in amount_fields:
            if amount is not None:
                break
            if field in row_lower:
                amount = TransactionParser.parse_amount(row_lower[field])
        
        if amount is None:
            return None
        
        # Extract currency
        currency = "EUR"
        for field in currency_fields:
            if field in row_lower:
                currency_value = str(row_lower[field]).upper().strip()
                if len(currency_value) == 3:
                    currency = currency_value
                    break
        
        # Extract category if provided, otherwise categorize
        category = None
        for field in category_fields:
            if field in row_lower:
                category_value = str(row_lower[field]).strip()
                if category_value and category_value.lower() not in ['nan', 'none', '']:
                    category = category_value.lower()
                    break
        
        if not category:
            category = TransactionParser._match_merchant_rule(description, merchant_rules)
        
        return {
            "date": date,
            "description": description,
            "amount": amount,
            "currency": currency,
            "category": category
        }
