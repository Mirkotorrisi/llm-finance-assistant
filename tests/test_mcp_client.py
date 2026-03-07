"""Unit tests for the remote MCP client."""

import pytest
from unittest.mock import MagicMock, patch


class TestRemoteMCPClientInterface:
    """Tests to verify RemoteMCPClient exposes the expected interface."""

    def _make_client(self):
        from src.workflow.mcp_instance import RemoteMCPClient
        return RemoteMCPClient("http://localhost:8000")

    def test_has_list_transactions_method(self):
        client = self._make_client()
        assert hasattr(client, "list_transactions")
        assert callable(client.list_transactions)

    def test_has_add_transaction_method(self):
        client = self._make_client()
        assert hasattr(client, "add_transaction")
        assert callable(client.add_transaction)

    def test_has_add_transactions_bulk_method(self):
        client = self._make_client()
        assert hasattr(client, "add_transactions_bulk")
        assert callable(client.add_transactions_bulk)

    def test_has_delete_transaction_method(self):
        client = self._make_client()
        assert hasattr(client, "delete_transaction")
        assert callable(client.delete_transaction)

    def test_has_get_balance_method(self):
        client = self._make_client()
        assert hasattr(client, "get_balance")
        assert callable(client.get_balance)

    def test_has_get_existing_categories_method(self):
        client = self._make_client()
        assert hasattr(client, "get_existing_categories")
        assert callable(client.get_existing_categories)

    def test_has_get_accounts_method(self):
        client = self._make_client()
        assert hasattr(client, "get_accounts")
        assert callable(client.get_accounts)

    def test_has_get_financial_data_method(self):
        client = self._make_client()
        assert hasattr(client, "get_financial_data")
        assert callable(client.get_financial_data)


class TestRemoteMCPClientHTTPCalls:
    """Tests verifying that RemoteMCPClient makes the correct HTTP calls."""

    def _make_client(self):
        from src.workflow.mcp_instance import RemoteMCPClient
        client = RemoteMCPClient("http://testserver")
        client.session = MagicMock()
        return client

    def _mock_response(self, json_data, status_code=200):
        response = MagicMock()
        response.json.return_value = json_data
        response.status_code = status_code
        response.raise_for_status = MagicMock()
        return response

    def test_list_transactions_calls_correct_endpoint(self):
        client = self._make_client()
        client.session.get.return_value = self._mock_response([])

        client.list_transactions()

        client.session.get.assert_called_once_with(
            "http://testserver/api/transactions", params={}
        )

    def test_list_transactions_passes_filters(self):
        client = self._make_client()
        client.session.get.return_value = self._mock_response([])

        client.list_transactions(category="food", start_date="2026-01-01", end_date="2026-01-31")

        _, kwargs = client.session.get.call_args
        assert kwargs["params"]["category"] == "food"
        assert kwargs["params"]["start_date"] == "2026-01-01"
        assert kwargs["params"]["end_date"] == "2026-01-31"

    def test_add_transaction_calls_correct_endpoint(self):
        client = self._make_client()
        client.session.post.return_value = self._mock_response({"id": 1})

        result = client.add_transaction(-50.0, "food", "Grocery", date="2026-01-11")

        client.session.post.assert_called_once()
        args, kwargs = client.session.post.call_args
        assert args[0] == "http://testserver/api/transactions"
        assert kwargs["json"]["amount"] == -50.0
        assert kwargs["json"]["category"] == "food"
        assert kwargs["json"]["description"] == "Grocery"
        assert kwargs["json"]["date"] == "2026-01-11"

    def test_add_transactions_bulk_calls_correct_endpoint(self):
        client = self._make_client()
        client.session.post.return_value = self._mock_response([{"id": 1}])
        transactions = [{"amount": -50.0, "category": "food", "description": "Grocery"}]

        client.add_transactions_bulk(transactions)

        args, kwargs = client.session.post.call_args
        assert args[0] == "http://testserver/api/transactions/bulk"
        assert kwargs["json"] == transactions

    def test_delete_transaction_calls_correct_endpoint(self):
        client = self._make_client()
        client.session.delete.return_value = self._mock_response({"message": "deleted"})

        client.delete_transaction(42)

        client.session.delete.assert_called_once_with(
            "http://testserver/api/transactions/42"
        )

    def test_get_balance_returns_float(self):
        client = self._make_client()
        client.session.get.return_value = self._mock_response({"balance": 1234.56})

        balance = client.get_balance()

        assert balance == 1234.56

    def test_get_existing_categories_returns_sorted_unique(self):
        client = self._make_client()
        client.session.get.return_value = self._mock_response([
            {"id": 1, "category": "food"},
            {"id": 2, "category": "transport"},
            {"id": 3, "category": "food"},
            {"id": 4, "category": "income"},
        ])

        categories = client.get_existing_categories()

        assert categories == ["food", "income", "transport"]

    def test_get_accounts_calls_correct_endpoint(self):
        client = self._make_client()
        client.session.get.return_value = self._mock_response([{"id": 1, "name": "Checking"}])

        result = client.get_accounts()

        client.session.get.assert_called_once_with("http://testserver/api/accounts")
        assert result == [{"id": 1, "name": "Checking"}]

    def test_get_financial_data_calls_correct_endpoint(self):
        client = self._make_client()
        fake_data = {
            "year": 2026,
            "currentNetWorth": 1000.0,
            "netSavings": 200.0,
            "monthlyData": [],
            "accountBreakdown": {"liquidity": 0.0, "investments": 0.0, "otherAssets": 0.0},
        }
        client.session.get.return_value = self._mock_response(fake_data)

        result = client.get_financial_data(2026)

        client.session.get.assert_called_once_with(
            "http://testserver/api/financial-data/2026"
        )
        assert result["year"] == 2026

    def test_base_url_trailing_slash_stripped(self):
        from src.workflow.mcp_instance import RemoteMCPClient
        client = RemoteMCPClient("http://testserver/")
        assert client.base_url == "http://testserver"


class TestMCPInstanceModule:
    """Tests for module-level helpers in mcp_instance."""

    def test_module_has_get_mcp_server(self):
        import src.workflow.mcp_instance as mcp_instance
        assert hasattr(mcp_instance, "get_mcp_server")
        assert callable(mcp_instance.get_mcp_server)

    def test_module_has_reset_mcp_server(self):
        import src.workflow.mcp_instance as mcp_instance
        assert hasattr(mcp_instance, "reset_mcp_server")
        assert callable(mcp_instance.reset_mcp_server)

    def test_get_mcp_server_returns_remote_client(self):
        import src.workflow.mcp_instance as mcp_instance
        from src.workflow.mcp_instance import RemoteMCPClient
        # Reset to force fresh creation
        mcp_instance._mcp_client = None
        client = mcp_instance.get_mcp_server()
        assert isinstance(client, RemoteMCPClient)

    def test_get_mcp_server_returns_same_instance(self):
        import src.workflow.mcp_instance as mcp_instance
        mcp_instance._mcp_client = None
        first = mcp_instance.get_mcp_server()
        second = mcp_instance.get_mcp_server()
        assert first is second

    def test_reset_mcp_server_creates_new_instance(self):
        import src.workflow.mcp_instance as mcp_instance
        mcp_instance._mcp_client = None
        first = mcp_instance.get_mcp_server()
        mcp_instance.reset_mcp_server()
        second = mcp_instance.get_mcp_server()
        assert first is not second
