#!/usr/bin/env python3
"""
Demonstration script for the monthly-based personal finance data model.

This script demonstrates:
1. Creating accounts
2. Creating monthly snapshots (source of truth for balances)
3. Adding optional transactions (for detailed views)
4. Querying key metrics as specified in the problem statement
"""

import os
import sys
from datetime import date

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Base, Account, MonthlyAccountSnapshot, Category, Transaction
from src.business_logic.snapshot_service import SnapshotService


def create_demo_database():
    """Create an in-memory SQLite database for demonstration."""
    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def main():
    """Run the demonstration."""
    print("=" * 70)
    print("Monthly-Based Personal Finance Data Model Demonstration")
    print("=" * 70)
    print()
    
    # Create database session
    db_session = create_demo_database()
    service = SnapshotService(db_session)
    
    # 1. Create Accounts
    print("1. Creating Accounts...")
    print("-" * 70)
    
    checking = service.create_account(
        name="Main Checking",
        account_type="checking",
        currency="USD"
    )
    print(f"   Created: {checking['name']} (ID: {checking['id']})")
    
    savings = service.create_account(
        name="Emergency Savings",
        account_type="savings",
        currency="USD"
    )
    print(f"   Created: {savings['name']} (ID: {savings['id']})")
    
    credit_card = service.create_account(
        name="Credit Card",
        account_type="credit",
        currency="USD"
    )
    print(f"   Created: {credit_card['name']} (ID: {credit_card['id']})")
    print()
    
    # 2. Create Monthly Snapshots (CORE - Source of Truth)
    print("2. Creating Monthly Snapshots (Source of Truth for Balances)...")
    print("-" * 70)
    
    # December 2025 snapshots
    dec_checking = service.create_snapshot(
        account_id=checking['id'],
        year=2025,
        month=12,
        starting_balance=2000.00,
        ending_balance=2500.00,
        total_income=3000.00,
        total_expense=2500.00
    )
    print(f"   Dec 2025 - {checking['name']}: ${dec_checking['ending_balance']:.2f}")
    
    dec_savings = service.create_snapshot(
        account_id=savings['id'],
        year=2025,
        month=12,
        starting_balance=8000.00,
        ending_balance=8500.00,
        total_income=500.00,
        total_expense=0.00
    )
    print(f"   Dec 2025 - {savings['name']}: ${dec_savings['ending_balance']:.2f}")
    
    dec_credit = service.create_snapshot(
        account_id=credit_card['id'],
        year=2025,
        month=12,
        starting_balance=-500.00,
        ending_balance=-300.00,
        total_income=0.00,
        total_expense=800.00
    )
    print(f"   Dec 2025 - {credit_card['name']}: ${dec_credit['ending_balance']:.2f}")
    
    # January 2026 snapshots (current month)
    jan_checking = service.create_snapshot(
        account_id=checking['id'],
        year=2026,
        month=1,
        starting_balance=2500.00,
        ending_balance=3200.00,
        total_income=3500.00,
        total_expense=2800.00
    )
    print(f"   Jan 2026 - {checking['name']}: ${jan_checking['ending_balance']:.2f}")
    
    jan_savings = service.create_snapshot(
        account_id=savings['id'],
        year=2026,
        month=1,
        starting_balance=8500.00,
        ending_balance=9000.00,
        total_income=500.00,
        total_expense=0.00
    )
    print(f"   Jan 2026 - {savings['name']}: ${jan_savings['ending_balance']:.2f}")
    
    jan_credit = service.create_snapshot(
        account_id=credit_card['id'],
        year=2026,
        month=1,
        starting_balance=-300.00,
        ending_balance=-100.00,
        total_income=0.00,
        total_expense=700.00
    )
    print(f"   Jan 2026 - {credit_card['name']}: ${jan_credit['ending_balance']:.2f}")
    print()
    
    # 3. Create Categories
    print("3. Creating Categories...")
    print("-" * 70)
    
    food_cat = service.create_category(
        name="Food & Dining",
        category_type="expense",
        color="#FF5733"
    )
    print(f"   Created: {food_cat['name']} ({food_cat['type']})")
    
    salary_cat = service.create_category(
        name="Salary",
        category_type="income",
        color="#28A745"
    )
    print(f"   Created: {salary_cat['name']} ({salary_cat['type']})")
    
    transport_cat = service.create_category(
        name="Transportation",
        category_type="expense",
        color="#FFC107"
    )
    print(f"   Created: {transport_cat['name']} ({transport_cat['type']})")
    print()
    
    # 4. Add Optional Transactions (for detailed views)
    print("4. Adding Optional Transactions (Detailed Views Only)...")
    print("-" * 70)
    print("   Note: Transactions do NOT affect balances!")
    print("   Balances come from snapshots above.")
    print()
    
    trans1 = service.add_transaction(
        account_id=checking['id'],
        date=date(2026, 1, 5),
        amount=-45.50,
        category="Food & Dining",
        description="Grocery shopping",
        currency="USD"
    )
    print(f"   {trans1['date']}: {trans1['description']} - ${trans1['amount']}")
    
    trans2 = service.add_transaction(
        account_id=checking['id'],
        date=date(2026, 1, 10),
        amount=3500.00,
        category="Salary",
        description="Monthly salary",
        currency="USD"
    )
    print(f"   {trans2['date']}: {trans2['description']} - ${trans2['amount']}")
    
    trans3 = service.add_transaction(
        account_id=credit_card['id'],
        date=date(2026, 1, 8),
        amount=-120.00,
        category="Transportation",
        description="Gas for car",
        currency="USD"
    )
    print(f"   {trans3['date']}: {trans3['description']} - ${trans3['amount']}")
    print()
    
    # 5. Demonstrate Key Queries (As Specified in Problem Statement)
    print("5. Key Queries (As Specified in Problem Statement)...")
    print("=" * 70)
    print()
    
    # Query 1: Current total balance (most recent snapshot of each account)
    current_balance = service.get_current_total_balance()
    print(f"📊 Total Balance Today (Current Month):")
    print(f"   ${current_balance:.2f}")
    print(f"   → Calculated from: Sum of ending_balance from Jan 2026 snapshots")
    print()
    
    # Query 2: Total balance last month
    last_month_balance = service.get_total_balance_for_month(2025, 12)
    print(f"📊 Total Balance Last Month (Dec 2025):")
    print(f"   ${last_month_balance:.2f}")
    print(f"   → Calculated from: Sum of ending_balance from Dec 2025 snapshots")
    print()
    
    # Query 3: Total expenses for current month
    current_expenses = service.get_total_expenses_for_month(2026, 1)
    print(f"📊 Total Expenses This Month (Jan 2026):")
    print(f"   ${current_expenses:.2f}")
    print(f"   → Calculated from: Sum of total_expense from Jan 2026 snapshots")
    print()
    
    # Query 4: Total income for current month
    current_income = service.get_total_income_for_month(2026, 1)
    print(f"📊 Total Income This Month (Jan 2026):")
    print(f"   ${current_income:.2f}")
    print(f"   → Calculated from: Sum of total_income from Jan 2026 snapshots")
    print()
    
    # Query 5: Balance trend over time
    print(f"📊 Balance Trend (All Accounts):")
    trend = service.get_balance_trend(num_months=6)
    for snapshot in trend:
        print(f"   {snapshot['year']}-{snapshot['month']:02d}: "
              f"Account {snapshot['account_id']} - "
              f"${snapshot['ending_balance']:.2f}")
    print()
    
    # 6. Verify Data Integrity
    print("6. Verify Data Integrity...")
    print("=" * 70)
    
    # Try to create duplicate snapshot (should fail)
    print("   Testing unique constraint on (account_id, year, month)...")
    try:
        service.create_snapshot(
            account_id=checking['id'],
            year=2026,
            month=1,
            starting_balance=0.0,
            ending_balance=0.0
        )
        print("   ❌ FAILED: Duplicate snapshot was allowed!")
    except ValueError as e:
        print(f"   ✓ PASSED: Duplicate snapshot prevented - {e}")
    print()
    
    # 7. Summary
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print()
    print("✓ Created 3 accounts")
    print("✓ Created 6 monthly snapshots (source of truth)")
    print("✓ Created 3 categories")
    print("✓ Added 3 transactions (optional detail)")
    print("✓ Demonstrated all key queries from problem statement:")
    print("  • Total balance today")
    print("  • Total balance last month")
    print("  • Total expenses for a month")
    print("  • Balance trend over time")
    print("✓ Verified data integrity constraints")
    print()
    print("The monthly-based data model is working correctly!")
    print("Snapshots are the source of truth, transactions are optional detail.")
    print()


if __name__ == "__main__":
    main()
