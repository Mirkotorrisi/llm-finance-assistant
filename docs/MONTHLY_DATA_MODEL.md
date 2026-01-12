# Monthly-Based Personal Finance Data Model

## Overview

This document describes the monthly-based personal finance data model implemented for this application. The design prioritizes **knowing how much money the user has now** and **comparing balances month-over-month**, inspired by a spreadsheet workflow.

## Core Design Principle

We adopt a **hybrid data model** where:

1. **Monthly snapshots are the source of truth for balances**
2. **Transactions are optional, granular details, not authoritative for totals**
3. **High-level numbers (balances, totals) must come from snapshots**, not from aggregating transactions at runtime

### Why This Model?

Traditional banking apps use transaction-only models where balances are calculated by summing all transactions. This approach:
- Requires real-time syncing and reconciliation
- Makes historical balance queries expensive
- Doesn't match how people mentally track finances (monthly budgets)

Our snapshot-based model:
- ✅ Matches spreadsheet mental model (one row per account per month)
- ✅ Makes balance queries instant and efficient
- ✅ Supports manual data entry and imports
- ✅ Allows optional transaction detail when needed
- ✅ Simplifies reconciliation and auditing

## Data Model

### Entity Relationship

```
Account (1) ──< (many) MonthlyAccountSnapshot
   │
   └──< (many) Transaction (optional)

Category (1) ──< (many) Transaction
```

## Entity Definitions

### 1. Account

Represents a financial account, stable over time.

**Fields:**
- `id` (Integer, Primary Key)
- `name` (String, Required) - e.g., "Main Checking", "Credit Card"
- `type` (String, Required) - e.g., "checking", "savings", "credit", "cash", "investment"
- `currency` (String, Default: "EUR") - ISO currency code
- `is_active` (Boolean, Default: True)
- `created_at` (DateTime)
- `updated_at` (DateTime)

**Relationships:**
- Has many `MonthlyAccountSnapshot` (cascade delete)
- Has many `Transaction` (cascade delete)

**Example:**
```python
{
    "id": 1,
    "name": "Main Checking",
    "type": "checking",
    "currency": "USD",
    "is_active": true
}
```

### 2. MonthlyAccountSnapshot (CORE ENTITY)

**This is the most important entity in the system.**

Represents the monthly financial state of an account. Maps 1:1 with a single row in a spreadsheet.

**Fields:**
- `id` (Integer, Primary Key)
- `account_id` (Integer, Foreign Key → Account, Required)
- `year` (Integer, Required) - YYYY format
- `month` (Integer, Required) - 1-12
- `starting_balance` (Float, Required) - Balance at start of month
- `ending_balance` (Float, Required) - Balance at end of month (**stored, not calculated**)
- `total_income` (Float, Default: 0.0) - Total income for the month
- `total_expense` (Float, Default: 0.0) - Total expenses for the month
- `created_at` (DateTime)
- `updated_at` (DateTime)

**Constraints:**
- **Unique constraint on (account_id, year, month)** - Only one snapshot per account per month
- Indexes on: `account_id`, `year`, `month` for efficient queries

**Key Points:**
- ✅ `ending_balance` is **stored directly**, not calculated from transactions
- ✅ Snapshots can be entered manually, imported, or derived once and persisted
- ✅ This is the **authoritative source** for "How much money did I have this month?"

**Example:**
```python
{
    "id": 1,
    "account_id": 1,
    "year": 2026,
    "month": 1,
    "starting_balance": 2500.00,
    "ending_balance": 3200.00,
    "total_income": 3500.00,
    "total_expense": 2800.00
}
```

### 3. Transaction (Optional)

Represents individual income/expense entries. **Transactions do NOT define balances.**

**Purpose:**
- Detailed views of what happened during the month
- Manual expense/income entry
- Fine-grained analytics and categorization
- Supporting documentation for snapshot totals

**Fields:**
- `id` (Integer, Primary Key)
- `account_id` (Integer, Foreign Key → Account, Nullable for backward compatibility)
- `date` (Date, Required)
- `amount` (Float, Required) - Positive = income, negative = expense
- `category` (String, Required)
- `description` (String, Required)
- `currency` (String, Default: "EUR") - For backward compatibility
- `category_id` (Integer, Foreign Key → Category, Optional)
- `created_at` (DateTime)
- `updated_at` (DateTime)

**Indexes on:** `account_id`, `date`, `category`

**Important:** 
- ❌ Transactions are **never summed at runtime to infer balances**
- ✅ They provide **optional detail** for users who want to track individual items
- ✅ Snapshots can exist without any transactions

**Example:**
```python
{
    "id": 1,
    "account_id": 1,
    "date": "2026-01-05",
    "amount": -45.50,
    "category": "Food & Dining",
    "description": "Grocery shopping",
    "currency": "USD"
}
```

### 4. Category

Represents transaction categories with type information.

**Fields:**
- `id` (Integer, Primary Key)
- `name` (String, Unique, Required)
- `type` (String, Required) - Either "income" or "expense"
- `color` (String, Optional) - Hex color code for UI, e.g., "#FF5733"
- `created_at` (DateTime)
- `updated_at` (DateTime)

**Relationships:**
- Has many `Transaction`

**Example:**
```python
{
    "id": 1,
    "name": "Food & Dining",
    "type": "expense",
    "color": "#FF5733"
}
```

## Key Queries

The data model must efficiently support these queries (all specified in the problem statement):

### 1. Total Balance Today

**Query:** Sum of `ending_balance` from the **most recent snapshot** of each account.

```python
service.get_current_total_balance()
# Returns: 12100.00
```

**Implementation:** Find the latest snapshot for each account and sum their `ending_balance` values.

### 2. Total Balance Last Month

**Query:** Sum of `ending_balance` from snapshots for a specific month.

```python
service.get_total_balance_for_month(year=2025, month=12)
# Returns: 10700.00
```

**Implementation:** Filter snapshots by year and month, sum `ending_balance`.

### 3. Total Expenses for a Month

**Query:** Sum of `total_expense` across all snapshots for a given month.

```python
service.get_total_expenses_for_month(year=2026, month=1)
# Returns: 3500.00
```

**Implementation:** Filter snapshots by year and month, sum `total_expense`.

### 4. Total Income for a Month

**Query:** Sum of `total_income` across all snapshots for a given month.

```python
service.get_total_income_for_month(year=2026, month=1)
# Returns: 4000.00
```

### 5. Balance Trend Over Time

**Query:** Historical snapshots showing balance changes over time.

```python
service.get_balance_trend(account_id=1, num_months=12)
# Returns: List of snapshots for the last 12 months
```

**Implementation:** Query snapshots ordered by year/month descending, limit to N months.

## Data Responsibility Rules

### ✅ DO:
- **Always** read balances from `MonthlyAccountSnapshot.ending_balance`
- **Always** read monthly totals from `total_income` and `total_expense` in snapshots
- Use transactions for **detail views only** (e.g., "show me all food expenses this month")
- Allow snapshots to exist without transactions
- Allow manual entry of snapshot data

### ❌ DON'T:
- **Never** sum transactions to calculate balances
- **Never** infer account balance from transaction history
- **Never** use transactions as the source of truth for totals
- Don't assume snapshot data is derived from transactions

## Usage Examples

### Creating a New Month

```python
from src.business_logic.snapshot_service import SnapshotService

service = SnapshotService(db_session)

# Create snapshot for January 2026
snapshot = service.create_snapshot(
    account_id=1,
    year=2026,
    month=1,
    starting_balance=2500.00,  # From last month's ending balance
    ending_balance=3200.00,    # From bank statement or manual entry
    total_income=3500.00,      # Sum of all income this month
    total_expense=2800.00      # Sum of all expenses this month
)
```

### Adding Optional Transaction Detail

```python
# Add a transaction for record-keeping
# This does NOT affect the account balance
transaction = service.add_transaction(
    account_id=1,
    date=date(2026, 1, 5),
    amount=-45.50,
    category="Food & Dining",
    description="Grocery shopping at Whole Foods"
)
```

### Querying Current Financial State

```python
# How much money do I have right now?
current_balance = service.get_current_total_balance()
print(f"Total balance: ${current_balance:.2f}")

# How did it change from last month?
last_month = service.get_total_balance_for_month(2025, 12)
change = current_balance - last_month
print(f"Change: ${change:.2f}")

# What were my expenses this month?
expenses = service.get_total_expenses_for_month(2026, 1)
print(f"Total expenses: ${expenses:.2f}")
```

### Viewing Balance Trend

```python
# Show balance history for an account
trend = service.get_balance_trend(account_id=1, num_months=6)
for snapshot in trend:
    print(f"{snapshot['year']}-{snapshot['month']:02d}: ${snapshot['ending_balance']:.2f}")
```

## Database Schema (SQL)

```sql
-- Accounts table
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    type VARCHAR(50) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Monthly snapshots table (CORE)
CREATE TABLE monthly_account_snapshots (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    starting_balance FLOAT NOT NULL,
    ending_balance FLOAT NOT NULL,
    total_income FLOAT NOT NULL DEFAULT 0.0,
    total_expense FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(account_id, year, month)
);
CREATE INDEX idx_snapshots_account_id ON monthly_account_snapshots(account_id);
CREATE INDEX idx_snapshots_year ON monthly_account_snapshots(year);
CREATE INDEX idx_snapshots_month ON monthly_account_snapshots(month);

-- Categories table
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    type VARCHAR(10) NOT NULL CHECK (type IN ('income', 'expense')),
    color VARCHAR(7),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Transactions table (optional detail)
CREATE TABLE transactions (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    date DATE NOT NULL,
    amount FLOAT NOT NULL,
    category VARCHAR(100) NOT NULL,
    description VARCHAR(500) NOT NULL,
    currency VARCHAR(3) DEFAULT 'EUR',
    category_id INTEGER REFERENCES categories(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_transactions_account_id ON transactions(account_id);
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_category ON transactions(category);
```

## Migration from Transaction-Only Model

If migrating from a traditional transaction-only model:

1. **Create accounts** for each unique account in your transactions
2. **Group transactions by account and month**
3. **Calculate monthly snapshots:**
   - `starting_balance` = ending balance from previous month (or 0 for first month)
   - `ending_balance` = starting_balance + sum(transactions this month)
   - `total_income` = sum(positive transactions)
   - `total_expense` = sum(absolute value of negative transactions)
4. **Persist snapshots** to database
5. **Keep transactions** for detailed views (optional)

## Testing

Run the demonstration script to see the model in action:

```bash
python scripts/demo_monthly_model.py
```

Run unit tests:

```bash
pytest tests/test_snapshot_models.py -v
```

## API Layer (SnapshotService)

The `SnapshotService` class provides all the business logic for working with the monthly model:

### Account Management
- `create_account(name, type, currency, is_active)`
- `get_account(account_id)`
- `list_accounts(active_only=True)`

### Snapshot Management
- `create_snapshot(account_id, year, month, starting_balance, ending_balance, total_income, total_expense)`
- `update_snapshot(account_id, year, month, **fields)`
- `get_snapshot(account_id, year, month)`
- `list_snapshots_for_account(account_id, start_year, start_month, end_year, end_month)`

### Key Queries
- `get_current_total_balance()` - Total balance across all accounts today
- `get_total_balance_for_month(year, month)` - Total balance for specific month
- `get_total_expenses_for_month(year, month)` - Total expenses for month
- `get_total_income_for_month(year, month)` - Total income for month
- `get_balance_trend(account_id, num_months)` - Balance history

### Optional Transaction Support
- `add_transaction(account_id, date, amount, category, description, category_id, currency)`
- `list_transactions_for_account(account_id, start_date, end_date)`

### Category Management
- `create_category(name, type, color)`
- `list_categories(category_type)`

## Summary

This monthly-based data model provides:

✅ **Efficient queries** - Balance lookups are instant (no aggregation needed)
✅ **Spreadsheet mental model** - Matches how people think about monthly finances
✅ **Flexible data entry** - Supports manual entry, imports, and automated syncing
✅ **Optional granularity** - Transactions provide detail when needed
✅ **Data integrity** - Unique constraints prevent duplicate snapshots
✅ **Clear responsibility** - Snapshots = truth, transactions = detail

The model is **simple, robust, and evolvable** as specified in the requirements.
