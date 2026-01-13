"""Integration test for the bank statement upload feature."""

import io
import pytest
from fastapi.testclient import TestClient
from src.api.app import app

client = TestClient(app)


def test_complete_upload_workflow():
    """Test the complete upload workflow from end to end."""
    
    # 1. Check initial balance
    response = client.get("/api/balance")
    assert response.status_code == 200
    initial_balance = response.json()["balance"]
    
    # 2. Get initial transaction count
    response = client.get("/api/transactions")
    assert response.status_code == 200
    initial_count = len(response.json()["transactions"])
    
    # 3. Upload a CSV file
    csv_content = b"""date,description,amount,currency
2026-01-15,Test Grocery,-50.00,EUR
2026-01-16,Test Gas Station,-30.00,EUR
2026-01-17,Test Salary,2000.00,EUR"""
    
    files = {"file": ("test_upload.csv", io.BytesIO(csv_content), "text/csv")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["transactions_added"] == 3
    assert result["transactions_skipped"] == 0
    
    # 4. Verify transactions were added
    response = client.get("/api/transactions")
    assert response.status_code == 200
    new_count = len(response.json()["transactions"])
    assert new_count == initial_count + 3
    
    # 5. Verify balance changed
    response = client.get("/api/balance")
    assert response.status_code == 200
    new_balance = response.json()["balance"]
    expected_change = -50.00 - 30.00 + 2000.00  # Sum of uploaded transactions
    assert abs(new_balance - (initial_balance + expected_change)) < 0.01
    
    # 6. Test duplicate detection - upload same file again
    files = {"file": ("test_upload.csv", io.BytesIO(csv_content), "text/csv")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["transactions_added"] == 0
    assert result["transactions_skipped"] == 3  # All should be duplicates
    
    # 7. Verify no new transactions were added
    response = client.get("/api/transactions")
    assert response.status_code == 200
    final_count = len(response.json()["transactions"])
    assert final_count == new_count  # Should be same as after first upload


def test_invalid_file_format():
    """Test that invalid file formats are rejected."""
    
    content = b"This is not a valid file"
    files = {"file": ("test.txt", io.BytesIO(content), "text/plain")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 400
    assert "Unsupported file format" in response.json()["detail"]


def test_file_too_large():
    """Test that files exceeding size limit are rejected."""
    
    # Create a large CSV content (> 10 MB)
    large_content = b"date,description,amount\n" * 1000000
    files = {"file": ("large.csv", io.BytesIO(large_content), "text/csv")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 400
    assert "exceeds maximum limit" in response.json()["detail"]


def test_empty_csv():
    """Test uploading an empty CSV file."""
    
    csv_content = b"date,description,amount\n"
    files = {"file": ("empty.csv", io.BytesIO(csv_content), "text/csv")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["transactions_added"] == 0


def test_categorization():
    """Test that transactions are categorized correctly."""
    
    csv_content = b"""date,description,amount
2026-01-20,Grocery shopping at Walmart,-100.00
2026-01-21,Gas station fill up,-40.00
2026-01-22,Amazon purchase,-75.00
2026-01-23,Monthly salary payment,3000.00"""
    
    files = {"file": ("categorization_test.csv", io.BytesIO(csv_content), "text/csv")}
    response = client.post("/statements/upload", files=files)
    
    assert response.status_code == 200
    result = response.json()
    transactions = result["transactions"]
    
    # Check categories
    assert any(t["category"] == "food" for t in transactions)
    assert any(t["category"] == "transport" for t in transactions)
    assert any(t["category"] == "shopping" for t in transactions)
    assert any(t["category"] == "income" for t in transactions)


def test_financial_data_endpoint():
    """Test the financial data endpoint."""
    
    # Test with a year (2026)
    response = client.get("/api/financial-data/2026")
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify response structure
    assert "year" in data
    assert "currentNetWorth" in data
    assert "netSavings" in data
    assert "monthlyData" in data
    assert "accountBreakdown" in data
    
    # Verify year
    assert data["year"] == 2026
    
    # Verify monthlyData structure
    assert isinstance(data["monthlyData"], list)
    assert len(data["monthlyData"]) == 12  # Should have 12 months
    
    # Check first month structure
    if data["monthlyData"]:
        first_month = data["monthlyData"][0]
        assert "month" in first_month
        assert "netWorth" in first_month
        assert "expenses" in first_month
        assert "income" in first_month
        assert "net" in first_month
        assert first_month["month"] == "Jan"
    
    # Verify accountBreakdown structure
    assert "liquidity" in data["accountBreakdown"]
    assert "investments" in data["accountBreakdown"]
    assert "otherAssets" in data["accountBreakdown"]


def test_financial_data_endpoint_year_with_no_data():
    """Test financial data endpoint with a year that has no data."""
    
    # Test with a year that likely has no data (e.g., 2050)
    response = client.get("/api/financial-data/2050")
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify response structure even with no data
    assert data["year"] == 2050
    assert data["currentNetWorth"] == 0.0
    assert data["netSavings"] == 0.0
    assert len(data["monthlyData"]) == 12
    
    # All months should have zero values
    for month_data in data["monthlyData"]:
        assert month_data["netWorth"] == 0.0
        assert month_data["expenses"] == 0.0
        assert month_data["income"] == 0.0
        assert month_data["net"] == 0.0
    
    # Account breakdown should be all zeros
    assert data["accountBreakdown"]["liquidity"] == 0.0
    assert data["accountBreakdown"]["investments"] == 0.0
    assert data["accountBreakdown"]["otherAssets"] == 0.0


def test_financial_data_endpoint_month_names():
    """Test that month names are properly formatted."""
    
    response = client.get("/api/financial-data/2026")
    assert response.status_code == 200
    
    data = response.json()
    monthly_data = data["monthlyData"]
    
    expected_months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    actual_months = [m["month"] for m in monthly_data]
    
    assert actual_months == expected_months
