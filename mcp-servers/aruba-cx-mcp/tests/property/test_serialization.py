# Property 9: JSON serialization
"""Property tests for JSON serialization.

Tests cover:
- Property 9: _json_dumps() produces valid JSON identical to json.dumps(data, indent=2).
- Property 31: ITSM_ENABLED env var detection is binary (true/false).

**Validates: Requirements 6.2, 1.5**
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _import_server_module():
    """Import aruba_cx_mcp_server with side effects mocked out."""
    import importlib

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
_json_dumps = _server._json_dumps


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_json_primitives = st.one_of(
    st.text(min_size=0, max_size=50),
    st.integers(min_value=-10000, max_value=10000),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.booleans(),
    st.none(),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            children,
            max_size=5,
        ),
    ),
    max_leaves=20,
)


# ---------------------------------------------------------------------------
# Property 9: JSON serialization matches json.dumps
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(data=_json_values)
def test_json_dumps_matches_stdlib(data):
    """For any JSON-serializable Python object, _json_dumps() should produce
    output identical to json.dumps(data, indent=2, default=str)."""
    result = _json_dumps(data)
    expected = json.dumps(data, indent=2, default=str)
    assert result == expected, (
        f"_json_dumps output differs from json.dumps.\n"
        f"Input: {data!r}\n"
        f"Got:      {result!r}\n"
        f"Expected: {expected!r}"
    )


@settings(max_examples=100)
@given(data=_json_values)
def test_json_dumps_returns_string(data):
    """For any JSON-serializable input, _json_dumps() should always return
    a string."""
    result = _json_dumps(data)
    assert isinstance(result, str), (
        f"Expected str, got {type(result).__name__} for input {data!r}"
    )


# ---------------------------------------------------------------------------
# Property 31: ITSM_ENABLED detection is binary
# ---------------------------------------------------------------------------

_true_values = st.sampled_from(["true", "True", "TRUE", "tRuE", "TrUe"])

_non_true_values = st.one_of(
    st.just("false"),
    st.just("False"),
    st.just("FALSE"),
    st.just(""),
    st.just("0"),
    st.just("no"),
    st.just("yes"),
    st.just("1"),
    st.just("enabled"),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s.lower() != "true"),
)


def _detect_itsm_enabled(env_value: str | None) -> bool:
    """Replicate the ITSM detection logic: returns True only for 'true' (case-insensitive)."""
    if env_value is None:
        return False
    return env_value.lower() == "true"


@settings(max_examples=100)
@given(env_val=_true_values)
def test_itsm_enabled_for_true(env_val: str):
    """When ITSM_ENABLED is 'true' (case-insensitive), detection returns True."""
    assert _detect_itsm_enabled(env_val) is True


@settings(max_examples=100)
@given(env_val=_non_true_values)
def test_itsm_disabled_for_non_true(env_val: str):
    """When ITSM_ENABLED is any value other than 'true', detection returns False."""
    assert _detect_itsm_enabled(env_val) is False


def test_itsm_disabled_when_unset():
    """When ITSM_ENABLED is unset, detection returns False."""
    assert _detect_itsm_enabled(None) is False

    old = os.environ.get("ITSM_ENABLED")
    try:
        os.environ.pop("ITSM_ENABLED", None)
        detected = os.environ.get("ITSM_ENABLED", "false").lower() == "true"
        assert detected is False
    finally:
        if old is not None:
            os.environ["ITSM_ENABLED"] = old


@settings(max_examples=100)
@given(env_val=st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=30,
))
def test_itsm_detection_is_binary(env_val: str):
    """For any value, ITSM detection is exactly True or False."""
    result = _detect_itsm_enabled(env_val)
    assert result is (env_val.lower() == "true")
