"""Script to seed initial data into the database."""

import os
from datetime import date, timedelta

def seed_initial_data():
    """Seed the database with initial transaction data."""
    print("=" * 60)
    print("Database Seeding Script")
    print("=" * 60)
    
    # Ensure we're using database
    os.environ["USE_DATABASE"] = "true"
    
    try:
        from src.database.init import init_database
        from src.business_logic.mcp_database import FinanceMCPDatabase
        from src.business_logic.data import get_initial_data
        
        print("\n1. Initializing database...")
        init_database()
        
        print("\n2. Creating MCP instance...")
        mcp = FinanceMCPDatabase()
        
        print("\n3. Checking existing transactions...")
        existing = mcp.list_transactions()
        if existing:
            print(f"   Found {len(existing)} existing transactions")
            response = input("   Do you want to add sample data anyway? (y/n): ")
            if response.lower() != 'y':
                print("   Skipping seed data")
                return
        
        print("\n4. Adding initial transaction data...")
        initial_data = get_initial_data()
        
        added_count = 0
        for transaction in initial_data:
            mcp.add_transaction(
                amount=transaction["amount"],
                category=transaction["category"],
                description=transaction["description"],
                date=transaction["date"],
                currency=transaction.get("currency", "EUR")
            )
            added_count += 1
            print(f"   ✓ Added: {transaction['description']} ({transaction['amount']})")
        
        print(f"\n5. Successfully added {added_count} transactions")
        
        print("\n6. Current balance:", mcp.get_balance())
        
        print("\n7. Categories:", mcp.get_existing_categories())
        
        print("\n" + "=" * 60)
        print("Seeding completed successfully! ✓")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if not os.getenv("DB_PASSWORD"):
        print("ERROR: DB_PASSWORD environment variable not set")
        print("Please set it in your .env file")
        exit(1)
    
    seed_initial_data()
