# Feature: switch-log-retrieval, Property 1: Log Entry Round-Trip
# Feature: switch-log-retrieval, Property 2: Log Parsing Produces Structured Entries
# Feature: switch-log-retrieval, Property 3: Malformed Log Fallback
# Feature: switch-log-retrieval, Property 4: Severity Filter Correctness
# Feature: switch-log-retrieval, Property 5: Invalid Severity Rejection
# Feature: switch-log-retrieval, Property 6: Since Time Filter Correctness
# Feature: switch-log-retrieval, Property 7: parse_since Round-Trip for Durations
# Feature: switch-log-retrieval, Property 8: Invalid Since Rejection
# Feature: switch-log-retrieval, Property 9: Module Filter with Case-Insensitive Matching
# Feature: switch-log-retrieval, Property 10: Search Filter with Case-Insensitive Matching
# Feature: switch-log-retrieval, Property 11: Limit Clamping and Enforcement
# Feature: switch-log-retrieval, Property 12: Reverse Chronological Sort
"""Property tests for switch log retrieval — parsing, filtering, sorting.

Tests cover:
- Property 1: Round-trip — format then parse produces equivalent LogEntry.
- Property 2: Parsing — raw log dicts with all fields produce correct LogEntry.
- Property 3: Fallback — malformed dicts produce LogEntry with "unknown" severity/module.
- Property 4: Severity filter — all returned entries have severity at or above threshold.
- Property 5: Invalid severity — non-valid strings are rejected with error listing valid values.
- Property 6: Since filter — all returned entries have timestamp >= since.
- Property 7: parse_since — valid durations produce correct datetime.
- Property 8: Invalid since — invalid strings raise ValueError.
- Property 9: Module filter — case-insensitive module matching.
- Property 10: Search filter — case-insensitive substring matching.
- Property 11: Limit — result count <= clamped limit.
- Property 12: Sort — entries in reverse chronological order.

**Validates: Requirements 1.2, 1.4, 2.1, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, 5.1, 5.3, 5.4, 6.1, 6.2, 9.1, 9.2, 9.3**
"""

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from models import LogEntry


# ---------------------------------------------------------------------------
# Import helpers — same mock pattern as other property tests
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    mock_fastmcp = MagicMock()

    def _passthrough_tool(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    mock_fastmcp.FastMCP.return_value.tool = _passthrough_tool

    with patch.dict("sys.modules", {"fastmcp": mock_fastmcp}):
        with patch.dict("os.environ", {"ARUBA_CX_TARGETS": "[]"}, clear=False):
            if "aruba_cx_mcp_server" in sys.modules:
                mod = importlib.reload(sys.modules["aruba_cx_mcp_server"])
            else:
                mod = importlib.import_module("aruba_cx_mcp_server")
            return mod


_server = _import_server_module()

parse_log_entry = _server.parse_log_entry
format_log_entry = _server.format_log_entry
parse_since = _server.parse_since
validate_severity = _server.validate_severity
clamp_limit = _server.clamp_limit
filter_by_severity = _server.filter_by_severity
filter_by_since = _server.filter_by_since
filter_by_module = _server.filter_by_module
filter_by_search = _server.filter_by_search
sort_and_limit = _server.sort_and_limit
SEVERITY_RANKS = _server.SEVERITY_RANKS
VALID_SEVERITIES = _server.VALID_SEVERITIES


# ---------------------------------------------------------------------------
# Strategies (Task 6.1)
# ---------------------------------------------------------------------------

# Characters safe for module/message — no brackets to avoid breaking format pattern
_safe_chars = st.characters(
    whitelist_categories=("L", "N", "Zs"),
    blacklist_characters="[]\n\r",
)

_nonempty_safe_text = st.text(alphabet=_safe_chars, min_size=1, max_size=50).map(
    lambda s: s.strip() or "a"
)

# Valid ISO 8601 timestamps
_iso_timestamp = st.builds(
    lambda y, mo, d, h, mi, s: f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z",
    y=st.integers(min_value=2000, max_value=2030),
    mo=st.integers(min_value=1, max_value=12),
    d=st.integers(min_value=1, max_value=28),
    h=st.integers(min_value=0, max_value=23),
    mi=st.integers(min_value=0, max_value=59),
    s=st.integers(min_value=0, max_value=59),
)

# Valid severity values
severity_strategy = st.sampled_from(list(SEVERITY_RANKS.keys()))

# LogEntry strategy — valid entries with proper ISO timestamps
log_entry_strategy = st.builds(
    LogEntry,
    timestamp=_iso_timestamp,
    severity=severity_strategy,
    module=_nonempty_safe_text,
    message=_nonempty_safe_text,
)

# Raw log dict strategy — all 4 fields present as non-empty strings
raw_log_dict_strategy = st.fixed_dictionaries({
    "timestamp": _iso_timestamp,
    "severity": _nonempty_safe_text,
    "module": _nonempty_safe_text,
    "message": _nonempty_safe_text,
})

# Malformed log dict strategy — missing severity, module, or both
malformed_log_strategy = st.one_of(
    # Missing severity
    st.fixed_dictionaries({
        "timestamp": _iso_timestamp,
        "module": _nonempty_safe_text,
        "message": _nonempty_safe_text,
    }),
    # Missing module
    st.fixed_dictionaries({
        "timestamp": _iso_timestamp,
        "severity": _nonempty_safe_text,
        "message": _nonempty_safe_text,
    }),
    # Missing both severity and module
    st.fixed_dictionaries({
        "timestamp": _iso_timestamp,
        "message": _nonempty_safe_text,
    }),
)

# Invalid severity — strings NOT in the valid set
invalid_severity_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.lower() not in SEVERITY_RANKS)

# Duration strategy — valid relative durations: positive int + unit
duration_strategy = st.builds(
    lambda amount, unit: f"{amount}{unit}",
    amount=st.integers(min_value=1, max_value=999),
    unit=st.sampled_from(["m", "h", "d"]),
)

# Invalid since strategy — neither valid duration nor ISO 8601
invalid_since_strategy = st.one_of(
    st.just(""),
    st.just("abc"),
    st.just("yesterday"),
    st.just("1x"),
    st.just("0h"),
    st.just("-5m"),
    st.just("h1"),
    st.just("not-a-date"),
    st.text(
        alphabet=st.characters(whitelist_categories=("L",)),
        min_size=2,
        max_size=20,
    ).filter(lambda s: not s.strip() == ""),
)

# Search string strategy
_search_string = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)


# ---------------------------------------------------------------------------
# Property 1: Round-trip — format then parse produces equivalent LogEntry
# Feature: switch-log-retrieval, Property 1
# **Validates: Requirements 9.3**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(entry=log_entry_strategy)
def test_round_trip_format_then_parse(entry: LogEntry):
    """For any valid LogEntry, format_log_entry then parse_log_entry produces
    an equivalent LogEntry with the same timestamp, severity, module, message."""
    formatted = format_log_entry(entry)
    parsed = parse_log_entry(formatted)
    assert parsed.timestamp == entry.timestamp
    assert parsed.severity == entry.severity
    assert parsed.module == entry.module
    assert parsed.message == entry.message


# ---------------------------------------------------------------------------
# Property 2: Parsing — raw log dicts with all fields produce correct LogEntry
# Feature: switch-log-retrieval, Property 2
# **Validates: Requirements 9.1, 1.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(raw=raw_log_dict_strategy)
def test_parsing_raw_log_dict(raw: dict):
    """For any raw log dict with all 4 fields present as non-empty strings,
    parse_log_entry returns a LogEntry with fields correctly populated."""
    entry = parse_log_entry(raw)
    assert entry.timestamp == raw["timestamp"].strip()
    assert entry.severity == raw["severity"].strip().lower()
    assert entry.module == raw["module"].strip()
    assert entry.message == raw["message"].strip()


# ---------------------------------------------------------------------------
# Property 3: Fallback — malformed dicts produce LogEntry with "unknown"
# Feature: switch-log-retrieval, Property 3
# **Validates: Requirements 9.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(raw=malformed_log_strategy)
def test_malformed_dict_fallback(raw: dict):
    """For any malformed dict (missing severity or module), parse_log_entry
    returns LogEntry with severity='unknown' and module='unknown'."""
    entry = parse_log_entry(raw)
    assert entry.severity == "unknown"
    assert entry.module == "unknown"


# ---------------------------------------------------------------------------
# Property 4: Severity filter — returned entries at or above threshold
# Feature: switch-log-retrieval, Property 4
# **Validates: Requirements 2.1**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entries=st.lists(log_entry_strategy, min_size=0, max_size=30),
    threshold=severity_strategy,
)
def test_severity_filter_correctness(entries: list, threshold: str):
    """For any list of LogEntry objects and valid severity threshold,
    filter_by_severity returns only entries with severity rank <= threshold rank."""
    result = filter_by_severity(entries, threshold)
    threshold_rank = SEVERITY_RANKS[threshold]
    for e in result:
        assert SEVERITY_RANKS.get(e.severity.lower(), 7) <= threshold_rank
    # Also verify completeness — no qualifying entry was dropped
    for e in entries:
        if SEVERITY_RANKS.get(e.severity.lower(), 7) <= threshold_rank:
            assert e in result


# ---------------------------------------------------------------------------
# Property 5: Invalid severity — rejected with error listing valid values
# Feature: switch-log-retrieval, Property 5
# **Validates: Requirements 2.3**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(bad_severity=invalid_severity_strategy)
def test_invalid_severity_rejected(bad_severity: str):
    """For any string not in VALID_SEVERITIES, validate_severity raises
    ValueError containing all valid severity names."""
    with pytest.raises(ValueError) as exc_info:
        validate_severity(bad_severity)
    error_msg = str(exc_info.value)
    for sev in VALID_SEVERITIES:
        assert sev in error_msg


# ---------------------------------------------------------------------------
# Property 6: Since filter — returned entries have timestamp >= since
# Feature: switch-log-retrieval, Property 6
# **Validates: Requirements 3.1**
# ---------------------------------------------------------------------------

_since_datetime = st.builds(
    lambda y, mo, d, h, mi, s: datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc),
    y=st.integers(min_value=2000, max_value=2030),
    mo=st.integers(min_value=1, max_value=12),
    d=st.integers(min_value=1, max_value=28),
    h=st.integers(min_value=0, max_value=23),
    mi=st.integers(min_value=0, max_value=59),
    s=st.integers(min_value=0, max_value=59),
)


@settings(max_examples=100)
@given(
    entries=st.lists(log_entry_strategy, min_size=0, max_size=30),
    since=_since_datetime,
)
def test_since_filter_correctness(entries: list, since: datetime):
    """For any list of LogEntry objects with valid timestamps and any since
    datetime, filter_by_since returns only entries with timestamp >= since."""
    result = filter_by_since(entries, since)
    for e in result:
        dt = datetime.fromisoformat(e.timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        assert dt >= since


# ---------------------------------------------------------------------------
# Property 7: parse_since — valid durations produce correct datetime
# Feature: switch-log-retrieval, Property 7
# **Validates: Requirements 3.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(dur=duration_strategy)
def test_parse_since_valid_duration(dur: str):
    """For any valid duration string (positive int + m/h/d), parse_since returns
    a datetime approximately the specified duration before now (within 2s)."""
    before = datetime.now(timezone.utc)
    result = parse_since(dur)
    after = datetime.now(timezone.utc)

    # Extract amount and unit
    amount = int(dur[:-1])
    unit = dur[-1]
    unit_map = {"m": "minutes", "h": "hours", "d": "days"}
    expected_delta = timedelta(**{unit_map[unit]: amount})

    # result should be approximately (now - delta), within 2 seconds tolerance
    expected_low = before - expected_delta - timedelta(seconds=2)
    expected_high = after - expected_delta + timedelta(seconds=2)
    assert expected_low <= result <= expected_high


# ---------------------------------------------------------------------------
# Property 8: Invalid since — invalid strings raise ValueError
# Feature: switch-log-retrieval, Property 8
# **Validates: Requirements 3.3**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(bad_since=invalid_since_strategy)
def test_invalid_since_raises_value_error(bad_since: str):
    """For any string that is neither a valid duration nor ISO 8601,
    parse_since raises ValueError."""
    with pytest.raises(ValueError):
        parse_since(bad_since)


# ---------------------------------------------------------------------------
# Property 9: Module filter — case-insensitive matching
# Feature: switch-log-retrieval, Property 9
# **Validates: Requirements 4.1, 4.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entries=st.lists(log_entry_strategy, min_size=0, max_size=30),
    module=_nonempty_safe_text,
)
def test_module_filter_case_insensitive(entries: list, module: str):
    """For any list of LogEntry objects and module string, filter_by_module
    returns only entries matching case-insensitively, and the result is the
    same regardless of case."""
    result = filter_by_module(entries, module)
    # All returned entries match case-insensitively
    for e in result:
        assert e.module.lower() == module.lower()
    # Case-insensitive: upper and lower produce same results
    result_upper = filter_by_module(entries, module.upper())
    result_lower = filter_by_module(entries, module.lower())
    assert result_upper == result_lower


# ---------------------------------------------------------------------------
# Property 10: Search filter — case-insensitive substring matching
# Feature: switch-log-retrieval, Property 10
# **Validates: Requirements 6.1, 6.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entries=st.lists(log_entry_strategy, min_size=0, max_size=30),
    search=_search_string,
)
def test_search_filter_case_insensitive(entries: list, search: str):
    """For any list of LogEntry objects and search string, filter_by_search
    returns only entries whose message contains the search substring
    case-insensitively."""
    result = filter_by_search(entries, search)
    # All returned entries contain the search substring
    for e in result:
        assert search.lower() in e.message.lower()
    # Case-insensitive: upper and lower produce same results
    result_upper = filter_by_search(entries, search.upper())
    result_lower = filter_by_search(entries, search.lower())
    assert result_upper == result_lower


# ---------------------------------------------------------------------------
# Property 11: Limit — result count <= clamped limit
# Feature: switch-log-retrieval, Property 11
# **Validates: Requirements 5.1, 5.3, 5.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entries=st.lists(log_entry_strategy, min_size=0, max_size=100),
    limit=st.integers(min_value=-10, max_value=2000),
)
def test_limit_clamping_and_enforcement(entries: list, limit: int):
    """For any integer limit and list of LogEntry objects, the result count
    of sort_and_limit is <= clamp_limit(limit), and clamp_limit(0) == 50,
    clamp_limit values are in [1, 1000]."""
    effective = clamp_limit(limit)
    # clamp_limit properties
    assert 1 <= effective <= 1000
    if limit <= 0:
        assert effective == 50
    # sort_and_limit respects the effective limit
    result = sort_and_limit(entries, effective)
    assert len(result) <= effective


# ---------------------------------------------------------------------------
# Property 12: Sort — entries in reverse chronological order
# Feature: switch-log-retrieval, Property 12
# **Validates: Requirements 1.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(entries=st.lists(log_entry_strategy, min_size=0, max_size=50))
def test_sort_reverse_chronological(entries: list):
    """For any list of LogEntry objects, sort_and_limit returns entries in
    reverse chronological order (each timestamp >= next timestamp)."""
    result = sort_and_limit(entries, 1000)
    for i in range(len(result) - 1):
        assert result[i].timestamp >= result[i + 1].timestamp
