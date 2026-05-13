"""Tests para api/dynamo_store.py — operaciones DynamoDB con boto3 mockeado."""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError


def _client_error(code="InternalServerError"):
    error = {"Error": {"Code": code, "Message": "test error"}}
    return ClientError(error, "operation")


@pytest.fixture(autouse=True)
def reset_dynamo_state():
    """Reinicia el cliente y TABLE entre tests."""
    import api.dynamo_store as ds
    ds._client = None
    original_table = ds.TABLE
    yield
    ds._client = None
    ds.TABLE = original_table


class TestGetClient:
    def test_get_client_creates_boto3_client(self):
        """Cubre lines 37-39: _get_client() instancia boto3 cuando _client es None."""
        import api.dynamo_store as ds
        ds._client = None
        mock_client = MagicMock()
        with patch("api.dynamo_store.boto3.client", return_value=mock_client):
            result = ds._get_client()
        assert result is mock_client
        assert ds._client is mock_client

    def test_get_client_reuses_singleton(self):
        import api.dynamo_store as ds
        ds._client = None
        mock_client = MagicMock()
        with patch("api.dynamo_store.boto3.client", return_value=mock_client):
            ds._get_client()
            ds._get_client()  # second call should not recreate
        # boto3.client called only once
        assert ds._client is mock_client


class TestEnabled:
    def test_enabled_when_table_set(self):
        import api.dynamo_store as ds
        ds.TABLE = "my-table"
        assert ds.enabled() is True

    def test_disabled_when_table_empty(self):
        import api.dynamo_store as ds
        ds.TABLE = ""
        assert ds.enabled() is False


class TestCreateSession:
    def test_no_op_when_disabled(self):
        import api.dynamo_store as ds
        ds.TABLE = ""
        with patch("api.dynamo_store._get_client") as mock_get:
            ds.create_session("sess-001")
        mock_get.assert_not_called()

    def test_puts_item_when_enabled(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.create_session("sess-001", phase="running", prompt="hola")
        mock_client.put_item.assert_called_once()
        call_kwargs = mock_client.put_item.call_args.kwargs
        assert call_kwargs["TableName"] == "test-table"
        assert call_kwargs["Item"]["session_id"]["S"] == "sess-001"
        assert call_kwargs["Item"]["phase"]["S"] == "running"

    def test_handles_client_error_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.put_item.side_effect = _client_error()
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.create_session("sess-error")  # should not raise

    def test_handles_generic_exception_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.put_item.side_effect = RuntimeError("boom")
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.create_session("sess-error")  # should not raise


class TestGetSession:
    def test_returns_none_when_disabled(self):
        import api.dynamo_store as ds
        ds.TABLE = ""
        result = ds.get_session("sess-001")
        assert result is None

    def test_returns_none_when_item_not_found(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.get_item.return_value = {"Item": None}
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            result = ds.get_session("sess-missing")
        assert result is None

    def test_returns_parsed_session(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.get_item.return_value = {
            "Item": {
                "session_id":      {"S": "sess-001"},
                "phase":           {"S": "completed"},
                "messages":        {"S": json.dumps([{"role": "system", "content": "ok"}])},
                "hitl_data":       {"S": "null"},
                "error":           {"S": ""},
                "docx_s3_key":     {"S": "results/doc.docx"},
                "workflow_status": {"S": "success"},
                "paci_s3_key":     {"S": "jobs/sess-001/paci.pdf"},
                "material_s3_key": {"S": "jobs/sess-001/material.docx"},
                "prompt":          {"S": "hola"},
                "school_id":       {"S": "colegio_demo"},
            }
        }
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            result = ds.get_session("sess-001")
        assert result["phase"] == "completed"
        assert result["workflow_status"] == "success"
        assert len(result["messages"]) == 1
        assert result["hitl_data"] is None
        assert result["error"] is None

    def test_handles_client_error_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.get_item.side_effect = _client_error()
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            result = ds.get_session("sess-error")
        assert result is None

    def test_handles_generic_exception_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.get_item.side_effect = RuntimeError("boom")
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            result = ds.get_session("sess-error")
        assert result is None


class TestUpdateSession:
    def test_no_op_when_disabled(self):
        import api.dynamo_store as ds
        ds.TABLE = ""
        with patch("api.dynamo_store._get_client") as mock_get:
            ds.update_session("sess-001", phase="completed")
        mock_get.assert_not_called()

    def test_skips_unknown_fields(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.update_session("sess-001", unknown_field="value")
        mock_client.update_item.assert_not_called()

    def test_updates_known_fields(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.update_session("sess-001", phase="completed", workflow_status="success")
        mock_client.update_item.assert_called_once()
        call_kwargs = mock_client.update_item.call_args.kwargs
        assert call_kwargs["TableName"] == "test-table"
        assert call_kwargs["Key"]["session_id"]["S"] == "sess-001"

    def test_handles_throttle_error_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.update_item.side_effect = _client_error("ProvisionedThroughputExceededException")
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.update_session("sess-001", phase="completed")  # should not raise

    def test_handles_client_error_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.update_item.side_effect = _client_error("InternalServerError")
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.update_session("sess-001", phase="completed")  # should not raise

    def test_handles_generic_exception_gracefully(self):
        import api.dynamo_store as ds
        ds.TABLE = "test-table"
        mock_client = MagicMock()
        mock_client.update_item.side_effect = RuntimeError("boom")
        with patch("api.dynamo_store._get_client", return_value=mock_client):
            ds.update_session("sess-001", phase="error")  # should not raise
