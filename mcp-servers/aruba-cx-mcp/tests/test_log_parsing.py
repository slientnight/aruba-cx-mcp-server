"""Unit tests for parse_log_entry and format_log_entry (Task 2)."""

import sys
import os
from unittest.mock import patch, MagicMock

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
parse_log_entry = _server.parse_log_entry
format_log_entry = _server.format_log_entry

from models import LogEntry


# ---------------------------------------------------------------------------
# 2.1 — parse_log_entry extracts fields from a raw API dict
# ---------------------------------------------------------------------------

class TestParseLogEntryFromDict:
    def test_extracts_all_fields(self):
        raw = {
            "timestamp": "2025-01-15T14:30:00Z",
            "severity": "warning",
            "module": "hpe-stpd",
            "message": "STP topology change on port 1/1/5",
        }
        entry = parse_log_entry(raw)
        assert entry.timestamp == "2025-01-15T14:30:00Z"
        assert entry.severity == "warning"
        assert entry.module == "hpe-stpd"
        assert entry.message == "STP topology change on port 1/1/5"

    def test_normalizes_severity_to_lowercase(self):
        raw = {
            "timestamp": "2025-01-15T14:30:00Z",
            "severity": "WARNING",
            "module": "hpe-stpd",
            "message": "test message",
        }
        entry = parse_log_entry(raw)
        assert entry.severity == "warning"

    def test_strips_whitespace(self):
        raw = {
            "timestamp": "  2025-01-15T14:30:00Z  ",
            "severity": "  error  ",
            "module": "  hpe-lldpd  ",
            "message": "  link down  ",
        }
        entry = parse_log_entry(raw)
        assert entry.timestamp == "2025-01-15T14:30:00Z"
        assert entry.severity == "error"
        assert entry.module == "hpe-lldpd"
        assert entry.message == "link down"


# ---------------------------------------------------------------------------
# 2.2 — fallback logic for missing/unparseable fields
# ---------------------------------------------------------------------------

class TestParseLogEntryFallback:
    def test_missing_severity_falls_back(self):
        raw = {
            "timestamp": "2025-01-15T14:30:00Z",
            "module": "hpe-stpd",
            "message": "some message",
        }
        entry = parse_log_entry(raw)
        assert entry.severity == "unknown"
        assert entry.module == "unknown"

    def test_missing_module_falls_back(self):
        raw = {
            "timestamp": "2025-01-15T14:30:00Z",
            "severity": "error",
            "message": "some message",
        }
        entry = parse_log_entry(raw)
        assert entry.severity == "unknown"
        assert entry.module == "unknown"

    def test_empty_severity_falls_back(self):
        raw = {
            "timestamp": "2025-01-15T14:30:00Z",
            "severity": "",
            "module": "hpe-stpd",
            "message": "some message",
        }
        entry = parse_log_entry(raw)
        assert entry.severity == "unknown"
        assert entry.module == "unknown"

    def test_empty_dict_falls_back(self):
        raw = {}
        entry = parse_log_entry(raw)
        assert entry.severity == "unknown"
        assert entry.module == "unknown"
        assert isinstance(entry.timestamp, str)
        assert len(entry.timestamp) > 0

    def test_non_string_values_fall_back(self):
        raw = {
            "timestamp": 12345,
            "severity": None,
            "module": 42,
            "message": "test",
        }
        entry = parse_log_entry(raw)
        assert entry.severity == "unknown"
        assert entry.module == "unknown"


# ---------------------------------------------------------------------------
# 2.3 — format_log_entry and round-trip
# ---------------------------------------------------------------------------

class TestFormatLogEntry:
    def test_format_produces_expected_string(self):
        entry = LogEntry(
            timestamp="2025-01-15T14:30:00Z",
            severity="warning",
            module="hpe-stpd",
            message="STP topology change on port 1/1/5",
        )
        result = format_log_entry(entry)
        assert result == "2025-01-15T14:30:00Z [warning] [hpe-stpd] STP topology change on port 1/1/5"

    def test_round_trip_preserves_entry(self):
        original = LogEntry(
            timestamp="2025-01-15T14:30:00Z",
            severity="error",
            module="hpe-lldpd",
            message="link down on 1/1/3",
        )
        formatted = format_log_entry(original)
        parsed = parse_log_entry(formatted)
        assert parsed.timestamp == original.timestamp
        assert parsed.severity == original.severity
        assert parsed.module == original.module
        assert parsed.message == original.message

    def test_round_trip_with_all_severities(self):
        for sev in ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]:
            entry = LogEntry(
                timestamp="2025-06-01T00:00:00Z",
                severity=sev,
                module="test-mod",
                message="test message",
            )
            formatted = format_log_entry(entry)
            parsed = parse_log_entry(formatted)
            assert parsed.severity == sev
            assert parsed.module == "test-mod"
