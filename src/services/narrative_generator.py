"""Narrative generation service for financial summaries.

This service generates human-readable narrative documents from pre-computed
financial data. Narratives are the data embedded in the vector store.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Threshold for category summary generation (in EUR)
MIN_CATEGORY_AMOUNT = 100.0


class NarrativeGenerator:
    """Service for generating narrative financial summaries.

    This service converts pre-computed financial data into readable narratives
    suitable for embedding in the vector store.
    """

    def close(self):
        """No-op: no resources to release."""

    def generate_monthly_summary(self, year: int, month: int, data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a monthly financial summary narrative from pre-computed data.

        Args:
            year: Year (YYYY)
            month: Month (1-12)
            data: Pre-computed monthly totals and net-worth dict. If None, returns None.

        Returns:
            Document dict with text, type, metadata, or None if no data.
        """
        if not data:
            return None

        try:
            total_income = data.get("total_income", 0)
            total_expense = data.get("total_expense", 0)
            net_savings = data.get("net_savings", 0)
            net_worth = data.get("net_worth", 0)

            if total_income == 0 and total_expense == 0:
                return None

            month_name = datetime(year, month, 1).strftime("%B")
            text = (
                f"In {month_name} {year}, total expenses were €{abs(total_expense):.2f} "
                f"and total income was €{total_income:.2f}, "
                f"resulting in net savings of €{net_savings:.2f}. "
                f"Month-end net worth was €{net_worth:.2f}."
            )

            return {
                "text": text,
                "type": "monthly_summary",
                "metadata": {
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "total_income": total_income,
                    "total_expense": total_expense,
                    "net_savings": net_savings,
                    "net_worth": net_worth,
                },
            }
        except Exception as e:
            logger.error(f"Error generating monthly summary for {year}-{month}: {str(e)}")
            return None

    def generate_all_documents(self, year: int) -> List[Dict[str, Any]]:
        """Generate all narrative documents for a year.

        Without a connected data source this method returns an empty list.
        Provide data via the individual generate_* helpers when data is
        available externally.

        Args:
            year: Year (YYYY)

        Returns:
            Empty list (no data source configured).
        """
        logger.info(
            f"generate_all_documents called for {year} – no data source available; "
            "returning empty list."
        )
        return []

