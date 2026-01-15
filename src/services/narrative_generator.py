"""Narrative generation service for financial summaries.

This service generates human-readable narrative documents from SQL data.
These narratives are the ONLY data embedded in the vector store.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from src.services.aggregation_service import AggregationService

logger = logging.getLogger(__name__)

# Threshold for category summary generation (in EUR)
MIN_CATEGORY_AMOUNT = 100.0


class NarrativeGenerator:
    """Service for generating narrative financial summaries.
    
    This service:
    - Takes SQL aggregates and converts them to readable narratives
    - Produces documents suitable for embedding
    - Never includes raw transaction data
    """
    
    def __init__(self, db_session: Optional[Session] = None):
        """Initialize the narrative generator.
        
        Args:
            db_session: SQLAlchemy session. If None, AggregationService creates one.
        """
        self.aggregation_service = AggregationService(db_session)
    
    def close(self):
        """Close underlying services."""
        self.aggregation_service.close()
    
    def generate_monthly_summary(self, year: int, month: int) -> Optional[Dict[str, Any]]:
        """Generate a monthly financial summary narrative.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Document dict with text, type, metadata, or None if no data
        """
        try:
            # Get SQL aggregates
            totals = self.aggregation_service.get_monthly_totals(year, month)
            net_worth = self.aggregation_service.get_net_worth(year, month)
            deltas = self.aggregation_service.get_month_over_month_delta(year, month)
            
            # Skip if no meaningful data
            if totals["total_income"] == 0 and totals["total_expense"] == 0:
                return None
            
            # Format month name
            month_name = datetime(year, month, 1).strftime("%B")
            
            # Build narrative text
            text_parts = []
            
            # Main summary
            text_parts.append(
                f"In {month_name} {year}, total expenses were €{abs(totals['total_expense']):.2f} "
                f"and total income was €{totals['total_income']:.2f}, "
                f"resulting in net savings of €{totals['net_savings']:.2f}."
            )
            
            # Month-over-month comparison
            if deltas["previous_month"]:
                prev_month_name = datetime(deltas["previous_year"], deltas["previous_month"], 1).strftime("%B")
                
                if deltas["expense_delta"] > 0:
                    text_parts.append(
                        f"Expenses increased by €{abs(deltas['expense_delta']):.2f} "
                        f"({deltas['expense_pct_change']:.1f}%) compared to {prev_month_name}."
                    )
                elif deltas["expense_delta"] < 0:
                    text_parts.append(
                        f"Expenses decreased by €{abs(deltas['expense_delta']):.2f} "
                        f"({abs(deltas['expense_pct_change']):.1f}%) compared to {prev_month_name}."
                    )
                
                # Net worth change
                if deltas["net_worth_delta"] > 0:
                    text_parts.append(
                        f"Net worth increased by €{deltas['net_worth_delta']:.2f} during the month."
                    )
                elif deltas["net_worth_delta"] < 0:
                    text_parts.append(
                        f"Net worth decreased by €{abs(deltas['net_worth_delta']):.2f} during the month."
                    )
            
            # Add net worth info
            text_parts.append(
                f"Month-end net worth was €{net_worth:.2f}."
            )
            
            text = " ".join(text_parts)
            
            return {
                "text": text,
                "type": "monthly_summary",
                "metadata": {
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "total_income": totals["total_income"],
                    "total_expense": totals["total_expense"],
                    "net_savings": totals["net_savings"],
                    "net_worth": net_worth
                }
            }
        except Exception as e:
            logger.error(f"Error generating monthly summary for {year}-{month}: {str(e)}")
            return None
    
    def generate_category_summary(self, year: int, category: str) -> Optional[Dict[str, Any]]:
        """Generate a category summary for the year.
        
        Args:
            year: Year (YYYY)
            category: Category name
            
        Returns:
            Document dict with text, type, metadata, or None if no data
        """
        try:
            # Get yearly category data
            all_categories = self.aggregation_service.get_category_aggregates(year)
            
            # Find the specific category
            category_data = next(
                (c for c in all_categories if c["category"].lower() == category.lower()),
                None
            )
            
            if not category_data or category_data["total"] == 0:
                return None
            
            # Get monthly breakdown for this category
            monthly_amounts = []
            peak_month = None
            peak_amount = 0
            
            for month in range(1, 13):
                month_categories = self.aggregation_service.get_category_aggregates(year, month)
                month_data = next(
                    (c for c in month_categories if c["category"].lower() == category.lower()),
                    None
                )
                
                if month_data:
                    amount = abs(month_data["total"])
                    monthly_amounts.append((month, amount))
                    if amount > peak_amount:
                        peak_amount = amount
                        peak_month = month
            
            # Calculate percentage of total spending
            total_expenses = sum(abs(c["total"]) for c in all_categories if c["total"] < 0)
            percentage = (abs(category_data["total"]) / total_expenses * 100) if total_expenses > 0 else 0
            
            # Build narrative
            text_parts = []
            
            text_parts.append(
                f"In {year}, the {category} category accounted for "
                f"€{abs(category_data['total']):.2f} in expenses"
            )
            
            if peak_month:
                peak_month_name = datetime(year, peak_month, 1).strftime("%B")
                text_parts.append(
                    f", with a peak in {peak_month_name} (€{peak_amount:.2f})"
                )
            
            text_parts.append(
                f". This category represents {percentage:.1f}% of total yearly spending."
            )
            
            text = "".join(text_parts)
            
            return {
                "text": text,
                "type": "category_summary",
                "metadata": {
                    "year": year,
                    "category": category,
                    "total": category_data["total"],
                    "count": category_data["count"],
                    "percentage_of_total": percentage,
                    "peak_month": peak_month,
                    "peak_amount": peak_amount
                }
            }
        except Exception as e:
            logger.error(f"Error generating category summary for {year}/{category}: {str(e)}")
            return None
    
    def generate_anomaly_summary(self, year: int, month: int) -> Optional[Dict[str, Any]]:
        """Generate an anomaly/highlight summary for unusual spending.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Document dict with text, type, metadata, or None if no anomalies
        """
        try:
            # Detect anomalies
            anomalies = self.aggregation_service.detect_anomalies(year, month)
            
            if not anomalies:
                return None
            
            # Get monthly totals to check if it's a high spending month
            totals = self.aggregation_service.get_monthly_totals(year, month)
            
            # Build narrative
            month_name = datetime(year, month, 1).strftime("%B")
            text_parts = []
            
            # If there are multiple anomalies, it might be a high spending month
            if len(anomalies) >= 3:
                text_parts.append(
                    f"{month_name} {year} showed unusually high spending "
                    f"(€{abs(totals['total_expense']):.2f} total)"
                )
            else:
                text_parts.append(
                    f"{month_name} {year} had notable spending anomalies"
                )
            
            # List top anomalies (max 3)
            top_anomalies = sorted(
                anomalies, 
                key=lambda x: x["deviation_pct"], 
                reverse=True
            )[:3]
            
            anomaly_descriptions = []
            for anomaly in top_anomalies:
                anomaly_descriptions.append(
                    f"{anomaly['category']} (€{abs(anomaly['current_amount']):.2f}, "
                    f"{anomaly['deviation_pct']:.0f}% above average)"
                )
            
            text_parts.append(", driven by ")
            text_parts.append(", ".join(anomaly_descriptions))
            text_parts.append(".")
            
            text = "".join(text_parts)
            
            return {
                "text": text,
                "type": "anomaly",
                "metadata": {
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "anomaly_count": len(anomalies),
                    "anomalies": anomalies
                }
            }
        except Exception as e:
            logger.error(f"Error generating anomaly summary for {year}-{month}: {str(e)}")
            return None
    
    def generate_all_documents(self, year: int) -> List[Dict[str, Any]]:
        """Generate all narrative documents for a year.
        
        Args:
            year: Year (YYYY)
            
        Returns:
            List of all generated narrative documents
        """
        documents = []
        
        try:
            # Generate monthly summaries
            logger.info(f"Generating monthly summaries for {year}...")
            for month in range(1, 13):
                doc = self.generate_monthly_summary(year, month)
                if doc:
                    documents.append(doc)
            
            logger.info(f"Generated {len(documents)} monthly summaries")
            
            # Generate category summaries
            logger.info(f"Generating category summaries for {year}...")
            categories = self.aggregation_service.get_category_aggregates(year)
            
            # Only generate for expense categories with meaningful spending
            for cat_data in categories:
                if cat_data["total"] < 0 and abs(cat_data["total"]) > MIN_CATEGORY_AMOUNT:
                    doc = self.generate_category_summary(year, cat_data["category"])
                    if doc:
                        documents.append(doc)
            
            logger.info(f"Total documents with categories: {len(documents)}")
            
            # Generate anomaly summaries
            logger.info(f"Generating anomaly summaries for {year}...")
            for month in range(1, 13):
                doc = self.generate_anomaly_summary(year, month)
                if doc:
                    documents.append(doc)
            
            logger.info(f"Generated {len(documents)} total documents for {year}")
            
            return documents
            
        except Exception as e:
            logger.error(f"Error generating all documents for {year}: {str(e)}")
            return documents
    
    def generate_yearly_overview(self, year: int) -> Optional[Dict[str, Any]]:
        """Generate a high-level yearly overview narrative.
        
        Args:
            year: Year (YYYY)
            
        Returns:
            Document dict with text, type, metadata, or None if no data
        """
        try:
            yearly_summary = self.aggregation_service.get_yearly_summary(year)
            
            if yearly_summary["total_income"] == 0 and yearly_summary["total_expense"] == 0:
                return None
            
            # Build narrative
            text_parts = []
            
            text_parts.append(
                f"In {year}, total income was €{yearly_summary['total_income']:.2f} "
                f"and total expenses were €{abs(yearly_summary['total_expense']):.2f}, "
                f"resulting in net savings of €{yearly_summary['net_savings']:.2f}."
            )
            
            # Add top expense categories
            if yearly_summary["top_expense_categories"]:
                top_cats = yearly_summary["top_expense_categories"][:3]
                cat_descriptions = [
                    f"{c['category']} (€{abs(c['total']):.2f})"
                    for c in top_cats
                ]
                text_parts.append(
                    f" The largest expense categories were {', '.join(cat_descriptions)}."
                )
            
            # Find best and worst months
            monthly = yearly_summary["monthly_data"]
            best_month = max(
                (m for m in monthly if m["net_savings"] != 0),
                key=lambda x: x["net_savings"],
                default=None
            )
            worst_month = min(
                (m for m in monthly if m["net_savings"] != 0),
                key=lambda x: x["net_savings"],
                default=None
            )
            
            if best_month:
                best_name = datetime(year, best_month["month"], 1).strftime("%B")
                text_parts.append(
                    f" The best month was {best_name} with net savings of €{best_month['net_savings']:.2f}."
                )
            
            if worst_month and worst_month != best_month:
                worst_name = datetime(year, worst_month["month"], 1).strftime("%B")
                text_parts.append(
                    f" {worst_name} had the lowest savings (€{worst_month['net_savings']:.2f})."
                )
            
            text = "".join(text_parts)
            
            return {
                "text": text,
                "type": "yearly_overview",
                "metadata": {
                    "year": year,
                    "total_income": yearly_summary["total_income"],
                    "total_expense": yearly_summary["total_expense"],
                    "net_savings": yearly_summary["net_savings"]
                }
            }
        except Exception as e:
            logger.error(f"Error generating yearly overview for {year}: {str(e)}")
            return None
