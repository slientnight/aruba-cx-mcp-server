"""Unit tests for parameter parsing and validation (Task 3)."""

import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import helpers — same pattern as existing tests
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    import importlib

    server_dir = os.path.join(os.path.dirname(__file__), "..")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)

    with patch.dict("sys.modules", {
        "fastmcp": MagicMock(),
    }):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()
parse_since = _server.parse_since
validate_severity = _server.validate_severity
clamp_limit = _server.clamp_limit
VALID_SEVERITIES = _server.VALID_SEVERITIES


# ---------------------------------------------------------------------------
# 3.1 — parse_since
# ---------------------------------------------------------------------------

class TestParseSince:
    def test_relative_minutes(self):
        before = datetime.now(timezone.utc)
        result = parse_since("30m")
        after = datetime.now(timezone.utc)
        expected = timedelta(minutes=30)
        assert before - expected <= result <= after - expected + timedelta(seconds=1)

    def test_relative_hours(self):
        before = datetime.now(timezone.utc)
        result = parse_since("1h")
        after = datetime.now(timezone.utc)
        expected = timedelta(hours=1)
        assert before - expected <= result <= after - expected + timedelta(seconds=1)

    def test_relative_days(self):
        before = datetime.now(timezone.utc)
        result = parse_since("7d")
        after = datetime.now(timezone.utc)
        expected = timedelta(days=7)
        assert before - expected <= result <= after - expected + timedelta(seconds=1)

    def test_iso8601_with_timezone(self):
        result = parse_since("2025-01-15T14:30:00+00:00")
        assert result == datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)

    def test_iso8601_without_timezone_assumes_utc(self):
        result = parse_since("2025-01-15T14:30:00")
        assert result == datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc)

    def test_iso8601_z_suffix(self):
        result = parse_since("2025-01-15T14:30:00Z")
        assert result.tzinfo is not None

    def test_invalid_string_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid 'since' value"):
            parse_since("not-a-date")

    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError, match="Empty 'since' value"):
            parse_since("")

    def test_whitespace_only_raises_valueerror(self):
        with pytest.raises(ValueError, match="Empty 'since' value"):
            parse_since("   ")

    def test_invalid_unit_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid 'since' value"):
            parse_since("5x")

    def test_strips_whitespace(self):
        result = parse_since("  1h  ")
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# 3.2 — validate_severity
# ---------------------------------------------------------------------------

class TestValidateSeverity:
    @pytest.mark.parametrize("sev", [
        "emergency", "alert", "critical", "error",
        "warning", "notice", "info", "debug",
    ])
    def test_valid_severities_accepted(self, sev):
        # Should not raise
        validate_severity(sev)

    def test_case_insensitive_acceptance(self):
        validate_severity("WARNING")
        validate_severity("Error")
        validate_severity("DEBUG")

    def test_invalid_severity_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            validate_severity("fatal")

    def test_error_message_lists_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            validate_severity("bogus")
        msg = str(exc_info.value)
        for sev in VALID_SEVERITIES:
            assert sev in msg

    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            validate_severity("")


# ---------------------------------------------------------------------------
# 3.3 — clamp_limit
# ---------------------------------------------------------------------------

class TestClampLimit:
    def test_zero_defaults_to_50(self):
        assert clamp_limit(0) == 50

    def test_negative_defaults_to_50(self):
        assert clamp_limit(-10) == 50

    def test_value_in_range_unchanged(self):
        assert clamp_limit(100) == 100

    def test_value_at_lower_bound(self):
        assert clamp_limit(1) == 1

    def test_value_at_upper_bound(self):
        assert clamp_limit(1000) == 1000

    def test_value_above_upper_bound_clamped(self):
        assert clamp_limit(5000) == 1000

    def test_default_50_value(self):
        assert clamp_limit(50) == 50
