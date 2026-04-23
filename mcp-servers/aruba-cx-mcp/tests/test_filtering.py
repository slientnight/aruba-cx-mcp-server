"""Unit tests for filtering and sorting functions (Task 4)."""

import sys
import os
from datetime import datetime, timezone
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
filter_by_severity = _server.filter_by_severity
filter_by_since = _server.filter_by_since
filter_by_module = _server.filter_by_module
filter_by_search = _server.filter_by_search
sort_and_limit = _server.sort_and_limit

from models import LogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(ts="2025-01-15T14:30:00Z", sev="warning", mod="hpe-stpd", msg="test"):
    return LogEntry(timestamp=ts, severity=sev, module=mod, message=msg)


# ---------------------------------------------------------------------------
# 4.1 — filter_by_severity
# ---------------------------------------------------------------------------

class TestFilterBySeverity:
    def test_error_threshold_includes_higher_severities(self):
        entries = [
            _entry(sev="emergency"),
            _entry(sev="alert"),
            _entry(sev="critical"),
            _entry(sev="error"),
            _entry(sev="warning"),
            _entry(sev="info"),
            _entry(sev="debug"),
        ]
        result = filter_by_severity(entries, "error")
        severities = [e.severity for e in result]
        assert severities == ["emergency", "alert", "critical", "error"]

    def test_debug_threshold_includes_all(self):
        entries = [_entry(sev=s) for s in ["emergency", "warning", "debug"]]
        result = filter_by_severity(entries, "debug")
        assert len(result) == 3

    def test_emergency_threshold_includes_only_emergency(self):
        entries = [_entry(sev="emergency"), _entry(sev="alert"), _entry(sev="error")]
        result = filter_by_severity(entries, "emergency")
        assert len(result) == 1
        assert result[0].severity == "emergency"

    def test_unknown_severity_defaults_to_debug_rank(self):
        entries = [_entry(sev="unknown"), _entry(sev="error")]
        result = filter_by_severity(entries, "debug")
        assert len(result) == 2

    def test_unknown_severity_excluded_at_low_threshold(self):
        entries = [_entry(sev="unknown"), _entry(sev="error")]
        result = filter_by_severity(entries, "error")
        assert len(result) == 1
        assert result[0].severity == "error"

    def test_empty_list_returns_empty(self):
        assert filter_by_severity([], "error") == []


# ---------------------------------------------------------------------------
# 4.2 — filter_by_since
# ---------------------------------------------------------------------------

class TestFilterBySince:
    def test_filters_entries_before_since(self):
        entries = [
            _entry(ts="2025-01-15T10:00:00Z"),
            _entry(ts="2025-01-15T14:00:00Z"),
            _entry(ts="2025-01-15T18:00:00Z"),
        ]
        since = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = filter_by_since(entries, since)
        assert len(result) == 2
        assert result[0].timestamp == "2025-01-15T14:00:00Z"
        assert result[1].timestamp == "2025-01-15T18:00:00Z"

    def test_includes_entry_exactly_at_since(self):
        entries = [_entry(ts="2025-01-15T12:00:00+00:00")]
        since = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = filter_by_since(entries, since)
        assert len(result) == 1

    def test_excludes_unparseable_timestamps(self):
        entries = [_entry(ts="not-a-date"), _entry(ts="2025-01-15T14:00:00Z")]
        since = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = filter_by_since(entries, since)
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert filter_by_since([], since) == []


# ---------------------------------------------------------------------------
# 4.3 — filter_by_module
# ---------------------------------------------------------------------------

class TestFilterByModule:
    def test_exact_match(self):
        entries = [_entry(mod="hpe-stpd"), _entry(mod="hpe-lldpd")]
        result = filter_by_module(entries, "hpe-stpd")
        assert len(result) == 1
        assert result[0].module == "hpe-stpd"

    def test_case_insensitive_match(self):
        entries = [_entry(mod="HPE-STPD"), _entry(mod="hpe-stpd")]
        result = filter_by_module(entries, "hpe-stpd")
        assert len(result) == 2

    def test_filter_uppercase_matches_lowercase_entry(self):
        entries = [_entry(mod="hpe-lldpd")]
        result = filter_by_module(entries, "HPE-LLDPD")
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        entries = [_entry(mod="hpe-stpd")]
        result = filter_by_module(entries, "hpe-lldpd")
        assert len(result) == 0

    def test_empty_list_returns_empty(self):
        assert filter_by_module([], "hpe-stpd") == []


# ---------------------------------------------------------------------------
# 4.4 — filter_by_search
# ---------------------------------------------------------------------------

class TestFilterBySearch:
    def test_substring_match(self):
        entries = [
            _entry(msg="STP topology change on port 1/1/5"),
            _entry(msg="link down on 1/1/3"),
        ]
        result = filter_by_search(entries, "topology")
        assert len(result) == 1
        assert "topology" in result[0].message

    def test_case_insensitive_match(self):
        entries = [_entry(msg="STP Topology Change")]
        result = filter_by_search(entries, "stp topology")
        assert len(result) == 1

    def test_uppercase_search_matches_lowercase_message(self):
        entries = [_entry(msg="link down")]
        result = filter_by_search(entries, "LINK DOWN")
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        entries = [_entry(msg="STP topology change")]
        result = filter_by_search(entries, "OSPF")
        assert len(result) == 0

    def test_empty_list_returns_empty(self):
        assert filter_by_search([], "test") == []


# ---------------------------------------------------------------------------
# 4.5 — sort_and_limit
# ---------------------------------------------------------------------------

class TestSortAndLimit:
    def test_sorts_newest_first(self):
        entries = [
            _entry(ts="2025-01-15T10:00:00Z"),
            _entry(ts="2025-01-15T18:00:00Z"),
            _entry(ts="2025-01-15T14:00:00Z"),
        ]
        result = sort_and_limit(entries, 10)
        assert result[0].timestamp == "2025-01-15T18:00:00Z"
        assert result[1].timestamp == "2025-01-15T14:00:00Z"
        assert result[2].timestamp == "2025-01-15T10:00:00Z"

    def test_truncates_to_limit(self):
        entries = [_entry(ts=f"2025-01-15T{h:02d}:00:00Z") for h in range(10)]
        result = sort_and_limit(entries, 3)
        assert len(result) == 3

    def test_limit_larger_than_list_returns_all(self):
        entries = [_entry(), _entry()]
        result = sort_and_limit(entries, 100)
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        assert sort_and_limit([], 10) == []

    def test_single_entry(self):
        entries = [_entry(ts="2025-01-15T14:00:00Z")]
        result = sort_and_limit(entries, 5)
        assert len(result) == 1
