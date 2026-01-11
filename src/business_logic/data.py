"""Initial data for the finance assistant."""

import datetime
from typing import List


def get_initial_data() -> List[dict]:
    """Get initial transaction data for the finance assistant.
    
    Returns:
        List of initial transactions
    """
    today = datetime.date.today()
    return [
        {
            "id": 1, 
            "date": (today - datetime.timedelta(days=6)).isoformat(), 
            "amount": -50.0, 
            "category": "food", 
            "description": "Grocery shopping"
        },
        {
            "id": 2, 
            "date": (today - datetime.timedelta(days=5)).isoformat(), 
            "amount": -15.0, 
            "category": "transport", 
            "description": "Bus ticket"
        },
        {
            "id": 3, 
            "date": (today - datetime.timedelta(days=4)).isoformat(), 
            "amount": -1200.0, 
            "category": "rent", 
            "description": "Monthly rent"
        },
        {
            "id": 4, 
            "date": (today - datetime.timedelta(days=3)).isoformat(), 
            "amount": -30.0, 
            "category": "food", 
            "description": "Dinner out"
        },
        {
            "id": 5, 
            "date": (today - datetime.timedelta(days=2)).isoformat(), 
            "amount": 2000.0, 
            "category": "income", 
            "description": "Salary"
        },
        {
            "id": 6, 
            "date": (today - datetime.timedelta(days=1)).isoformat(), 
            "amount": -45.0, 
            "category": "food", 
            "description": "Lunch with friends"
        },
        {
            "id": 7, 
            "date": today.isoformat(), 
            "amount": -10.0, 
            "category": "transport", 
            "description": "Parking"
        },
    ]
