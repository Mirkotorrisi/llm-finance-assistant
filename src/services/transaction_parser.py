"""Transaction parser service for converting extracted data to transactions."""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class _ParsedTransaction(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str


class _ParsedTransactionList(BaseModel):
    transactions: List[_ParsedTransaction]


class _ParsedCategory(BaseModel):
    category: str
    is_new: bool
    reasoning: str


class TransactionParser:
    """Service for parsing extracted data into transaction format."""
    
    # Maximum description length for LLM categorization to prevent prompt injection
    MAX_DESCRIPTION_LENGTH = 500
    
    # Maximum characters per PDF chunk sent to the LLM.
    # Each chunk is already bounded by PAGES_PER_CHUNK pages; this is a safety cap.
    MAX_PDF_TEXT_LENGTH = 40000
    
    @staticmethod
    def _get_openai_client() -> Optional[OpenAI]:
        """Get or create OpenAI client.
        
        Note: This creates a new client instance each time rather than using a global
        singleton pattern. This approach is used because:
        1. The parser is typically used in batch processing contexts where a client
           is created once per batch in parse_transactions()
        2. It avoids global state management in a stateless service class
        3. It allows for easier testing with dependency injection
        
        Returns:
            OpenAI client instance or None if API key is not available
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            return OpenAI(api_key=api_key)
        except Exception as e:
            logger.warning(f"Failed to create OpenAI client: {str(e)}")
            return None
    
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
    def categorize_transaction_with_llm(
        description: str,
        amount: float,
        existing_categories: List[str],
        openai_client: Optional[OpenAI] = None,
        merchant_rules: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Categorize transaction using LLM based on description, amount, and existing categories.
        
        This method uses an LLM to intelligently assign categories to transactions.
        The LLM will either reuse an existing category or create a new one if appropriate.
        
        Args:
            description: Transaction description
            amount: Transaction amount (negative for expenses, positive for income)
            existing_categories: List of existing category labels to consider
            openai_client: Optional OpenAI client instance. If not provided, creates a new one.
            
        Returns:
            Category name (either existing or newly created)
        """
        # Check merchant rules before calling the LLM
        if merchant_rules:
            desc_lower = description.lower()
            for rule in merchant_rules:
                if rule["pattern"].lower() in desc_lower:
                    logger.debug(f"Merchant rule matched '{rule['pattern']}' → '{rule['category']}' for '{description}'")
                    return rule["category"]

        # Get or create OpenAI client
        if openai_client is None:
            openai_client = TransactionParser._get_openai_client()
            if openai_client is None:
                logger.warning("OPENAI_API_KEY not set or client creation failed, falling back to rule-based categorization")
                return TransactionParser._categorize_transaction_fallback(description, amount)

        # Prepare the prompt for the LLM
        transaction_type = "income" if amount > 0 else "expense"
        
        system_prompt = """You are a financial transaction categorization expert.
Your job is to assign a category label to a transaction based on its description and amount.

Rules:
1. If the transaction clearly fits into one of the existing categories, use that category exactly as it appears.
2. Only create a NEW category if the transaction doesn't fit well into any existing category.
3. Category labels should be short (1-2 words), lowercase, and descriptive.
4. Common categories: food, transport, shopping, utilities, rent, income, entertainment, health, education."""

        existing_categories_text = "None - this is the first transaction" if not existing_categories else ", ".join(existing_categories)

        sanitized_description = description.strip()[:TransactionParser.MAX_DESCRIPTION_LENGTH]
        sanitized_description = ''.join(char for char in sanitized_description if char.isprintable() or char.isspace())

        user_prompt = (
            f"Transaction details:\n"
            f"- Description: {sanitized_description}\n"
            f"- Amount: {amount}\n"
            f"- Type: {transaction_type}\n\n"
            f"Existing categories: {existing_categories_text}\n\n"
            f"Assign the most appropriate category for this transaction."
        )

        try:
            completion = openai_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=_ParsedCategory,
                temperature=0.3,
            )

            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError("Structured output returned None")

            if parsed.is_new:
                logger.info(f"LLM created new category '{parsed.category}' for '{sanitized_description}': {parsed.reasoning}")
            else:
                logger.debug(f"LLM assigned existing category '{parsed.category}' for '{sanitized_description}'")
            
            return parsed.category.lower().strip()

        except Exception as e:
            logger.warning(f"LLM categorization failed: {str(e)}, falling back to rule-based categorization")
            return TransactionParser._categorize_transaction_fallback(description, amount)
    
    @staticmethod
    def _categorize_transaction_fallback(description: str, amount: float) -> str:
        """Fallback categorization using simple rules (used when LLM is unavailable).
        
        This is the original hardcoded categorization logic, kept as a fallback.
        
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
    def categorize_transaction(description: str, amount: float) -> str:
        """Categorize transaction based on description and amount.
        
        DEPRECATED: This method uses hardcoded rules. Use categorize_transaction_with_llm instead.
        Kept for backward compatibility.
        
        Args:
            description: Transaction description
            amount: Transaction amount
            
        Returns:
            Category name
        """
        return TransactionParser._categorize_transaction_fallback(description, amount)
    
    @staticmethod
    def parse_pdf_with_llm(
        pdf_text: str,
        existing_categories: Optional[List[str]] = None,
        openai_client: Optional[OpenAI] = None
    ) -> List[Dict[str, Any]]:
        """Parse PDF text using LLM to extract transaction data.
        
        Args:
            pdf_text: Full text content from PDF
            existing_categories: List of existing category labels
            openai_client: Optional OpenAI client instance
            
        Returns:
            List of parsed transactions
        """
        if existing_categories is None:
            existing_categories = []
        
        # Get or create OpenAI client
        if openai_client is None:
            openai_client = TransactionParser._get_openai_client()
            if openai_client is None:
                logger.error("OPENAI_API_KEY not set or client creation failed, cannot parse PDF with LLM")
                return []
        
        system_prompt = """You are a financial document parsing expert.
Extract all financial transactions from the bank statement text provided.

Rules:
- Only extract clear, identifiable transactions
- Dates must be in ISO format (YYYY-MM-DD)
- Amounts are negative for expenses/debits, positive for income/credits
- Categories should be short and descriptive (e.g. food, transport, shopping, utilities, income)
- Default currency to EUR if not specified
- Reuse existing categories when appropriate"""

        existing_categories_text = "None - create appropriate categories" if not existing_categories else ", ".join(existing_categories)
        truncated_text = pdf_text[: TransactionParser.MAX_PDF_TEXT_LENGTH]

        user_prompt = (
            f"Bank statement text:\n\n{truncated_text}\n\n"
            f"Existing categories: {existing_categories_text}"
        )

        try:
            completion = openai_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=_ParsedTransactionList,
                temperature=0.1,
            )

            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError("Structured output returned None")

            valid_transactions = [t.model_dump() for t in parsed.transactions]
            logger.info(f"LLM extracted {len(valid_transactions)} transactions from PDF")
            return valid_transactions

        except Exception as e:
            logger.error(f"LLM-based PDF parsing failed: {str(e)}")
            return []
    
    @staticmethod
    async def _parse_pdf_chunk_async(
        chunk: Dict[str, Any],
        existing_categories: List[str],
        async_client: AsyncOpenAI,
    ) -> List[Dict[str, Any]]:
        """Parse a single PDF chunk via AsyncOpenAI (used by parse_transactions_async)."""
        label = chunk.get("pages", f"chunk {chunk.get('chunk_index', '?')}")
        pdf_text = chunk["pdf_text"]
        truncated = pdf_text[: TransactionParser.MAX_PDF_TEXT_LENGTH]

        existing_categories_text = (
            "None - create appropriate categories"
            if not existing_categories
            else ", ".join(existing_categories)
        )

        system_prompt = """You are a financial document parsing expert.
Extract all financial transactions from the bank statement text provided.

Rules:
- Only extract clear, identifiable transactions
- Dates must be in ISO format (YYYY-MM-DD)
- Amounts are negative for expenses/debits, positive for income/credits
- Categories should be short and descriptive (e.g. food, transport, shopping, utilities, income)
- Default currency to EUR if not specified
- Reuse existing categories when appropriate"""

        user_prompt = (
            f"Bank statement text:\n\n{truncated}\n\n"
            f"Existing categories: {existing_categories_text}"
        )

        try:
            completion = await async_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=_ParsedTransactionList,
                temperature=0.1,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError("Structured output returned None")
            valid = [t.model_dump() for t in parsed.transactions]
            logger.info(f"Chunk {label}: {len(valid)} transactions extracted")
            return valid
        except Exception as e:
            logger.warning(f"Skipped chunk {label}: {e}")
            return []

    @staticmethod
    async def parse_transactions_async(
        rows: List[Dict[str, Any]],
        existing_categories: Optional[List[str]] = None,
        merchant_rules: Optional[List[Dict[str, str]]] = None,
        on_chunk_done: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Async version of parse_transactions for PDF chunks.

        All chunks are sent to OpenAI in parallel via asyncio.as_completed — total time
        equals the slowest single call instead of the sum of all calls.
        Falls back to the sync path for CSV/Excel rows.
        on_chunk_done(completed, total) is called each time a chunk finishes.
        """
        if existing_categories is None:
            existing_categories = []

        if not (rows and all("pdf_text" in row for row in rows)):
            # CSV/Excel: run the sync path in a thread to avoid blocking the event loop
            return await asyncio.to_thread(
                TransactionParser.parse_transactions, rows, existing_categories, merchant_rules
            )

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not set — cannot parse PDF")
            return []

        async_client = AsyncOpenAI(api_key=api_key)
        total = len(rows)
        logger.info(f"Parsing {total} PDF chunk(s) in parallel")

        tasks = [
            asyncio.create_task(
                TransactionParser._parse_pdf_chunk_async(chunk, list(existing_categories), async_client)
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
        existing_categories: Optional[List[str]] = None,
        openai_client: Optional[OpenAI] = None,
        merchant_rules: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse a single row into transaction format.
        
        Args:
            row: Raw data row
            existing_categories: List of existing category labels (for LLM categorization)
            openai_client: Optional OpenAI client instance
            
        Returns:
            Transaction dictionary or None if parsing fails
        """
        if existing_categories is None:
            existing_categories = []
            
        # Handle PDF text - this should now be handled by parse_pdf_with_llm
        if "pdf_text" in row:
            # This is now handled by parse_pdf_with_llm instead
            logger.debug("PDF text detected, should be parsed with parse_pdf_with_llm")
            return None
        
        # Handle structured data (CSV/Excel)
        # Try to find date, description, and amount fields with various common names
        date_fields = ['date', 'transaction date', 'booking date', 'value date']
        description_fields = ['description', 'details', 'memo', 'narrative', 'transaction details']
        amount_fields = ['amount', 'value', 'debit', 'credit', 'transaction amount']
        currency_fields = ['currency', 'ccy']
        category_fields = ['category', 'type', 'transaction type']
        
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
        for field in amount_fields:
            if field in row_lower:
                amount = TransactionParser.parse_amount(row_lower[field])
                if amount is not None:
                    break
        
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
            category = TransactionParser.categorize_transaction_with_llm(
                description, amount, existing_categories, openai_client, merchant_rules
            )
        
        return {
            "date": date,
            "description": description,
            "amount": amount,
            "currency": currency,
            "category": category
        }
    
    @staticmethod
    def parse_transactions(
        rows: List[Dict[str, Any]],
        existing_categories: Optional[List[str]] = None,
        merchant_rules: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Parse multiple rows into transactions.
        
        Args:
            rows: List of raw data rows
            existing_categories: List of existing category labels (for LLM categorization)
            
        Returns:
            List of parsed transactions
        """
        if existing_categories is None:
            existing_categories = []
        
        # Detect PDF chunks: all rows have a "pdf_text" key
        if rows and all("pdf_text" in row for row in rows):
            total_chunks = len(rows)
            logger.info(f"Detected {total_chunks} PDF chunk(s) — processing with LLM")
            openai_client = TransactionParser._get_openai_client()
            all_transactions: List[Dict[str, Any]] = []

            for chunk in rows:
                label = chunk.get("pages", f"chunk {chunk.get('chunk_index', '?')}")
                logger.info(f"  Processing pages {label} ({total_chunks} chunks total)")
                try:
                    chunk_txns = TransactionParser.parse_pdf_with_llm(
                        chunk["pdf_text"],
                        existing_categories=existing_categories,
                        openai_client=openai_client,
                    )
                    all_transactions.extend(chunk_txns)
                    # Share discovered categories with subsequent chunks so the LLM
                    # reuses existing labels rather than inventing new ones
                    for txn in chunk_txns:
                        cat = txn.get("category")
                        if cat and cat not in existing_categories:
                            existing_categories.append(cat)
                except Exception as e:
                    logger.warning(f"  Skipped pages {label}: {e}")

            logger.info(f"PDF parsing complete: {len(all_transactions)} transactions extracted")
            return all_transactions
        
        transactions = []
        
        # Create a single OpenAI client to reuse across all transactions
        openai_client = None
        openai_client = TransactionParser._get_openai_client()
        
        for i, row in enumerate(rows):
            try:
                transaction = TransactionParser.parse_row(
                    row,
                    existing_categories=existing_categories,
                    openai_client=openai_client,
                    merchant_rules=merchant_rules,
                )
                if transaction:
                    transactions.append(transaction)
                    # Add new category to existing_categories for next iterations
                    if transaction["category"] not in existing_categories:
                        existing_categories.append(transaction["category"])
                else:
                    logger.debug(f"Skipped row {i}: Could not parse")
            except Exception as e:
                logger.warning(f"Error parsing row {i}: {str(e)}")
        
        logger.info(f"Parsed {len(transactions)} transactions from {len(rows)} rows")
        
        return transactions
    
    @staticmethod
    def remove_duplicates(
        transactions: List[Dict[str, Any]], 
        existing_transactions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove duplicate transactions.
        
        Args:
            transactions: New transactions to check
            existing_transactions: Existing transactions in the system
            
        Returns:
            List of unique transactions
        """
        unique_transactions = []
        
        for transaction in transactions:
            is_duplicate = False
            
            for existing in existing_transactions:
                # Check if transaction matches on date, amount, and description
                if (transaction["date"] == existing.get("date") and
                    abs(transaction["amount"] - existing.get("amount", 0)) < 0.01 and
                    transaction["description"] == existing.get("description")):
                    is_duplicate = True
                    logger.debug(f"Skipped duplicate transaction: {transaction}")
                    break
            
            if not is_duplicate:
                unique_transactions.append(transaction)
        
        logger.info(
            f"Removed {len(transactions) - len(unique_transactions)} duplicates, "
            f"{len(unique_transactions)} unique transactions remaining"
        )
        
        return unique_transactions
