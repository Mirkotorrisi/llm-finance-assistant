"""Transaction parser service for converting extracted data to transactions."""

import json
import logging
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class TransactionParser:
    """Service for parsing extracted data into transaction format."""
    
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
        openai_client: Optional[OpenAI] = None
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
        # Get or create OpenAI client
        if openai_client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.warning("OPENAI_API_KEY not set, falling back to rule-based categorization")
                return TransactionParser._categorize_transaction_fallback(description, amount)
            openai_client = OpenAI(api_key=api_key)
        
        # Prepare the prompt for the LLM
        transaction_type = "income" if amount > 0 else "expense"
        
        system_prompt = """You are a financial transaction categorization expert.
Your job is to assign a category label to a transaction based on its description and amount.

Rules:
1. If the transaction clearly fits into one of the existing categories, use that category exactly as it appears.
2. Only create a NEW category if the transaction doesn't fit well into any existing category.
3. Category labels should be:
   - Short (1-2 words)
   - Descriptive and clear
   - Lowercase
   - Consistent with similar transactions
4. Common categories include: food, transport, shopping, utilities, rent, income, entertainment, health, education, etc.

Respond with ONLY a JSON object in this format:
{
  "category": "the_category_name",
  "is_new": true/false,
  "reasoning": "brief explanation of why this category was chosen"
}"""

        existing_categories_text = "None - this is the first transaction" if not existing_categories else ", ".join(existing_categories)
        
        user_prompt = f"""Transaction details:
- Description: {description}
- Amount: {amount}
- Type: {transaction_type}

Existing categories: {existing_categories_text}

Please assign the most appropriate category for this transaction."""

        try:
            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3
            )
            
            response_text = completion.choices[0].message.content
            response_data = json.loads(response_text)
            
            category = response_data.get("category", "other")
            is_new = response_data.get("is_new", False)
            reasoning = response_data.get("reasoning", "")
            
            if is_new:
                logger.info(f"LLM created new category '{category}' for '{description}': {reasoning}")
            else:
                logger.debug(f"LLM assigned existing category '{category}' for '{description}'")
            
            return category.lower().strip()
            
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
    def parse_row(
        row: Dict[str, Any], 
        existing_categories: Optional[List[str]] = None,
        use_llm_categorization: bool = True,
        openai_client: Optional[OpenAI] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse a single row into transaction format.
        
        Args:
            row: Raw data row
            existing_categories: List of existing category labels (for LLM categorization)
            use_llm_categorization: Whether to use LLM for categorization (default: True)
            openai_client: Optional OpenAI client instance
            
        Returns:
            Transaction dictionary or None if parsing fails
        """
        if existing_categories is None:
            existing_categories = []
            
        # Handle PDF raw text
        if "raw_text" in row:
            # Try to parse the raw text line
            # This is a simplified parser - in production, you'd use more sophisticated NLP
            text = row["raw_text"]
            
            # Try to find date pattern
            date_match = re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text)
            if not date_match:
                return None
            
            date_str = date_match.group()
            date = TransactionParser.parse_date(date_str)
            
            # Try to find amount pattern
            amount_match = re.search(r'[-+]?\$?\€?\£?[\d,]+\.?\d*', text)
            if not amount_match:
                return None
            
            amount_str = amount_match.group()
            amount = TransactionParser.parse_amount(amount_str)
            if amount is None:
                return None
            
            # Description is the text with date and amount removed (use their positions)
            parts = []
            last_end = 0
            
            # Remove date part
            if date_match:
                parts.append(text[last_end:date_match.start()])
                last_end = date_match.end()
            
            # Add middle part (before amount)
            if amount_match:
                parts.append(text[last_end:amount_match.start()])
                last_end = amount_match.end()
            
            # Add remaining part
            parts.append(text[last_end:])
            
            description = ' '.join(parts).strip()
            if not description:
                description = "Transaction"
            
            currency = TransactionParser.extract_currency(text)
            
            # Use LLM categorization if enabled
            if use_llm_categorization:
                category = TransactionParser.categorize_transaction_with_llm(
                    description, amount, existing_categories, openai_client
                )
            else:
                category = TransactionParser.categorize_transaction(description, amount)
            
            return {
                "date": date,
                "description": description,
                "amount": amount,
                "currency": currency,
                "category": category
            }
        
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
            # Use LLM categorization if enabled
            if use_llm_categorization:
                category = TransactionParser.categorize_transaction_with_llm(
                    description, amount, existing_categories, openai_client
                )
            else:
                category = TransactionParser.categorize_transaction(description, amount)
        
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
        use_llm_categorization: bool = True
    ) -> List[Dict[str, Any]]:
        """Parse multiple rows into transactions.
        
        Args:
            rows: List of raw data rows
            existing_categories: List of existing category labels (for LLM categorization)
            use_llm_categorization: Whether to use LLM for categorization (default: True)
            
        Returns:
            List of parsed transactions
        """
        if existing_categories is None:
            existing_categories = []
        
        transactions = []
        
        # Create a single OpenAI client to reuse across all transactions
        openai_client = None
        if use_llm_categorization:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                try:
                    openai_client = OpenAI(api_key=api_key)
                except Exception as e:
                    logger.warning(f"Failed to create OpenAI client: {str(e)}, will use fallback categorization")
        
        for i, row in enumerate(rows):
            try:
                transaction = TransactionParser.parse_row(
                    row, 
                    existing_categories=existing_categories,
                    use_llm_categorization=use_llm_categorization,
                    openai_client=openai_client
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
