"""Transaction parser service for converting extracted data to transactions."""

import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

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
    def categorize_transaction(description: str, amount: float) -> str:
        """Categorize transaction based on description and amount.
        
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
        transport_keywords = ['gas', 'fuel', 'uber', 'taxi', 'bus', 'train', 'parking', 'transport']
        shopping_keywords = ['amazon', 'shop', 'store', 'retail', 'purchase']
        utilities_keywords = ['electric', 'water', 'gas', 'internet', 'phone', 'utility']
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
    def parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a single row into transaction format.
        
        Args:
            row: Raw data row
            
        Returns:
            Transaction dictionary or None if parsing fails
        """
        # Handle PDF raw text
        if "raw_text" in row:
            # Try to parse the raw text line
            # This is a simplified parser - in production, you'd use more sophisticated NLP
            text = row["raw_text"]
            
            # Try to find date pattern
            date_match = re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text)
            if not date_match:
                return None
            
            date = TransactionParser.parse_date(date_match.group())
            
            # Try to find amount pattern
            amount_match = re.search(r'[-+]?\$?\€?\£?[\d,]+\.?\d*', text)
            if not amount_match:
                return None
            
            amount = TransactionParser.parse_amount(amount_match.group())
            if amount is None:
                return None
            
            # Description is the remaining text
            description = text.replace(date_match.group(), '').replace(amount_match.group(), '').strip()
            if not description:
                description = "Transaction"
            
            currency = TransactionParser.extract_currency(text)
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
            category = TransactionParser.categorize_transaction(description, amount)
        
        return {
            "date": date,
            "description": description,
            "amount": amount,
            "currency": currency,
            "category": category
        }
    
    @staticmethod
    def parse_transactions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse multiple rows into transactions.
        
        Args:
            rows: List of raw data rows
            
        Returns:
            List of parsed transactions
        """
        transactions = []
        
        for i, row in enumerate(rows):
            try:
                transaction = TransactionParser.parse_row(row)
                if transaction:
                    transactions.append(transaction)
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
