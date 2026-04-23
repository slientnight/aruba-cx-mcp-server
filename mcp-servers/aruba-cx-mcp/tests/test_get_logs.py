"""Example-based unit tests for the get_logs MCP tool (Task 7).

Tests cover:
- Default limit of 50 when no limit provided
- All 8 severity values accepted
- Audit log emission on success and failure
- Error handling: API error, connection error, auth failure, generic exception
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import helpers — same pattern as existing tests
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out.

    The @mcp.tool() decorator must act as a passthrough so that the
    decorated functions (like get_logs) remain callable Python functions
    rather than becoming MagicMock objects.
    """
    import importlib

    server_dir = os.path.join(os.path.dirname(__file__), "..")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)

    # Build a fake FastMCP whose .tool() decorator is a no-op passthrough
    fake_mcp = MagicMock()
    fake_mcp.tool.return_value = lambda fn: fn  # decorator returns fn unchanged

    fake_fastmcp = MagicMock()
    fake_fastmcp.FastMCP.return_value = fake_mcp

    with patch.dict("sys.modules", {
        "fastmcp": fake_fastmcp,
    }):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()
get_logs = _server.get_logs

# Use the same ArubaCxException class the server module imported, so that
# isinstance checks inside get_logs' except clause match correctly.
ArubaCxException = _server.ArubaCxException
ArubaCxError = _server.ArubaCxError
ErrorCode = _server.ErrorCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_log(i: int, severity: str = "info", module: str = "hpe-test") -> dict:
    """Create a sample raw log entry dict."""
    return {
        "timestamp": f"2025-01-15T{i % 24:02d}:{i % 60:02d}:00Z",
        "severity": severity,
        "module": module,
        "message": f"Test log message {i}",
    }


def _parse_result(result: str):
    """Parse the JSON string returned by get_logs."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# 7.2 — Default limit of 50 when no limit provided
# ---------------------------------------------------------------------------

class TestDefaultLimit:
    def test_returns_at_most_50_entries_when_no_limit(self):
        """When get_logs is called with no limit (limit=0), it returns at most 50 entries."""
        raw_logs = [_make_raw_log(i) for i in range(80)]
        with patch.object(_server, "client") as mock_client:
            mock_client.get.return_value = raw_logs
            result = _parse_result(get_logs(target="test-switch"))
        assert isinstance(result, list)
        assert len(result) == 50

    def test_returns_all_when_fewer_than_50(self):
        """When fewer than 50 entries exist, all are returned."""
        raw_logs = [_make_raw_log(i) for i in range(10)]
        with patch.object(_server, "client") as mock_client:
            mock_client.get.return_value = raw_logs
            result = _parse_result(get_logs(target="test-switch"))
        assert isinstance(result, list)
        assert len(result) == 10

    def test_explicit_limit_overrides_default(self):
        """When an explicit limit is provided, it overrides the default of 50."""
        raw_logs = [_make_raw_log(i) for i in range(80)]
        with patch.object(_server, "client") as mock_client:
            mock_client.get.return_value = raw_logs
            result = _parse_result(get_logs(target="test-switch", limit=20))
        assert isinstance(result, list)
        assert len(result) == 20


# ---------------------------------------------------------------------------
# 7.3 — All 8 severity values accepted
# ---------------------------------------------------------------------------

class TestSeverityValues:
    @pytest.mark.parametrize("severity", [
        "emergency", "alert", "critical", "error",
        "warning", "notice", "info", "debug",
    ])
    def test_each_severity_value_accepted(self, severity):
        """Each of the 8 valid severity values should be accepted and return a list."""
        raw_logs = [_make_raw_log(i, severity=severity) for i in range(5)]
        with patch.object(_server, "client") as mock_client:
            mock_client.get.return_value = raw_logs
            result = _parse_result(get_logs(target="test-switch", severity=severity))
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 7.4 — Audit log emission on success and failure
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_log_on_success(self):
        """_audit_log is called with ("get_logs", target, "success") on successful calls."""
        raw_logs = [_make_raw_log(0)]
        with patch.object(_server, "client") as mock_client, \
             patch.object(_server, "_audit_log") as mock_audit:
            mock_client.get.return_value = raw_logs
            get_logs(target="test-switch")
        mock_audit.assert_called_once_with("get_logs", "test-switch", "success")

    def test_audit_log_on_api_failure(self):
        """_audit_log is called with ("get_logs", target, "error") when the API call fails."""
        error = ArubaCxError(
            code=ErrorCode.API_ERROR,
            message="API error",
            target="test-switch",
        )
        with patch.object(_server, "client") as mock_client, \
             patch.object(_server, "_audit_log") as mock_audit:
            mock_client.get.side_effect = ArubaCxException(error)
            get_logs(target="test-switch")
        mock_audit.assert_called_once_with("get_logs", "test-switch", "error")

    def test_audit_log_on_generic_exception(self):
        """_audit_log is called with error status when a generic exception occurs."""
        with patch.object(_server, "client") as mock_client, \
             patch.object(_server, "_audit_log") as mock_audit:
            mock_client.get.side_effect = RuntimeError("unexpected failure")
            get_logs(target="test-switch")
        mock_audit.assert_called_once_with("get_logs", "test-switch", "error")


# ---------------------------------------------------------------------------
# 7.5 — Error handling: API error, connection error, auth failure, generic
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_api_error_returns_aruba_cx_error(self):
        """ArubaCxException with API_ERROR returns serialized ArubaCxError."""
        error = ArubaCxError(
            code=ErrorCode.API_ERROR,
            message="HTTP 404 from target",
            target="test-switch",
            http_status=404,
        )
        with patch.object(_server, "client") as mock_client:
            mock_client.get.side_effect = ArubaCxException(error)
            result = _parse_result(get_logs(target="test-switch"))
        assert result["code"] == "API_ERROR"
        assert result["target"] == "test-switch"
        assert "404" in result["message"]

    def test_connection_error_returns_connection_error(self):
        """ArubaCxException with CONNECTION_ERROR returns serialized ArubaCxError."""
        error = ArubaCxError(
            code=ErrorCode.CONNECTION_ERROR,
            message="Connection refused",
            target="test-switch",
        )
        with patch.object(_server, "client") as mock_client:
            mock_client.get.side_effect = ArubaCxException(error)
            result = _parse_result(get_logs(target="test-switch"))
        assert result["code"] == "CONNECTION_ERROR"
        assert result["target"] == "test-switch"

    def test_auth_error_returns_auth_error(self):
        """ArubaCxException with AUTH_ERROR returns serialized ArubaCxError."""
        error = ArubaCxError(
            code=ErrorCode.AUTH_ERROR,
            message="Authentication failed",
            target="test-switch",
            http_status=401,
        )
        with patch.object(_server, "client") as mock_client:
            mock_client.get.side_effect = ArubaCxException(error)
            result = _parse_result(get_logs(target="test-switch"))
        assert result["code"] == "AUTH_ERROR"
        assert result["target"] == "test-switch"

    def test_generic_exception_returns_api_error(self):
        """A generic Exception is caught and returned as ArubaCxError with API_ERROR code."""
        with patch.object(_server, "client") as mock_client:
            mock_client.get.side_effect = RuntimeError("something broke")
            result = _parse_result(get_logs(target="test-switch"))
        assert result["code"] == "API_ERROR"
        assert result["target"] == "test-switch"
        assert "something broke" in result["message"]
