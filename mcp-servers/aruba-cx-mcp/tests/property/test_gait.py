# Feature: aruba-cx-python-port, Property 7: GAIT log entry completeness
"""Property tests for GAIT audit logging.

Tests cover:
- Property 7: GAIT log entry completeness — every log entry has required fields,
  write ops include additional fields.
- Property 32: GAIT graceful degradation — GAIT failures never break tool invocations.

**Validates: Requirements 5.1, 5.2, 5.4**
"""

import io
import json
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Import helpers — aruba_cx_mcp_server.py has module-level side effects
# (FastMCP init, ArubaCxClient init). We mock those on import.
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    import importlib

    with patch.dict("sys.modules", {
        "fastmcp": MagicMock(),
    }):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            # If already imported, reload; otherwise import fresh
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()
_gait_log = _server._gait_log


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty printable text for operation, target, status
_operation = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
)

_target = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
)

_status = st.sampled_from(["success", "failure", "error", "partial"])

# Write-op extra fields
_change_request = st.from_regex(r"CHG\d{1,10}", fullmatch=True)

_baseline_dict = st.fixed_dictionaries({
    "admin_state": st.sampled_from(["up", "down"]),
    "description": st.text(min_size=0, max_size=30),
})

_verify_dict = st.fixed_dictionaries({
    "admin_state": st.sampled_from(["up", "down"]),
    "description": st.text(min_size=0, max_size=30),
})


# ---------------------------------------------------------------------------
# Property 7: GAIT log entry completeness
# Feature: aruba-cx-python-port, Property 7: GAIT log entry completeness
# **Validates: Requirements 5.1, 5.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(operation=_operation, target=_target, status=_status)
def test_gait_log_entry_has_required_fields(operation: str, target: str, status: str):
    """For any tool invocation, the GAIT log entry emitted to stderr should be
    valid JSON containing operation, timestamp (ISO 8601 UTC), target, and status."""
    captured = io.StringIO()

    with patch("sys.stderr", captured):
        _gait_log(operation, target, status)

    output = captured.getvalue().strip()
    assert len(output) > 0, "GAIT log should emit output to stderr"

    entry = json.loads(output)

    # Required fields present
    assert "operation" in entry, "Missing 'operation' field"
    assert "timestamp" in entry, "Missing 'timestamp' field"
    assert "target" in entry, "Missing 'target' field"
    assert "status" in entry, "Missing 'status' field"

    # Field values match inputs
    assert entry["operation"] == operation
    assert entry["target"] == target
    assert entry["status"] == status

    # Timestamp is valid ISO 8601 UTC (ends with Z)
    ts = entry["timestamp"]
    assert ts.endswith("Z"), f"Timestamp should end with 'Z' (UTC), got: {ts}"
    # Parse the timestamp to verify it's valid ISO 8601
    ts_no_z = ts.rstrip("Z")
    parsed = datetime.fromisoformat(ts_no_z)
    assert parsed is not None, f"Could not parse timestamp: {ts}"


@settings(max_examples=100)
@given(
    operation=_operation,
    target=_target,
    status=_status,
    change_request=_change_request,
    baseline=_baseline_dict,
    verify=_verify_dict,
)
def test_gait_log_write_op_includes_extra_fields(
    operation: str,
    target: str,
    status: str,
    change_request: str,
    baseline: dict,
    verify: dict,
):
    """For write operations, the GAIT log entry should additionally contain
    change_request_number, baseline, and verify fields."""
    captured = io.StringIO()

    with patch("sys.stderr", captured):
        _gait_log(
            operation,
            target,
            status,
            change_request_number=change_request,
            baseline=baseline,
            verify=verify,
        )

    output = captured.getvalue().strip()
    entry = json.loads(output)

    # Base fields still present
    assert entry["operation"] == operation
    assert entry["target"] == target
    assert entry["status"] == status

    # Write-op fields present
    assert "change_request_number" in entry, "Missing 'change_request_number' for write op"
    assert "baseline" in entry, "Missing 'baseline' for write op"
    assert "verify" in entry, "Missing 'verify' for write op"

    assert entry["change_request_number"] == change_request
    assert entry["baseline"] == baseline
    assert entry["verify"] == verify


# ---------------------------------------------------------------------------
# Property 32: GAIT graceful degradation
# Feature: aruba-cx-python-port, Property 32: GAIT graceful degradation
# **Validates: Requirements 5.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(operation=_operation, target=_target, status=_status)
def test_gait_graceful_degradation_on_json_dumps_failure(
    operation: str, target: str, status: str
):
    """If json.dumps raises an exception inside _gait_log, the function should
    silently degrade — no exception should propagate."""
    original_dumps = _server.json.dumps
    try:
        _server.json.dumps = MagicMock(side_effect=TypeError("boom"))
        # Should NOT raise
        _gait_log(operation, target, status)
    finally:
        _server.json.dumps = original_dumps


@settings(max_examples=100)
@given(operation=_operation, target=_target, status=_status)
def test_gait_graceful_degradation_on_print_failure(
    operation: str, target: str, status: str
):
    """If print to stderr raises an exception inside _gait_log, the function
    should silently degrade — no exception should propagate."""
    with patch("builtins.print", side_effect=IOError("stderr broken")):
        # Should NOT raise
        _gait_log(operation, target, status)


@settings(max_examples=100)
@given(operation=_operation, target=_target, status=_status)
def test_gait_graceful_degradation_on_datetime_failure(
    operation: str, target: str, status: str
):
    """If datetime.utcnow() raises an exception inside _gait_log, the function
    should silently degrade — no exception should propagate."""
    original_datetime = _server.datetime

    class BrokenDatetime:
        @staticmethod
        def utcnow():
            raise RuntimeError("clock broken")

    try:
        _server.datetime = BrokenDatetime
        # Should NOT raise
        _gait_log(operation, target, status)
    finally:
        _server.datetime = original_datetime
