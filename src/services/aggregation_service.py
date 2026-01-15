"""SQL-first aggregation service for financial data.

This service provides SQL-based aggregations and computations.
It is the ONLY source of truth for numerical data.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from src.database.models import MonthlyAccountSnapshot, Transaction, Account
from src.database.init import get_db_session

logger = logging.getLogger(__name__)


class AggregationService:
    """Service for SQL-based financial aggregations.
    
    This service:
    - Computes all numerical aggregates from SQL
    - Never relies on cached or embedded data
    - Is the single source of truth for calculations
    """
    
    def __init__(self, db_session: Optional[Session] = None):
        """Initialize the aggregation service.
        
        Args:
            db_session: SQLAlchemy session. If None, creates a new one.
        """
        self.db = db_session if db_session else get_db_session()
        self.owns_session = db_session is None
    
    def close(self):
        """Close the database session if we own it."""
        if self.owns_session and self.db:
            self.db.close()
            self.owns_session = False
    
    def __del__(self):
        """Cleanup when object is destroyed."""
        if hasattr(self, 'owns_session') and self.owns_session and hasattr(self, 'db') and self.db:
            try:
                self.db.close()
            except Exception:
                pass
    
    def get_monthly_totals(self, year: int, month: int) -> Dict[str, float]:
        """Get monthly income/expense totals from MonthlyAccountSnapshot.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Dict with total_income, total_expense, net_savings
        """
        try:
            snapshots = self.db.query(MonthlyAccountSnapshot).filter(
                and_(
                    MonthlyAccountSnapshot.year == year,
                    MonthlyAccountSnapshot.month == month
                )
            ).all()
            
            total_income = sum(s.total_income for s in snapshots)
            total_expense = sum(s.total_expense for s in snapshots)
            net_savings = total_income - total_expense
            
            return {
                "total_income": total_income,
                "total_expense": total_expense,
                "net_savings": net_savings
            }
        except Exception as e:
            logger.error(f"Error getting monthly totals for {year}-{month}: {str(e)}")
            return {
                "total_income": 0.0,
                "total_expense": 0.0,
                "net_savings": 0.0
            }
    
    def get_net_worth(self, year: int, month: int) -> float:
        """Get net worth for a specific month.
        
        Net worth is the sum of all account ending balances for the month.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Total net worth
        """
        try:
            result = self.db.query(
                func.sum(MonthlyAccountSnapshot.ending_balance)
            ).filter(
                and_(
                    MonthlyAccountSnapshot.year == year,
                    MonthlyAccountSnapshot.month == month
                )
            ).scalar()
            
            return result if result is not None else 0.0
        except Exception as e:
            logger.error(f"Error getting net worth for {year}-{month}: {str(e)}")
            return 0.0
    
    def get_category_aggregates(
        self, 
        year: int, 
        month: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get category-wise spending aggregates.
        
        Args:
            year: Year (YYYY)
            month: Optional month (1-12). If None, aggregates for whole year.
            
        Returns:
            List of dicts with category, total, transaction_count
        """
        try:
            query = self.db.query(
                Transaction.category,
                func.sum(Transaction.amount).label('total'),
                func.count(Transaction.id).label('count')
            ).filter(
                func.extract('year', Transaction.date) == year
            )
            
            if month is not None:
                query = query.filter(
                    func.extract('month', Transaction.date) == month
                )
            
            query = query.group_by(Transaction.category).order_by(
                func.sum(Transaction.amount).desc()
            )
            
            results = query.all()
            
            return [
                {
                    "category": r.category,
                    "total": float(r.total),
                    "count": r.count
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error getting category aggregates: {str(e)}")
            return []
    
    def get_month_over_month_delta(self, year: int, month: int) -> Dict[str, Any]:
        """Get month-over-month changes in key metrics.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Dict with income_delta, expense_delta, net_worth_delta, percentage changes
        """
        try:
            # Get current month data
            current = self.get_monthly_totals(year, month)
            current_net_worth = self.get_net_worth(year, month)
            
            # Get previous month data
            if month == 1:
                prev_year = year - 1
                prev_month = 12
            else:
                prev_year = year
                prev_month = month - 1
            
            previous = self.get_monthly_totals(prev_year, prev_month)
            prev_net_worth = self.get_net_worth(prev_year, prev_month)
            
            # Calculate deltas
            income_delta = current["total_income"] - previous["total_income"]
            expense_delta = current["total_expense"] - previous["total_expense"]
            net_worth_delta = current_net_worth - prev_net_worth
            
            # Calculate percentage changes (avoid division by zero)
            income_pct = (
                (income_delta / previous["total_income"] * 100) 
                if previous["total_income"] > 0 else 0.0
            )
            expense_pct = (
                (expense_delta / previous["total_expense"] * 100)
                if previous["total_expense"] > 0 else 0.0
            )
            net_worth_pct = (
                (net_worth_delta / prev_net_worth * 100)
                if prev_net_worth > 0 else 0.0
            )
            
            return {
                "income_delta": income_delta,
                "expense_delta": expense_delta,
                "net_worth_delta": net_worth_delta,
                "income_pct_change": income_pct,
                "expense_pct_change": expense_pct,
                "net_worth_pct_change": net_worth_pct,
                "previous_month": prev_month,
                "previous_year": prev_year
            }
        except Exception as e:
            logger.error(f"Error calculating month-over-month delta: {str(e)}")
            return {
                "income_delta": 0.0,
                "expense_delta": 0.0,
                "net_worth_delta": 0.0,
                "income_pct_change": 0.0,
                "expense_pct_change": 0.0,
                "net_worth_pct_change": 0.0,
                "previous_month": None,
                "previous_year": None
            }
    
    def detect_anomalies(
        self, 
        year: int, 
        month: int, 
        threshold_multiplier: float = 1.5
    ) -> List[Dict[str, Any]]:
        """Detect spending anomalies compared to historical average.
        
        An anomaly is when a category's spending exceeds the historical average
        by the threshold multiplier.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            threshold_multiplier: Multiplier for anomaly detection (default 1.5x)
            
        Returns:
            List of anomalies with category, current_amount, average_amount, deviation
        """
        try:
            # Get current month category totals
            current_categories = self.get_category_aggregates(year, month)
            
            # Get historical averages (previous 6 months, including previous year if needed)
            historical_averages = {}
            
            for cat_data in current_categories:
                category = cat_data["category"]
                
                # Calculate date range for historical data (6 months back)
                from datetime import datetime, timedelta
                current_date = datetime(year, month, 1)
                six_months_ago = current_date - timedelta(days=180)
                
                # Query historical data (up to 6 months prior, may span years)
                historical = self.db.query(
                    func.avg(
                        func.sum(Transaction.amount)
                    ).label('avg_amount')
                ).filter(
                    Transaction.category == category,
                    Transaction.date >= six_months_ago.date(),
                    Transaction.date < current_date.date()
                ).group_by(
                    func.extract('year', Transaction.date),
                    func.extract('month', Transaction.date)
                ).subquery()
                
                # Get the average of monthly totals
                avg_result = self.db.query(
                    func.avg(historical.c.avg_amount)
                ).scalar()
                
                if avg_result:
                    historical_averages[category] = float(avg_result)
            
            # Detect anomalies (only for expenses - negative amounts)
            anomalies = []
            for cat_data in current_categories:
                category = cat_data["category"]
                current_amount = cat_data["total"]
                
                # Only check expenses (negative amounts)
                if current_amount >= 0:
                    continue
                
                # Get absolute value for comparison
                current_abs = abs(current_amount)
                
                if category in historical_averages:
                    avg_abs = abs(historical_averages[category])
                    
                    # Check if current spending exceeds threshold
                    if current_abs > avg_abs * threshold_multiplier:
                        deviation = ((current_abs - avg_abs) / avg_abs * 100)
                        anomalies.append({
                            "category": category,
                            "current_amount": current_amount,
                            "average_amount": -avg_abs,
                            "deviation_pct": deviation,
                            "is_high": True
                        })
            
            return anomalies
        except Exception as e:
            logger.error(f"Error detecting anomalies: {str(e)}")
            return []
    
    def get_yearly_summary(self, year: int) -> Dict[str, Any]:
        """Get complete yearly financial summary.
        
        Args:
            year: Year (YYYY)
            
        Returns:
            Dict with yearly totals, monthly breakdown, top categories
        """
        try:
            # Get all monthly data for the year
            monthly_data = []
            for month in range(1, 13):
                totals = self.get_monthly_totals(year, month)
                net_worth = self.get_net_worth(year, month)
                
                monthly_data.append({
                    "month": month,
                    "income": totals["total_income"],
                    "expense": totals["total_expense"],
                    "net_savings": totals["net_savings"],
                    "net_worth": net_worth
                })
            
            # Calculate yearly totals
            yearly_income = sum(m["income"] for m in monthly_data)
            yearly_expense = sum(m["expense"] for m in monthly_data)
            yearly_net_savings = yearly_income - yearly_expense
            
            # Get top expense categories
            categories = self.get_category_aggregates(year)
            expense_categories = [
                c for c in categories if c["total"] < 0
            ]
            top_expenses = sorted(
                expense_categories, 
                key=lambda x: abs(x["total"]), 
                reverse=True
            )[:5]
            
            return {
                "year": year,
                "total_income": yearly_income,
                "total_expense": yearly_expense,
                "net_savings": yearly_net_savings,
                "monthly_data": monthly_data,
                "top_expense_categories": top_expenses
            }
        except Exception as e:
            logger.error(f"Error getting yearly summary: {str(e)}")
            return {
                "year": year,
                "total_income": 0.0,
                "total_expense": 0.0,
                "net_savings": 0.0,
                "monthly_data": [],
                "top_expense_categories": []
            }
